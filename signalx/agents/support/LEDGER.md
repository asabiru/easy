# Support Agent — LEDGER

> Mission: time-to-first-response ≤ 1 hour during business hours, ≤ 6 hours off-hours.
> Channels: Telegram bot, email (`/support/ticket`), Discord VIP-channel.

## S-001 · Ticket intake API
- **Status:** live
- `POST /support/ticket` — fields: email, category, message, optional signal_id.
- `GET  /support/ticket/{id}` — read-back.

## S-002 · Telegram bot command surface
- **Status:** live
- `/help` — usage; `/status` — uptime + signals fired in last hour; `/last` — latest signal; `/about` — model/version.

## S-003 · Auto-trade-specific FAQ
- **Status:** in_progress
- Answer questions: "how do I create a trade-only API key on Bybit?", "what does paper-mode mean?", "how do I cancel?", "how do I switch from paper to live?", "what triggers the daily-loss pause?".

## S-004 · Onboarding email drip *(7 days)*
- **Status:** proposed
- Day 0: "Welcome — you're in paper mode for 7 days."
- Day 2: "Here's a paper trade the bot just took for you."
- Day 5: "How to flip to live (and how the kill-switch protects you)."
- Day 7: "Time to go live? Click here."

## S-005 · Churn-saver flow *(pairs with M-013)*
- **Status:** proposed
- Cancel-intent triggers: offer downgrade tier + 50% off 1 month + 1 free VIP coaching call.

## S-006 · Incident communication template
- **Status:** proposed
- If global kill-switch trips OR if an exchange API outage prevents executions, every active sub gets a Telegram message within 60 seconds explaining the cause + ETA.

## Decision log
- **2024-Q4** → Support is a first-class agent, not a side concern; SLAs are public on the landing.
