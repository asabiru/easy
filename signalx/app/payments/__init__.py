"""Payment integrations.

  app.payments.ton           — TON / Telegram Wallet payments. Two flows:
                                  1. Wallet Pay (custodial, KYC by Wallet team)
                                  2. TonConnect (self-custody Tonkeeper)
  app.payments.stripe        — (future) card payments for retail bot subs.

Architecture rule: every successful payment writes a `Payment` row +
an `AmlEvent` (kind=payment_received). KYC must be approved before
any deposit > $0 is accepted (enforced at the endpoint level via
`require_kyc()`). Subscription billing payments are exempt — those
are off-platform from a regulatory standpoint (we just collect
service fees).
"""
