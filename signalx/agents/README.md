# Ruflo Agent Ledgers

Each subdirectory is a long-lived "ledger" for a single SignalX agent role.
Ledgers track that agent's mission, current proposals, decisions, and open
questions. Agents append to their own ledger on every iteration — this gives
the project an auditable history of *why* design choices were made.

```
agents/
  marketing/        # Marketing Agent
  monetization/     # Revenue / Monetization Agent (works with Marketing)
  support/          # Customer support / DX
  compliance/       # Regulatory / risk-disclosure
  growth/           # Distribution, partnerships, virality
  twitter_intel/    # X (Twitter) intel — ingestion, source curation
  anti_fake/        # Fake-news detection, fake_risk model evolution
```

Plus the original 12 build-time agents (PM, Architect, Backend, Database,
News Intel, Market Data, Signal Engine, Telegram, Performance, QA, DevOps,
Security, Docs) live in their own subfolders alongside.

## Ledger format

Each `LEDGER.md` follows this skeleton:

```
# Role

## Mission
What the agent owns.

## Active proposals
Proposals currently up for discussion. Each gets a numeric ID and status.

## Decision log
What was decided, with date + rationale.

## Open questions
Things blocked on user / team input.
```

Ledgers are intentionally text-only artefacts — no automation reads them yet.
They serve as a living spec each agent can reference and evolve.
