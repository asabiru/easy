---
name: trading-risk
description: |
  Trading-risk audit persona for SignalX. Custom skill (not in stock
  gstack). Verifies every code change preserves the four hard
  guardrails: global kill switch, per-sub feature flag, paper-window,
  daily loss limit, max position cap.
---

# /trading-risk — Hard-guardrail audit

Run this skill whenever the diff touches:

- `app/autotrade/*`
- `app/api/routes_autotrade.py`
- `app/database/models.py` (AutoTradeSubscription, AutoTradeOrder,
  NewsEvent.fake_risk)
- `app/config/settings.py` (any *autotrade*, *enable_*, *kill* flag)

## The five hard guardrails

### G1 — Global kill switch

`Settings.enable_autotrade` defaults to **False**. When False:

- `/autotrade/{id}/go-live` returns 409 with detail "global autotrade
  kill switch is off".
- `_maybe_execute` records every order with `mode="paper"` regardless
  of per-sub flags.

**Audit:** grep for `enable_autotrade` and verify every check is
intact. **A change that bypasses this is a CRITICAL bug.**

### G2 — Per-subscription live flag

`AutoTradeSubscription.live_trading_enabled` is False on creation and
flipped True only by `/go-live`. **Never** auto-flipped by signals or
fills.

### G3 — Paper window

`paper_until` is set to `now + autotrade_default_paper_days` (default
7) on subscribe. `/go-live` is refused while `paper_until > now()`.

### G4 — Daily loss limit

`daily_loss_limit_pct` (default 5%). When `daily_pnl < 0` and
`abs(daily_pnl) >= starting_balance * daily_loss_limit_pct`, the
risk-guard returns `pause_subscription=True` and the order is rejected
with status="rejected".

The starting_balance MUST be `starting_of_day_balance(orders)` — never
the oldest balance in the recent window. (BUG_0003 regression.)

### G5 — Max position cap

`max_position_pct` (default 10%). When proposed_notional >
`free_balance * max_position_pct`, order is rejected with
reason="exceeds max position cap".

## Mandatory checks for any autotrade-touching diff

1. `pytest tests/test_autotrade.py -v` → all 11 pass.
2. The five regression tests must remain:
   - `test_kill_switch_blocks_orders`
   - `test_go_live_refused_when_global_kill_switch_off`
   - `test_risk_guard_rejects_oversized_position`
   - `test_risk_guard_pauses_on_daily_loss`
   - `test_autotrade_endpoints_reject_anonymous`
3. The dispatch path must still query `NewsEvent.fake_risk` via
   `signal.event_id` when `sub.max_fake_risk is not None`.
4. The defaults at `routes_autotrade.py:83-86` must use `is not None`
   ternaries — never `or`.
5. `dispatch_signal_to_subscriptions` is called from `routes_news.py`
   AFTER `Signal` and `NewsEvent` are committed (so `signal.event_id`
   resolves).

## Output

If any check fails: `severity: CRITICAL`, block the merge, link the
exact line, and reference the BUG_id of the original Devin Review
finding. If all pass: `pass: trading-risk audit clean ✓`.
