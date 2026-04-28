# Support Agent

## Mission
Be the first responder for users: bug reports, billing questions, feature
requests, missed signals. Fast, kind, and accurate.

## Active proposals

### S-001 · Ticket intake
Status: implemented (MVP).
- `POST /support/ticket` accepts `email`, `category`, `message`, optional
  `signal_id` reference. Stores in Postgres and forwards to Telegram support
  group.

### S-002 · Telegram bot commands
Status: proposed.
- `/help` — list commands
- `/status` — current pipeline health (DB up, exchange up, Telegram up,
  signals processed last hour)
- `/last [ticker]` — latest 5 signals (optionally filtered by ticker)
- `/about` — version, MVP disclaimer, link to docs

### S-003 · Knowledge base
Status: proposed.
A static `/docs` directory with FAQ, troubleshooting, "what does WATCH mean"
explainers. Auto-deploy on every push.

### S-004 · Auto-FAQ ML responder
Status: parked.
Once we have >1k tickets, fine-tune a small model on resolved tickets to
auto-suggest answers. Not for MVP.

## Decision log
- 2025-04-28: All support tickets are persisted; MVP forwards them via
  Telegram so a human picks up; no auto-reply.

## Open questions
- Where does the "support@" email actually land? Need a real inbox.
