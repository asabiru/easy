"""Sumsub KYC provider adapter.

Documentation:
  https://developers.sumsub.com/api-reference/

Why Sumsub for SignalX:
  - 220+ country coverage, FATF Travel Rule built-in.
  - Native sanctions + PEP screening (ComplyAdvantage embedded).
  - Crypto-specific compliance flows (source-of-funds, source-of-wealth).
  - $1.50/applicant in volume tier.

Auth: HMAC-SHA256 of (timestamp + method + path + body) with `secret_key`,
sent via headers `X-App-Token`, `X-App-Access-Sig`, `X-App-Access-Ts`.

Webhook auth: HMAC-SHA256 of raw body with `secret_key`, sent via
`x-payload-digest` header. Constant-time comparison required.

This adapter is **active only when**:
  * `settings.kyc_provider == "sumsub"`
  * `SUMSUB_APP_TOKEN` and `SUMSUB_SECRET_KEY` are set in env.

Otherwise the registry falls back to MockProvider so local dev /
tests / first-time deploy don't need a Sumsub account.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from app.config.settings import get_settings
from app.kyc.providers.base import KycProvider, KycVerdict, StartVerificationResult

log = logging.getLogger(__name__)

API_BASE = "https://api.sumsub.com"
TOKEN_TTL_SECONDS = 600  # 10-minute SDK token


class SumsubAdapter(KycProvider):
    name = "sumsub"

    def __init__(self) -> None:
        s = get_settings()
        if not s.sumsub_app_token or not s.sumsub_secret_key:
            raise RuntimeError(
                "SumsubAdapter requires SUMSUB_APP_TOKEN + SUMSUB_SECRET_KEY"
            )
        self._app_token = s.sumsub_app_token
        self._secret_key = s.sumsub_secret_key.encode("utf-8")
        self._level_name = s.sumsub_level_name or "basic-kyc-level"

    # ------------------------------------------------------------------ #
    # Public interface                                                    #
    # ------------------------------------------------------------------ #
    def start_verification(
        self, user_id: int, user_email: str, level: str = "level1"
    ) -> StartVerificationResult:
        # Sumsub pattern: externalUserId = our user id, applicant created
        # lazily on first SDK init. We just ask Sumsub for an SDK token.
        external_user_id = f"signalx_user_{user_id}"
        path = (
            f"/resources/accessTokens?userId={external_user_id}"
            f"&levelName={self._level_name}&ttlInSecs={TOKEN_TTL_SECONDS}"
        )
        body = b""
        resp = self._signed_post(path, body)
        token = resp.get("token")
        if not token:
            raise RuntimeError(f"sumsub: bad accessTokens response: {resp}")
        return StartVerificationResult(
            applicant_id=external_user_id,
            sdk_token=token,
            expires_at=int(time.time()) + TOKEN_TTL_SECONDS,
        )

    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> dict[str, Any]:
        # Sumsub sends `x-payload-digest` (lowercase) = HMAC-SHA256(body, secret).
        digest = headers.get("x-payload-digest") or headers.get("X-Payload-Digest")
        if not digest:
            raise ValueError("sumsub webhook: missing x-payload-digest")
        expected = hmac.new(self._secret_key, raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, expected):
            raise ValueError("sumsub webhook: invalid signature")
        try:
            return json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"sumsub webhook: bad JSON: {exc}") from exc

    def parse_webhook(self, payload: dict[str, Any]) -> KycVerdict:
        # Sumsub events: applicantReviewed, applicantPending, applicantOnHold,
        # applicantPersonalInfoChanged, applicantWorkflowCompleted, etc.
        event_type = payload.get("type", "")
        review = payload.get("reviewResult") or {}
        review_answer = (review.get("reviewAnswer") or "").upper()
        applicant_id = (
            payload.get("externalUserId")
            or payload.get("applicantId")
            or ""
        )

        if event_type in ("applicantReviewed", "applicantWorkflowCompleted"):
            if review_answer == "GREEN":
                status = "approved"
            elif review_answer == "RED":
                status = "rejected"
            else:
                status = "pending_review"
        elif event_type == "applicantPending":
            status = "submitted"
        elif event_type == "applicantOnHold":
            status = "pending_review"
        else:
            status = "pending_review"

        reject_reasons = tuple(review.get("rejectLabels") or [])
        # Sumsub flags sanctions/PEP via these labels:
        sanctions_hit = bool(
            {"BLOCKLIST", "ADVERSE_MEDIA", "SANCTION"} & set(reject_reasons)
        )
        pep_hit = "PEP" in reject_reasons
        sof_required = "SOURCE_OF_FUNDS_REQUIRED" in reject_reasons

        return KycVerdict(
            applicant_id=str(applicant_id),
            status=status,
            reject_reasons=reject_reasons,
            sanctions_hit=sanctions_hit,
            pep_hit=pep_hit,
            sof_required=sof_required,
            raw_event_id=payload.get("eventId"),
            document_country=(payload.get("info") or {}).get("country"),
        )

    # ------------------------------------------------------------------ #
    # Signing helpers                                                     #
    # ------------------------------------------------------------------ #
    def _sign(self, ts: int, method: str, path: str, body: bytes) -> str:
        msg = f"{ts}{method}{path}".encode("utf-8") + body
        return hmac.new(self._secret_key, msg, hashlib.sha256).hexdigest()

    def _signed_post(self, path: str, body: bytes) -> dict[str, Any]:
        # Lazy import to keep httpx out of the hot path for users who don't
        # need Sumsub.
        import httpx

        ts = int(time.time())
        sig = self._sign(ts, "POST", path, body)
        headers = {
            "X-App-Token": self._app_token,
            "X-App-Access-Sig": sig,
            "X-App-Access-Ts": str(ts),
            "Accept": "application/json",
        }
        try:
            r = httpx.post(
                API_BASE + path,
                content=body,
                headers=headers,
                timeout=10.0,
            )
        except Exception as exc:
            raise RuntimeError(f"sumsub: HTTP error: {exc}") from exc
        if r.status_code >= 400:
            raise RuntimeError(f"sumsub: HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except Exception as exc:
            raise RuntimeError(f"sumsub: bad JSON: {exc}") from exc
