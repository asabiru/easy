"""Risk acknowledgement gate.

Source-of-truth for the disclosure version a user must accept before
any state-mutating compliance gate (currently /autotrade/subscribe and
/autotrade/{id}/go-live). Bump RISK_ACK_VERSION whenever
site/legal/disclosures.html changes materially — clients are then
re-prompted before the next gated call.

The gate itself is implemented in routes_compliance.py (POST
/compliance/risk-ack) and consumed by routes_autotrade.py via
`require_risk_ack(user)`.
"""
from __future__ import annotations

import os

from fastapi import HTTPException

from app.database.models import User


# Version 1: initial release of /legal/disclosures.html.
# Version 2: custody / managed-pool section added (Mode B). Clients
# who only ack'd v1 (execution-only) MUST re-acknowledge before any
# /wallet/* endpoint succeeds — the disclosures changed materially:
# we now describe holding their USDT, NAV pricing, withdrawal queueing,
# performance + management fees, and the licence-attestation gate.
# Bump on material change (new section, OFAC list update, etc.).
RISK_ACK_VERSION: int = 2


def _gate_enabled() -> bool:
    """Master toggle. Defaults to enabled; tests set
    COMPLIANCE_RISK_ACK_REQUIRED=false in conftest.py so the existing
    autotrade flow tests don't all need to POST /compliance/risk-ack
    first. Production deployments leave this unset (== True)."""
    return os.environ.get("COMPLIANCE_RISK_ACK_REQUIRED", "true").lower() != "false"


def require_risk_ack(user: User) -> None:
    """Raise 412 Precondition Failed if the user has not accepted the
    current risk-acknowledgement version.

    Admin and manager roles bypass — they're acting on behalf of clients
    via the Compliance dashboard and have separately acknowledged the
    operator runbook."""
    if not _gate_enabled():
        return
    if user.role in ("admin", "manager"):
        return
    if (user.risk_ack_version or 0) >= RISK_ACK_VERSION:
        return
    raise HTTPException(
        status_code=412,
        detail=(
            "risk acknowledgement required: "
            f"POST /compliance/risk-ack with version={RISK_ACK_VERSION}"
        ),
    )
