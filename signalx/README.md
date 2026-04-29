# SignalX — Stock Futures News Trading System (MVP v0.1)

Event-driven trading signals for **tokenized stock perpetual futures** (NVDAUSDT,
TSLAUSDT, …) listed on crypto exchanges. The system ingests news in real time,
identifies the company and event, scores impact, checks market liquidity, and
emits a `LONG / SHORT / WATCH / SKIP` signal to Telegram + Postgres.

> ⚠️ **MVP v0.1 does NOT execute live trades.** Auto-execution is intentionally
> excluded and gated behind a future module. Use signals manually.

## Quickstart

```bash
cp .env.example .env
docker-compose up --build
```

After everything is healthy:

```bash
# health check
curl http://localhost:8000/health

# example news ingest
curl -X POST http://localhost:8000/news/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "source": "reuters",
    "source_url": "https://example.com/nvda-guidance",
    "raw_text": "Nvidia raises Q2 revenue guidance above Wall Street expectations after strong AI chip demand."
  }'
```

Expected response:

```json
{
  "event_id": 1,
  "signal_id": 1,
  "company": "Nvidia",
  "ticker": "NVDA",
  "symbol": "NVDAUSDT",
  "event_type": "guidance_raised",
  "direction": "bullish",
  "impact_score": 90,
  "confidence": 80,
  "signal_score": 95,
  "action": "LONG",
  "risk_level": "low",
  "reason": "event=guidance_raised; kw=raises guidance,guidance above; 5m_move=0.12"
}
```

If `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set and `TELEGRAM_ENABLED=true`,
the formatted alert is also posted to Telegram.

## Other endpoints

| Method | Path                                   | Purpose                              |
|--------|----------------------------------------|--------------------------------------|
| GET    | `/health`                              | service liveness                     |
| POST   | `/news/ingest`                         | run pipeline on a news payload       |
| GET    | `/signals`                             | list latest signals                  |
| GET    | `/signals/{id}`                        | one signal + result samples          |
| POST   | `/signals/{id}/result/update`          | manually patch result fields         |
| GET    | `/performance/summary`                 | aggregate stats: counts / win rate   |

## Repository layout

```
signalx/
├── app/
│   ├── main.py
│   ├── config/settings.py
│   ├── news/             collector / normalizer / deduplicator / source_reliability
│   ├── companies/        universe + keyword mapper
│   ├── analysis/         classifier / sentiment / impact_score
│   ├── market/           ccxt exchange_client (with mock) / liquidity / spread / funding
│   ├── signals/          risk_engine / signal_engine / formatter
│   ├── notifications/telegram.py
│   ├── database/         SQLAlchemy models + session + migrations/
│   ├── performance/      Celery beat tracker + metrics
│   └── api/              FastAPI routers
├── data/                 companies.json / sources.json / event_rules.json
├── tests/                pytest
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── ARCHITECTURE.md
└── README.md
```

## Local dev (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# point to a local Postgres OR use SQLite for fast iteration
export DATABASE_URL=sqlite:///./signalx.db
export EXCHANGE_USE_MOCK=true
export TELEGRAM_ENABLED=false

uvicorn app.main:app --reload
```

## Tests

```bash
cd signalx
pytest -q
```

Tests use SQLite in-memory and the deterministic mock exchange — no network or
running services required.

## Configuration

All knobs live in `.env` (see `.env.example`):

| Variable                | Purpose                                   |
|-------------------------|-------------------------------------------|
| `DATABASE_URL`          | SQLAlchemy URL                            |
| `REDIS_URL`             | Redis URL                                 |
| `EXCHANGE_ID`           | ccxt exchange id (`binance`, `bybit`, …)  |
| `EXCHANGE_USE_MOCK`     | bypass ccxt; use deterministic mock       |
| `EXCHANGE_API_KEY/SECRET` | ccxt creds (read-only OK in MVP)         |
| `TELEGRAM_BOT_TOKEN`    | Bot token from @BotFather                 |
| `TELEGRAM_CHAT_ID`      | target chat                               |
| `TELEGRAM_ENABLED`      | gate Telegram delivery                    |
| `ENABLE_AUTOTRADE`      | **must be false in v0.1**                 |

## Security

- `.env` is gitignored; only `.env.example` is committed.
- No API keys are referenced in code.
- All collectors / exchange / Telegram failures are caught and logged.
- `ENABLE_AUTOTRADE` defaults to `false` and there is intentionally **no
  order-placement code** in this repo.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for module map and data flow diagrams.

## Multi-agent build (Ruflo workflow)

This MVP was assembled following a 12-role Ruflo agent workflow tracked via
GitHub Issues:
Product Manager · System Architect · Backend · Database · News Intelligence ·
Market Data · Signal Engine · Telegram · Performance · QA · DevOps · Security ·
Documentation. See open/closed issues on this repo for per-agent scope.

## Roadmap

See **Roadmap (post-MVP)** in `ARCHITECTURE.md`.
