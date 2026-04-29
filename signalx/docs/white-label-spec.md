# SignalX White-Label Partner Integration — Spec (Model B)

> **Status:** spec only. Awaiting partner term-sheet before code.

## Why model B

Phase-1 (model A — execution-as-a-service) covers retail clients who
keep funds on their own exchange account. **HNW clients (>$250k tickets)
typically prefer to deposit into a regulated structure** rather than
manage exchange accounts. We satisfy that demand by partnering with an
already-licensed RIA / fund manager / SPC and acting as a **sub-advisor**.

This avoids the 9–18 month, $300k–$1.5M licensing path while still
giving HNW clients the legal structure they expect.

## Partner candidates

| Candidate | Jurisdiction | Structure | Notes |
| --- | --- | --- | --- |
| **Maples Group + Cayman SIBL** | Cayman | Master-feeder fund (open-ended) | Industry-standard for crypto + equities; Maples is the largest fund admin in offshore |
| **IBKR Prime + RIA partner** | US | Sub-advisor under existing RIA | Fast (4–8 weeks); requires a state-RIA partner with crypto expansion approved |
| **BVI Approved Manager** | BVI | Light-touch (no minimum capital) | Cheapest; AUM cap $300M; not US-friendly |
| **Bahamas DIB** | Bahamas | Digital Asset Business Act regulated | Solid for crypto-only; less recognised by US allocators |
| **MAS RFMC (Singapore)** | Singapore | Restricted fund management | Best access to APAC HNW; AUM cap S$250M for retail |

**Default recommendation**: Cayman SPC + Maples for the multi-strategy
parent + IBKR Prime sub-advisor agreement for the perp execution side.
This combination is:
- Recognised by US allocators (Cayman SPC = standard hedge-fund LP form).
- Crypto-friendly (Cayman has explicit guidance for digital assets).
- Compatible with our perp strategy via IBKR's institutional crypto desk.
- Retail-friendly via the SPC's segregated portfolio structure (each
  strategy = its own portfolio, no cross-portfolio liability).

## Revenue split

- **Management fee 2% / Performance fee 20%** charged at the SPC level.
- **Sub-advisor fee 50–70% of net of partner's overhead**:
  - Partner takes management fee 0.50% (admin) + 0.25% (legal) + 30% of perf fee.
  - SignalX retains 1.25% of management + 70% of perf.
- Operating overhead at partner: ~$5k–$15k/mo (admin + audit + KYC).

## Technical integration

The partner does **all** of: KYC/AML, custody, NAV calculation, audit,
investor reporting, redemptions. We provide:

1. **Read-only signal feed** — partner's risk team sees every signal
   we'd act on, with 30-min cooldown so they can veto.
2. **Signed trade-instructions REST API** — `POST /partner/trades`
   sends a directive (`asset`, `side`, `notional`, `time_horizon`)
   signed with our partner-issued private key.
3. **NAV-update hook** — partner pushes daily NAV via webhook so we
   can show it to retail clients in unified dashboards (when applicable).
4. **Compliance disclosures** — every signal shown to retail also
   surfaces "executed by `<Partner Fund Name>`" via the `route_via`
   field.

## Onboarding flow (HNW client)

1. Client clicks "Invest via fund" on `/lend.html` HNW track.
2. Redirected to partner's onboarding portal (KYC + AML + accreditation).
3. Partner approves, opens SPC sub-account, sends LP units.
4. Partner notifies SignalX via webhook → we add the client to the
   `route_via=fund` cohort, surfacing fund-specific reporting in `/app`.
5. Client never sends money to SignalX. Subscription fee model (M1–M3)
   does not apply — partner pays SignalX out of management/perf fees.

## Client-facing disclosures

The website footer + `/app` dashboard must show:

> "SignalX is a sub-advisor to `<Partner Fund Name>` (LICENSE_NUMBER,
> JURISDICTION). SignalX itself does not custody client funds, accept
> deposits, or hold any investment-management licence. The fund and
> its manager are the regulated counterparty. No investment advice is
> being offered; consult the fund offering memorandum for risks and
> terms."

Failure to display this disclosure on every fund-related screen is a
shipping blocker (caught by `.agents/skills/compliance/SKILL.md`
checklist).

## Out-of-scope for v1

- Direct fund of one (managed account per client) — too expensive at
  retail tickets; only available to LPs > $5M.
- US 506(c) listing — adds full SEC accredited-investor verification,
  defer to phase-3.
- ERISA-compliant structures — defer; vast majority of our retail base
  is non-US individual investors.
