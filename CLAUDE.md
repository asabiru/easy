# SignalX — Claude / Devin / agent guide

This repo is wired for **gstack-style agent-driven development**. Skills
live under `.agents/skills/<skill>/SKILL.md` and are auto-loaded by
Devin and Claude Code (via gstack).

## Skills available in this repo

| Skill | When to invoke | Where |
| --- | --- | --- |
| `office-hours` | Any new feature idea. Triages it through the rest. | `.agents/skills/office-hours/SKILL.md` |
| `ceo` | Strategic gate — does this serve Track A or Track B? | `.agents/skills/ceo/SKILL.md` |
| `eng-manager` | Phase planning, architecture lock-in. | `.agents/skills/eng-manager/SKILL.md` |
| `designer` | Catches AI slop, enforces visual system on every UI change. | `.agents/skills/designer/SKILL.md` |
| `reviewer` | Multi-pass code review (correctness + security + latency + tests). | `.agents/skills/reviewer/SKILL.md` |
| `qa` | Browser-based end-to-end testing on the live preview URL. | `.agents/skills/qa/SKILL.md` |
| `security` | STRIDE + OWASP audit on sensitive paths. | `.agents/skills/security/SKILL.md` |
| `trading-risk` | Custom: hard-guardrail audit on every autotrade-touching diff. | `.agents/skills/trading-risk/SKILL.md` |
| `compliance` | Regulatory review on custody / advisory / marketing claims. | `.agents/skills/compliance/SKILL.md` |
| `marketing-monetization` | Joint growth + revenue ideation. Always pairs with `ceo`. | `.agents/skills/marketing-monetization/SKILL.md` |
| `release` | Pre-flight, version bump, deploy, rollback plan. | `.agents/skills/release/SKILL.md` |

## Recommended Claude Code setup (for human contributors)

This repo is compatible with [garrytan/gstack](https://github.com/garrytan/gstack).
To install gstack locally:

```bash
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup
```

Then add this section to your local `CLAUDE.md` if it doesn't already
auto-pick the repo skills:

```
# gstack
Use the /browse skill from gstack for all web browsing — never use
mcp__claude-in-chrome__* directly.

# repo-local skills
For SignalX work, prefer the repo-local skills under .agents/skills/
over the generic gstack equivalents — they encode SignalX-specific
guardrails (trading-risk, no-custody, dual-track revenue).
```

## Slash commands (in Claude Code with gstack installed)

```
/office-hours   — start here for any new idea
/plan-ceo-review — strategic ROI gate
/review         — multi-pass code review
/qa             — open a browser, run F1–F6 flows
/security       — STRIDE + OWASP
/release        — ship + monitor
```

## Repo-specific rules

1. **Never break the five hard guardrails** — see `.agents/skills/trading-risk/SKILL.md`.
2. **Never custody client funds** — see `.agents/skills/compliance/SKILL.md`.
3. **Every state-mutating endpoint authenticates and authorizes** —
   see `.agents/skills/security/SKILL.md` and the
   `_own_or_admin` pattern in `signalx/app/api/routes_autotrade.py`.
4. **Every PR runs `pytest -q` locally before push** — current
   baseline 76/76 tests passing.

## Dev quick start

```bash
cd signalx
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

## Live preview

- Backend + landing + panels: https://signalx-mwamxcnp.fly.dev
- PR for current sprint: https://github.com/asabiru/easy/pull/15
