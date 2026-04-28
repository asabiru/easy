"""Abstract KYC provider interface.

Every provider (Sumsub, Persona, Onfido, mock) implements:

  * `start_verification(user)` → returns SDK token + applicant id.
    Called by `POST /kyc/start`. The returned token is fed to the
    Web/Mobile SDK that the client renders.

  * `verify_webhook(headers, raw_body)` → cryptographically validates
    the provider's callback. Raises if the signature is bad. Returns
    the parsed payload.

  * `parse_webhook(payload)` → maps provider-specific event JSON to
    our internal `KycVerdict` dataclass.

The router (app.api.routes_kyc) is provider-agnostic — it asks the
configured provider for tokens, accepts webhook → verify → parse →
update KycProfile, and never imports a concrete provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StartVerificationResult:
    applicant_id: str
    sdk_token: str  # WebSDK / mobile token, opaque to us
    expires_at: int  # unix ts, for TTL caching


@dataclass(frozen=True)
class KycVerdict:
    applicant_id: str
    status: str  # "approved" | "rejected" | "pending_review" | "submitted"
    reject_reasons: tuple[str, ...] = ()
    sanctions_hit: bool = False
    pep_hit: bool = False
    sof_required: bool = False
    raw_event_id: str | None = None
    document_country: str | None = None  # ISO-2 from the doc itself


class KycProvider(ABC):
    """Implementations live in app.kyc.providers.<name>."""

    name: str = "base"

    @abstractmethod
    def start_verification(
        self, user_id: int, user_email: str, level: str = "level1"
    ) -> StartVerificationResult: ...

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> dict[str, Any]:
        """Validate provider signature; return parsed JSON or raise."""

    @abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> KycVerdict: ...
