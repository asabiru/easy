# Anti-Fake Agent

## Mission
Keep the fake_risk score honest: high precision (don't flag real news as fake)
and high recall (don't let pump posts through). Evolve the model on real
incidents.

## Active proposals

### AF-001 · Feature set (current)
Status: implemented in `app/analysis/fake_risk.py`.

Inputs and weights (delta vs neutral 30 baseline):

| Signal                                      | Δ score |
| ------------------------------------------- | ------: |
| author tier=official                        | -15     |
| author tier=press / wire                    | -12     |
| verified=True                               | -5      |
| verified=False                              | +5      |
| followers <500                              | +25     |
| followers <5k                               | +12     |
| followers <50k                              | +3      |
| followers ≥50k                              | -3      |
| account_age <30d                            | +20     |
| account_age <180d                           | +8      |
| pump pattern (`100x`, `to the moon`, ...)   | +50     |
| rumor pattern (`BREAKING`, `LEAKED`) without authoritative link from non-press | +18 |
| rumor pattern with authoritative link       | +5      |
| has authoritative link                      | -8      |
| confirmation_count ≥3                       | -25     |
| confirmation_count =2                       | -15     |
| pre-news abs(price_change) ≥2%              | +20     |
| pre-news abs(price_change) ≥1%              | +8      |

Output is clamped to [0, 100]. Risk engine: ≥50 → SKIP, ≥30 → WATCH.

### AF-002 · Pre-news price action
Status: implemented.
We pull `price_change_1m` from real OHLCV (not 24h ticker) and feed it into
fake_risk as `pre_tweet_price_change_pct`. A meaningful move *before* the
news lands suggests insider activity → mark the post itself as suspect.

### AF-003 · Image / link forensics
Status: proposed.
- detect screenshots that look like fabricated press releases (template
  hashing of common Reuters/Bloomberg layouts; if image is "press release"
  shaped but not from a press handle → +10).
- domain reputation lookup on shortened links (any `.ru` / `.tk` /
  `.gen.tr` without an authoritative redirect → +15).

### AF-004 · Account-graph features
Status: parked.
Bot networks tend to share follower overlap; a posts-and-followers graph
analysis could surface coordinated pumps. Out of scope for MVP.

### AF-005 · False-positive feedback loop
Status: proposed.
Add a tiny `POST /support/ticket?category=false_positive` flow so users can
flag mis-classified posts. Anti-Fake reviews these weekly and tunes weights.

## Decision log
- 2025-04-28: Cap confirmation discount to -25 even at high counts, to
  prevent sock-puppet networks from gaming the score by "confirming"
  themselves.
- 2025-04-28: rumor patterns are *contextual* — same wording from a known
  press wire is much less suspect than from an unknown handle.

## Open questions
- Should we expose `fake_risk` in the public Telegram channel or only in
  Pro? Marketing wants it visible (differentiator); Compliance is fine.
