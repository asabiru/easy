---
name: qa
description: |
  QA persona for SignalX. Opens a real browser, exercises the live
  preview URL, records video evidence. Trigger after any frontend
  change, after any new endpoint, or before merging to main.
---

# /qa — End-to-end testing on live URL

## Setup

- Live preview URL: https://signalx-mwamxcnp.fly.dev
- Test users (create them per session, do not reuse):
  - admin@qa.signalx.test (set `BOOTSTRAP_ADMIN_EMAIL` env)
  - manager@qa.signalx.test (admin promotes to manager)
  - client@qa.signalx.test
- All assertions are recorded. Use `recording_start` before any flow,
  `annotate_recording` at every checkpoint, `recording_stop` at the
  end and attach the video to the PR.

## Critical golden-path flows (run on every PR)

### F1 — Anonymous landing
1. Visit `/`. See hero, 4 pricing tiers, FAQ, investor form.
2. Click "Get the Auto-Trade Bot" → goes to `/signup.html`.
3. Click "Apply as Investor" → scrolls to investor form.

### F2 — Client registration → subscribe → paper-trade
1. `/login.html`, toggle to "Create account", register
   `client@qa.signalx.test` / `testpass123`.
2. Redirected to `/app.html`. KPI tiles visible. No subscriptions yet.
3. Click "+ New subscription" → `/signup.html`. Pick auto_pro,
   email matches, paste mock API key + secret (≥8 chars).
4. Redirected to `/app.html`. Sub appears with status=paper,
   live=paper, paper_until ~7 days out.
5. Recent orders empty.

### F3 — Client kill switch
1. From `/app.html`, click "Kill" on the sub.
2. Status badge flips to `killed`.
3. Calling `/autotrade/{id}/status` (via DevTools network tab) returns
   `status: "killed"`.

### F4 — Manager — investor lead pipeline
1. From the public site, submit the investor form (track=managed, capital_band=1m_5m).
2. Log in as manager (admin promotes manager@qa.signalx.test).
3. `/manager.html`: lead appears in table.
4. Click row → modal opens. Change status to "qualified". Save.
5. Refresh: status reflects.

### F5 — Admin — system + role promotion + force-kill
1. Log in as admin.
2. `/admin.html` → System tab: counters > 0, ENABLE_AUTOTRADE shows OFF.
3. Users tab: promote a client to manager via dropdown. Audit-log entry visible.
4. Subscriptions tab: force-kill the test client's sub. Audit-log entry visible.

### F6 — Cross-user 403
1. Log in as client A, create sub.
2. Log out, register client B.
3. From DevTools console, call `fetch('/autotrade/{A_sub_id}/kill', {method:'POST'})`.
4. Must return 403.

## Anti-flake rules

- Never use sleep > 5s. If a flow needs longer, add a UI hook
  (`data-testid` attribute) and poll for it.
- Always clear cookies between users.
- Always run F6 — it is the single highest-value security regression
  test in the suite.

## Output

A summary table:

| Flow | Pass / Fail | Notes |
| --- | --- | --- |
| F1 | ✓ | … |

If any flow fails, attach the recording and stop. Do not merge.
