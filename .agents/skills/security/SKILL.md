---
name: security
description: |
  Security review persona for SignalX. STRIDE + OWASP audit on every
  sensitive path. Trigger before any release and after any change to
  auth / autotrade / encryption / admin endpoints.
---

# /security — STRIDE + OWASP audit

## Threat model — assets in priority order

1. **Client API keys** — encrypted with Fernet (AES-128-CBC + HMAC).
   If leaked → attacker can place trades on the client's exchange
   account. Mitigation: trade-only keys + IP whitelist + Fernet at
   rest + key rotation.
2. **JWT signing secret** — issues session cookies. If leaked →
   attacker can forge any user / admin session. Mitigation: env-only
   storage, never committed, rotate on suspicion.
3. **Database** — contains user emails + bcrypt hashes + encrypted
   keys + audit log. Mitigation: TLS-only connection, scoped service
   user, daily backups encrypted at rest.
4. **Admin role** — can promote roles + force-kill subs + view all
   data. Mitigation: bootstrap-only one admin email via env, every
   admin action audit-logged, MFA required (phase 2).

## STRIDE per endpoint

For each endpoint, walk through STRIDE:

| Threat | Question |
| --- | --- |
| Spoofing | Does the endpoint authenticate the caller? |
| Tampering | Are inputs validated (Pydantic)? Is state mutation atomic? |
| Repudiation | Is the action audit-logged with actor + payload? |
| Information disclosure | Are decrypted secrets ever returned in a response? |
| Denial of service | Is there a rate limit or input-size cap? |
| Elevation of privilege | Is the role check correct? `admin || owner`? |

## OWASP Top 10 — explicit checks

- **A01 Broken access control:** every state-mutating endpoint goes
  through `Depends(get_current_user)` + `_own_or_admin`. No exceptions.
- **A02 Cryptographic failures:** API keys encrypted (Fernet),
  passwords hashed (bcrypt rounds=12), TLS enforced on all egress.
- **A03 Injection:** ORM only (no raw SQL), Pydantic validates all
  inputs.
- **A04 Insecure design:** ENABLE_AUTOTRADE global kill switch,
  paper-mode default 7d, daily_loss_limit, max_position_pct.
- **A05 Security misconfiguration:** secrets only via env, no defaults
  in prod (`_DEV_KEY_SEED` only fires when env unset).
- **A07 Auth failures:** session cookies httponly+samesite=lax, JWT
  TTL 12h, no remember-me longer than 30d.
- **A08 Data integrity failures:** audit log is append-only, no DELETE.
- **A09 Logging failures:** every admin / manager action audit-logged.
- **A10 SSRF:** no user-supplied URLs are fetched server-side.

## Red-team scenarios

1. **Anonymous attacker hits `/autotrade/{N}/go-live`** for N=1..1000.
   → Must 401. (Fixed in 43c48be.)
2. **Logged-in client A guesses subscription ID of client B**, calls
   `/kill`. → Must 403. (Fixed in 43c48be.)
3. **Insider with DB access dumps `autotrade_subscriptions.api_key_encrypted`.**
   → Without Fernet key, payload is unreadable. Verify env separation.
4. **Attacker steals session cookie.** → 12h TTL caps damage. Phase 2:
   short-lived access token + refresh token.

## Output

For every finding: `severity:` (info / low / medium / high / critical),
`category:` (STRIDE letter + OWASP code), `path:` (file:line),
`recommendation:` (concrete diff or refactor).
