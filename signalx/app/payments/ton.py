"""TON (The Open Network) payment helpers.

Two complementary flows, both run through the same `/payments/ton/*`
endpoints:

1. **Wallet Pay** (https://pay.wallet.tg) — custodial flow inside the
   Telegram @wallet bot. The user has already gone through Wallet Pay's
   KYC. We create an invoice via the merchant API, the user clicks
   `t.me/wallet?startattach=pay-<invoice>` from Telegram, pays, and our
   webhook receives the confirmation. **Best for retail subscriptions.**

2. **TonConnect** (https://ton.org/connect) — self-custody flow with
   Tonkeeper / MyTonWallet / OpenMask. The user signs a transfer
   transaction in their own wallet to our `ton_treasury_address`. We
   verify it landed on-chain by polling TON Center (or accept a
   merchant relay event). **Best for HNW vault deposits**.

Both flows verify webhook signatures via HMAC-SHA256 using
`TON_WALLET_PAY_WEBHOOK_SECRET` (Wallet Pay) or transaction
verification on-chain (TonConnect).

NO funds-touching mainnet calls without:
  * KYC approved (require_kyc on every endpoint)
  * `TON_NETWORK=mainnet` explicitly set (default 'testnet')
  * `TON_TREASURY_ADDRESS` configured
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from app.config.settings import get_settings

log = logging.getLogger(__name__)

# Wallet Pay merchant API base URL.
WALLET_PAY_API_BASE = "https://pay.wallet.tg/wpay/store-api/v1"


@dataclass(frozen=True)
class WalletPayInvoice:
    invoice_id: str
    pay_link: str  # `t.me/wallet?startattach=pay-...`
    amount_usdt: float
    expires_at: int


def make_invoice_payload(
    *,
    user_id: int,
    amount_usdt: float,
    description: str,
    return_url: str | None = None,
) -> dict:
    """Build the JSON for a Wallet Pay POST /order request.

    Reference: https://docs.wallet.tg/pay/api-reference/createOrder
    """
    s = get_settings()
    if amount_usdt <= 0:
        raise ValueError("amount must be positive")
    if not s.ton_wallet_pay_api_key:
        raise RuntimeError("TON_WALLET_PAY_API_KEY not configured")
    return {
        "amount": {
            "currencyCode": "USD",  # Wallet Pay treats USDT-on-TON as USD-pegged
            "amount": f"{amount_usdt:.2f}",
        },
        "description": description[:128],
        "externalId": f"signalx-{user_id}-{int(time.time())}-{secrets.token_hex(4)}",
        "timeoutSeconds": 600,
        "customerTelegramUserId": None,  # we don't have it server-side
        "returnUrl": return_url or "https://signalx-mwamxcnp.fly.dev/app.html",
        "failReturnUrl": return_url or "https://signalx-mwamxcnp.fly.dev/app.html",
    }


def verify_wallet_pay_webhook(headers: dict[str, str], raw_body: bytes) -> None:
    """Validate the Wallet Pay webhook HMAC signature.

    Wallet Pay sends `Walletpay-Signature` (case-insensitive) which is
    base64-encoded HMAC-SHA256 of `<HTTP_METHOD>.<URL>.<TIMESTAMP>.<BODY_BASE64>`
    using `TON_WALLET_PAY_WEBHOOK_SECRET` as the key.

    For the MVP we treat the header as the HMAC-hex of the raw body
    (the simpler scheme exposed by TonConnect-bridge relays). We
    upgrade to the full Wallet Pay scheme once we have an active
    merchant account to test against.
    """
    s = get_settings()
    secret = s.ton_wallet_pay_webhook_secret
    if not secret:
        # No secret configured → fail closed in production.
        # In dev (settings.app_env == 'dev') we let unsigned webhooks
        # through so local testing isn't blocked.
        if s.app_env != "dev":
            raise ValueError("TON webhook: secret not configured")
        return
    sig = headers.get("walletpay-signature") or headers.get("x-ton-signature") or ""
    if not sig:
        raise ValueError("TON webhook: missing signature header")
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig.lower(), expected):
        raise ValueError("TON webhook: invalid signature")


def is_mainnet_ready() -> bool:
    s = get_settings()
    return bool(
        s.ton_network == "mainnet"
        and s.ton_treasury_address
        and s.ton_wallet_pay_api_key
    )
