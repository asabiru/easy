# Custody Operator Runbook

This is the day-to-day playbook for the human operator running the
SignalX managed-pool flow (Mode B). Mode A (no-custody execution)
does not need any of this — clients hold their own funds and we
never touch them.

> **Pre-flight gate.** None of these flows work until you have
> set both `CUSTODY_LIVE_DEPOSITS_ENABLED=true` AND either a real
> `CUSTODY_LICENSE_JURISDICTION` + `CUSTODY_LICENSE_NUMBER` pair OR
> an explicit `CUSTODY_SELF_ATTEST_OVERRIDE=true`. Without those,
> every `/wallet/*` endpoint refuses with 503 — by design.

---

## Daily routine (15 minutes)

1. **Check treasury health.**
   ```
   GET /admin/treasury/health
   ```
   If `overall_status` is anything but `ok`, follow the matching
   table below. Targets:

   * `shares_invariant_ok=true` always.
   * `negative_balances=0` always.
   * `latest_nav_age_hours < 24`.
   * `unattributed_count` should trend to 0 same-day.

2. **Reconcile balances.**
   * `GET /admin/treasury/wallets.csv` → sum `current_equity_usdt`.
   * Sum the on-chain treasury balances across TRC20 / ERC20 /
     Solana / BSC / TON addresses you control.
   * Sum exchange balances on whichever venues the pool is trading.
   * Total on-chain + exchange should equal sum of client equity
     within the day's PnL bound. Drift > 0.5% → page yourself.

3. **Snapshot NAV.**
   ```
   POST /admin/treasury/nav/snapshot {"aum_usdt": <reconciled total>}
   ```
   This is what credits performance-fee shares to the treasury wallet
   above each user's high-water mark. Skipping this means clients
   getting "free" PnL until you do snapshot. Run it daily even if AUM
   barely moved.

4. **Drain the pending-deposits queue.**
   ```
   GET /admin/treasury/deposits/pending
   ```
   Each row is either:
   * **unattributed** (sentinel-routed): see "Unattributed deposit"
     below.
   * **below-min**: amount < chain minimum. Either bump the minimum
     down for that user case-by-case, or reject and ask the client
     to top up.

5. **Drain the withdrawal queue.**
   ```
   GET /admin/treasury/withdrawals/queue
   ```
   For each `queued` row:
   * AML-check the destination address on chain.
   * If clean → `POST /admin/treasury/withdrawals/{id}/approve`.
   * Sign & broadcast on-chain from the treasury wallet (multisig if
     enabled).
   * `POST /admin/treasury/withdrawals/{id}/send {tx_hash}`.

---

## Specific scenarios

### Webhook stuck retrying (4xx in our logs)

Provider keeps re-delivering with non-2xx response.

1. `GET /admin/treasury/audit-log?kind=custody_deposit_webhook_signature_invalid&limit=20`.
   If you see entries → secret rotated on the provider side and ours
   is stale. Update `CUSTODY_<chain>_WEBHOOK_SECRET` in fly.io secrets.
2. If no signature errors, check `/payments/<chain>/webhook` route is
   live: the provider may have changed its body schema and the parser
   raised 400. File a `WebhookError` audit row will say which.

### Client says "I deposited but no balance"

1. Ask for the TX hash.
2. `GET /admin/treasury/audit-log?kind=custody_deposit_webhook_credited&limit=200`
   and grep for the hash. Found and `credited=true` → tell client to
   refresh; their wallet sees the shares already.
3. Not in audit → check pending queue:
   `GET /admin/treasury/deposits/pending`. If listed:
   * **below-min**: tell client to top up to chain minimum, then
     reattribute when the next deposit lands.
   * **unattributed**: see below.
4. Not in pending either → webhook never arrived. Check provider
   dashboard for delivery attempts. May need manual credit:
   `POST /admin/treasury/credit-deposit { user_id, chain, tx_hash, amount_usdt }`.

### Unattributed deposit

Sentinel-routed because memo and address didn't match a known user.

1. Verify the TX on the chain explorer (Tronscan / Etherscan / etc.).
2. Cross-reference the source address with anything in your KYC
   records, support tickets, or client outreach.
3. **Only when confident on the user identity:**
   ```
   POST /admin/treasury/deposits/{id}/reattribute
   {
     "user_id": <real user id>,
     "note": "Verified TX on Tronscan; confirmed via support ticket #4471"
   }
   ```
   The note is mandatory (4–512 chars) and lands in the AML audit
   log permanently.

### Suspicious withdrawal in queue

* Destination on a sanctions list, or matches a known mixer / scam
  address.
* `POST /admin/treasury/withdrawals/{id}/cancel`. Provide a reason in
  the optional `reason` body — it goes into the audit log. Shares
  re-credit to the user's wallet automatically.
* If you suspect compromised user account, also disable their login
  and trigger a 2FA reset.

### Stale NAV (`latest_nav_age_hours > 24`)

* New deposits are still issuing shares against the stale price. Not
  fatal, but unfair to either side depending on direction.
* Snapshot now: `POST /admin/treasury/nav/snapshot {aum_usdt}`.

### Negative balance detected (`overall_status=critical`)

* Bug. `GET /admin/treasury/wallets.csv`, find the row(s) with
  negative shares or balance.
* Do NOT process any deposits or withdrawals until resolved — pause
  the master toggle if needed:
  `CUSTODY_LIVE_DEPOSITS_ENABLED=false` (deposits 503 immediately).
* Investigate via audit log filtered to that user_id.

### Suspected webhook secret leak

* Rotate immediately:
  `flyctl secrets set CUSTODY_<chain>_WEBHOOK_SECRET=$(openssl rand -hex 32)`.
* Update the provider's webhook config with the new secret.
* No client impact — the secret is server-side only and we never expose it client-side.
* Audit-log search: any `custody_deposit_webhook_credited` events
  during the suspected leak window — verify each TX on-chain is real.

---

## Compliance / regulator submissions

Quarterly or on-demand request:

1. `GET /admin/treasury/audit-log.csv?limit=5000` for the full window.
2. `GET /admin/treasury/wallets.csv` for current state.
3. Both come pre-formatted (CSV + ISO timestamps + dated filename).
4. PGP-encrypt before sending if the recipient is external.

---

## Escalation

* Negative balance / shares-invariant break → page on-call eng.
* Unrecognized address spike (>5 unattributed in 1h) → possible
  sweep attack. Pause master toggle, investigate.
* Provider outage (no webhooks for >2h) → fall back to manual
  `/admin/treasury/credit-deposit` until provider recovers.

---

## Audit kinds reference

All custody events in `AmlEvent.kind` follow the `custody_*` prefix
so the audit log filter works:

| Kind | When |
|------|------|
| `custody_address_allocated` | client requested deposit address |
| `custody_deposit_credited` | manual credit via admin endpoint |
| `custody_deposit_webhook_credited` | webhook autocredited matched user |
| `custody_deposit_webhook_pending_review` | webhook deposit sentinel-routed or below-min |
| `custody_deposit_reattributed` | operator manual reattribute |
| `custody_withdraw_requested` | client requested withdrawal |
| `custody_withdraw_approved` | operator approved |
| `custody_withdraw_sent` | operator sent on-chain |
| `custody_withdraw_cancelled` | operator cancelled, shares re-credited |
| `custody_nav_snapshot` | NAV recomputed, perf fees accrued |
