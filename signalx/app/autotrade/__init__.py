"""Auto-trade bot subscription module.

Lets paying clients hand the bot a *trade-only* API key (no withdraw, IP
whitelisted) on their own exchange. The bot subscribes to signals coming out
of the news pipeline and places orders against the client's account.

Hard rules — enforced in code:
  * The bot NEVER holds client funds. We only call exchange `create_order`.
  * Live trading requires both:
      a) `Settings.enable_autotrade=True` (global kill switch)
      b) `AutoTradeSubscription.live_trading_enabled=True` (per-client opt-in)
  * New subscriptions start in paper-mode for `autotrade_default_paper_days`.
  * Daily loss limit + max-position guard live in `risk_guard.py`. If either
    trips, the subscription is auto-disabled and a Telegram alert is sent.
"""
