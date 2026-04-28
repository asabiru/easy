---
name: office-hours
description: |
  Garry-Tan-style "office hours" entry point: the user describes what
  they're trying to ship, and you triage it through the right gstack
  skills, then plan the next 1-2 weeks of work.
---

# /office-hours — Roadmap triage

When the user describes a new idea, run this protocol:

## Step 1 — Restate

In your own words, state what they want to build, in one sentence.
Confirm before proceeding.

## Step 2 — Track classification

Which revenue track does it serve?

- **Track A only** (HNW investors / IB rev-share)
- **Track B only** (retail bot subscribers)
- **Both** (rare, valuable)
- **Neither** (foundational refactor / compliance / DX) — explain why
  it's worth the cost

If neither and unjustified, push back politely.

## Step 3 — Run /plan-ceo-review

Run the CEO skill on the idea. Get the structured ruling
(SHIP / DEFER / REJECT / RESHAPE). Show the user.

## Step 4 — If SHIP, run /eng-manager

Break the work into 1-2 week phases. Each phase shippable. Each phase
flagged. Each phase tested.

## Step 5 — Pre-flight

For each phase, check:
- /security skill applies? (auth / encryption / admin paths)
- /trading-risk skill applies? (autotrade / risk-guard / hot path)
- /compliance skill applies? (custody / advisory / marketing claims)
- /design skill applies? (frontend changes)

If yes to any, that skill must run on the PR before merge.

## Step 6 — Schedule QA

For frontend or end-to-end changes, schedule a /qa run on the live
preview after PR merge.

## Step 7 — Schedule release

Once CI green and QA pass, run /release.

## Output

A structured plan:

```
IDEA: <restated>
TRACK: A | B | both | neither (justified)
CEO RULING: SHIP / DEFER / REJECT / RESHAPE
PHASES:
  1. <name> — <files> — <tests> — <demo URL>
  2. <name> — ...
GATES:
  - /review on every phase
  - /security on phases touching <list>
  - /trading-risk on phases touching <list>
  - /compliance on phases touching <list>
  - /qa after merge of <phase>
  - /release after qa pass
TIMELINE: <weeks>
```
