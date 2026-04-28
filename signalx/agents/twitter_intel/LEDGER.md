# Twitter Intel Agent

## Mission
Curate the X (Twitter) source list (`data/x_sources.json`), keep ingest
latency low, and surface high-signal posts before they hit traditional wires.

## Active proposals

### T-001 · Tiered handle list
Status: implemented.
`data/x_sources.json` defines four tiers:
- `wire`     — DeItaone, FirstSquawk
- `press`    — Reuters, Bloomberg, FT, WSJ, CNBC, MarketWatch
- `official` — company / regulator official accounts (Tesla, Nvidia,
  ExxonMobil, OPECSecretariat, EIAgov, SECGov, ...)
- `analyst`  — high-signal commentary accounts (unusual_whales, ...)

Tier feeds into source_reliability and fake_risk:
- `wire` / `press` → `is_known_press = True`
- `official` → `is_known_official = True`

### T-002 · Cashtag fan-out
Status: implemented.
For every ticker in the universe we listen for `$TICKER` cashtag mentions
from any verified account. This catches stories that broke on a niche
account before they reached our trusted list.

### T-003 · Operating modes
Status: implemented.
- **Stream** — `app/news/x_collector.py` connects to X v2 filtered stream
  when `X_API_BEARER_TOKEN` is set + `X_STREAM_ENABLED=true`. Forwards each
  match to `POST /news/ingest/x`. Reconnect with exponential backoff.
- **Webhook fallback** — without a token, external integrations
  (Zapier / n8n / IFTTT) post directly to `POST /news/ingest/x`. Same
  pipeline, latency ~5–15s instead of ~2s.

### T-004 · Handle reliability decay
Status: proposed.
If a handle posts >3 fake_risk≥50 messages in 30 days, drop its reliability
by 15. A periodic Celery task could compute this from `news_events` history.

### T-005 · Cross-source confirmation
Status: implemented.
`app/news/cross_source.py` keeps a 90s sliding window per (ticker, event_type)
across all sources. ≥2 distinct sources in the window = corroborated event;
boost confidence and lower fake_risk (handled in `signal_engine.decide`).

## Decision log
- 2025-04-28: Webhook fallback is the production-acceptable mode for MVP
  (per user's call). Stream mode is opt-in once a Bearer token is provided.
- 2025-04-28: We never trust elonmusk for $TSLA-fundamental news in
  isolation — reliability is intentionally capped at 70 to force
  cross-confirmation before LONG/SHORT.

## Open questions
- Should we listen for replies / quote-tweets, or only top-level posts?
  (Top-level only for MVP — reduces noise.)
- Maintain a "blocklist" of known impersonator accounts? (Yes, deferred to
  T-006.)
