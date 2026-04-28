---
name: marketing-monetization
description: |
  Marketing + Monetization joint persona. Proposes campaigns, pricing
  changes, referral programs, and revenue experiments. Always paired
  with the CEO skill for ROI justification.
---

# /marketing-monetization — Growth + revenue ideation

## Operating model

This skill works as a **pair**:
- **Marketing** — proposes how to reach the next 100 customers
- **Monetization** — proposes how to extract more $ per customer

Every proposal lands in a ledger (`signalx/agents/marketing/LEDGER.md`
or `signalx/agents/monetization/LEDGER.md`) with a unique ID
(M-### or MZ-###), an experiment owner, a target metric, and a kill
date if it doesn't hit threshold.

## Track-A revenue stack (priority order, do not reorder)

1. Exchange IB / affiliate rev-share (20–40 bps of investor volume)
2. Performance fee on managed flow (20% of alpha via SMA / sub-account)
3. PFOF / market-maker rebates
4. Co-investment with prop-firms (revenue share on prop-trader P&L)
5. Capital-introduction one-time premiums ($5–50k per HNW intro)
6. Strategy licensing to family offices ($25–250k/year)
7. Tokenized fund / on-chain vault (phase 2, regulatory heavy)
8. Sponsored research notes (transparently labeled, no impact on score)
9. B2B latency-sensitive data feed for HFT desks ($5–20k/mo)

## Track-B subscription tiers (lock these prices in MVP)

| Tier | $/mo | What |
| --- | --- | --- |
| Manual+ | $99 | Telegram signals + 1-click execute |
| Auto-Lite | $249 | Auto, top-3/day, fixed sizing |
| Auto-Pro | $499 | Auto, all signals, adaptive sizing |
| VIP | $999 + 15% perf | Priority + custom risk profile |

## Mandatory experiment fields

Every proposal must include:

```
ID: M-### or MZ-###
Title:
Hypothesis: <if X, then Y, because Z>
Target metric: <CAC, LTV, MRR, sub conversion, etc.>
Threshold: <quantitative bar>
Time-box: <weeks>
Kill criteria: <what makes us stop?>
Owner: <agent / function>
```

## Proposal channels

- New campaign / pricing change → ledger entry + GitHub issue with the
  full hypothesis.
- Code-touching experiment → PR with feature flag.
- Copy-only change → PR to `signalx/site/index.html` (or relevant page).

## Anti-patterns (reject)

- Discounting before product-market fit
- Bundling that obscures unit economics
- Affiliate programs paying out from gross revenue (must be from
  margin only)
- Performance fees without high-water mark
