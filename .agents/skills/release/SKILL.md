---
name: release
description: |
  Release engineer persona for SignalX. Versions, ships, monitors,
  and rolls back. Trigger when ready to merge a PR or cut a tag.
---

# /release — Ship a release

## Pre-flight checklist

- [ ] CI green on PR (all GitHub Actions + Devin Review)
- [ ] Manual QA flows F1–F6 passed (see `/qa` skill)
- [ ] No new high / critical findings from `/security` skill
- [ ] No new findings from `/trading-risk` skill
- [ ] PR description references all relevant LEDGER updates
- [ ] `.env.example` reflects any new env vars
- [ ] README updated if user-facing behavior changed

## Version bump

SignalX uses calver-ish: `0.<sprint>.<hotfix>`. Examples:

- `0.1.0` — initial MVP (current)
- `0.1.1` — post-MVP hotfix
- `0.2.0` — next sprint (multi-source collectors)

Bump version in:
- `signalx/pyproject.toml`
- `signalx/app/main.py` (FastAPI `version=`)

## Changelog

Append to `signalx/CHANGELOG.md` (create if missing):

```
## 0.1.1 — YYYY-MM-DD
### Security
- 🔴 require auth + ownership on autotrade endpoints
### Fixed
- 🟡 admin overview filtered tickets on wrong status value
### Added
- /app, /manager, /admin dashboards
```

## Deploy

1. Merge PR (squash, never merge-commit).
2. CI auto-deploys to https://signalx-mwamxcnp.fly.dev (or run
   `deploy(backend, dir=signalx/)` manually).
3. Smoke-test:
   ```
   curl -fS https://signalx-mwamxcnp.fly.dev/health
   curl -fS https://signalx-mwamxcnp.fly.dev/  # landing
   curl -fS https://signalx-mwamxcnp.fly.dev/login.html
   ```
4. Tag: `git tag v0.1.1 -m "release 0.1.1" && git push origin v0.1.1`.

## Rollback (if smoke-test fails)

1. `flyctl releases -a signalx-mwamxcnp` → find previous version
2. `flyctl releases revert <version> -a signalx-mwamxcnp`
3. Open hotfix PR.

## Post-release

- Monitor logs for 30 min: `deploy(logs)`.
- Watch error rate (target: <0.1%).
- Watch p95 latency on `/news/ingest` (target: <250ms).
