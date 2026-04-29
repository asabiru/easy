---
name: eng-manager
description: |
  Engineering manager persona for SignalX. Locks architecture,
  enforces conventions, breaks features into shippable phases.
  Trigger on any work that touches >3 files or introduces a new
  module / pattern.
---

# /eng-manager — Architecture lock-in

## Architecture invariants (do not break)

1. **Single source of truth for risk:** `app/autotrade/risk_guard.py`.
   No scattered `if balance < ...` in route handlers.
2. **Single dispatcher:** `app/autotrade/executor.py:dispatch_signal_to_subscriptions`
   — every order goes through it. No alternate code path.
3. **Encryption boundary:** `app/autotrade/crypto.py:encrypt|decrypt`
   only. Routes never touch Fernet directly.
4. **Auth boundary:** `app.auth.deps.get_current_user` /
   `require_role`. Routes never decode JWT directly.
5. **Database:** SQLAlchemy ORM only — never raw SQL. Migrations via
   Alembic when schema changes (defer until phase 2; for MVP
   `init_db()` recreates tables in dev).
6. **Audit:** every admin / manager mutation writes to `AuditLog`.
7. **Background work:** Celery tasks for anything > 100ms. No long
   blocking calls in route handlers.
8. **News pipeline:** ingest → classify → score → emit. Each stage
   is a pure function in `app/analysis/`. No cross-stage state.

## Convention checks (lint-able)

- Modules ≤ 400 lines. If longer → split.
- Functions ≤ 60 lines. If longer → extract helpers.
- Pydantic models for every request body. No raw dicts.
- Type hints on every function signature.
- One feature per PR; never bundle unrelated work.

## Phase planning template

For any feature > 1 day of work, produce:

```
PHASE 1 — <skeleton, behind feature flag>
  - Files: …
  - Tests: …
  - Demo URL: …
  - Risk: low — flag default off

PHASE 2 — <integration with hot path>
  - Files: …
  - Tests: …
  - Risk: medium — flag default on for staging

PHASE 3 — <cleanup, docs, prod flag-flip>
```

Each phase is its own PR.

## Anti-patterns to reject

- "Let me refactor the whole thing while I'm here" → reject.
  Refactor in a separate PR.
- "I'll skip the test, will add it later" → reject. Tests ship with
  the feature.
- "I'll bypass the risk-guard for this special case" → reject.
  Special cases get their own gate that fails closed.
- "I'll add Any type-hint to silence mypy" → reject. Use real types.
