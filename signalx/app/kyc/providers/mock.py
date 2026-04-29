"""Mock KYC provider for tests + local dev.

Behavior:
  * start_verification → deterministic applicant_id derived from user id,
    fixed 60-min sdk_token.
  * verify_webhook → no signature required (mock).
  * parse_webhook → reads the `result` field directly: 'approved' /
    'rejected' / 'submitted'.

DO NOT use this in any environment where real users self-register —
it's a free pass through KYC."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from app.kyc.providers.base import KycProvider, KycVerdict, StartVerificationResult


class MockProvider(KycProvider):
    name = "mock"

    def start_verification(
        self, user_id: int, user_email: str, level: str = "level1"
    ) -> StartVerificationResult:
        applicant = "mock-" + hashlib.sha256(
            f"{user_id}:{user_email}".encode()
        ).hexdigest()[:24]
        return StartVerificationResult(
            applicant_id=applicant,
            sdk_token=f"mock-token-{applicant}",
            expires_at=int(time.time()) + 3600,
        )

    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> dict[str, Any]:
        # Mock provider trusts payload — but we still parse JSON safely.
        import json
        try:
            return json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"mock webhook: bad JSON: {exc}") from exc

    def parse_webhook(self, payload: dict[str, Any]) -> KycVerdict:
        result = (payload.get("result") or "").lower()
        if result not in {"approved", "rejected", "submitted", "pending_review"}:
            raise ValueError(f"mock provider: unknown result '{result}'")
        return KycVerdict(
            applicant_id=str(payload.get("applicantId", "")),
            status=result,
            reject_reasons=tuple(payload.get("rejectReasons", [])),
            sanctions_hit=bool(payload.get("sanctionsHit", False)),
            pep_hit=bool(payload.get("pepHit", False)),
            sof_required=bool(payload.get("sofRequired", False)),
            raw_event_id=payload.get("eventId"),
            document_country=payload.get("documentCountry"),
        )
