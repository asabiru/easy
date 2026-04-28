# SignalX On-Chain Vault — Specification (Phase-2)

> **Status:** spec only. No mainnet contract. No mainnet deposits.
> All Phase-1 trading runs through the existing **execution-as-a-service**
> path: client funds stay on the user's own exchange account, we
> dispatch orders via API key (trade-only, IP-locked).

This vault is the on-chain alternative for clients who prefer
self-custody to giving us trade-only API keys on a centralized
exchange. It is **NOT** a custody product — clients hold tokenized
shares of the vault in their own wallets and can `withdraw()`
without our involvement.

## 1. Goals

- Clients deposit **USDC** (Solana) or **USDT-on-TON** (TVM) and receive
  vault shares (`vSIG`) at the prevailing NAV.
- A whitelisted **strategy executor PDA** (Solana) or **internal-message
  contract** (TVM) can swap deposited stablecoins into perp / spot
  positions across approved venues but **cannot** withdraw to an
  external address.
- `withdraw(shares)` burns shares and releases USDC pro-rata at the
  current NAV. Queued if the vault holds <80% in liquid form (open
  perp positions must close first; in practice we keep ≥90% liquid).
- Performance fee (default 20% above HWM) and management fee (2% APR)
  accrue continuously and are claimed by `harvest_fees()` to a
  treasury wallet.

## 2. Why this is not custody (legally)

- Clients hold their own `vSIG` tokens — the vault contract is just a
  multi-asset escrow with constrained spend rules. Withdrawal is
  permissionless and irreversible from our side.
- We do not have a private key that can move funds outside the
  whitelisted swap routes. Compromise of the executor key bounds the
  attacker to swapping among already-approved instruments — they
  cannot exfiltrate.
- The strategy is encoded as a **public, deterministic** ruleset (the
  signal pipeline) — clients can reproduce/verify. This is the
  defining feature of a vault product (Maple, Yearn, Aera, Veda) vs.
  a discretionary fund.

## 3. Choice of chain — Solana primary, TON secondary

### Why Solana primary

- **Native perp DEXs** (Drift, Mango v4, Zeta) with deep books on the
  exact stock-symbol perps we trade (NVDA-PERP via xStocks/Backed).
- 400ms blocks → re-balance fee budget low enough for 5–20 swaps/day.
- Anchor framework + standard audit firms (OtterSec, Neodyme, Halborn)
  with proven track records on perp vaults (Drift, Jito, Marinade).
- USDC issued natively (Circle), no bridge risk.

### Why TON secondary

- TVM toolchain (Tact, FunC) is younger; fewer audit firms cover it.
- DeDust / STON.fi cover spot well but **no perp DEX has deep books**
  for tokenized stocks on TON.
- USDT-on-TON has the lowest jetton fees in crypto (~$0.01) — excellent
  for retail buy-and-hold meme baskets.

**Decision**: Solana for the perp strategy. TON used as a
**parallel buy-and-hold meme basket vault** only — different product,
different risk profile, different KYC tier.

## 4. Solana program — Anchor sketch

```text
programs/signalx_vault/src/lib.rs

pub struct Vault {
    pub authority: Pubkey,           // upgrade authority (multisig)
    pub strategy_executor: Pubkey,   // PDA whitelisted to call execute_swap
    pub usdc_vault: Pubkey,          // SPL token account (USDC)
    pub share_mint: Pubkey,          // vSIG mint
    pub treasury: Pubkey,            // perf-fee accrual destination
    pub last_nav: u64,               // micro-USDC per share, updated each harvest
    pub high_water_mark: u64,        // for performance fee
    pub paused: bool,                // emergency kill
    pub deposit_cap_usdc: u64,       // hard cap (start at $1M for safety)
    pub min_deposit_usdc: u64,       // start at $1k
}

#[derive(Accounts)]
pub struct Deposit<'info> { ... }        // signer = depositor
#[derive(Accounts)]
pub struct Withdraw<'info> { ... }       // signer = share holder
#[derive(Accounts)]
pub struct ExecuteSwap<'info> { ... }    // signer = strategy_executor (PDA)
#[derive(Accounts)]
pub struct HarvestFees<'info> { ... }    // signer = authority
#[derive(Accounts)]
pub struct Pause<'info> { ... }          // signer = authority

pub fn deposit(ctx: Context<Deposit>, amount: u64) -> Result<()> { ... }
pub fn withdraw(ctx: Context<Withdraw>, shares: u64) -> Result<()> { ... }
pub fn execute_swap(ctx: Context<ExecuteSwap>,
                    venue: Venue,           // Drift / Mango / Jupiter
                    side: Side,
                    asset: Asset,
                    notional_usdc: u64,
                    min_out: u64) -> Result<()> { ... }
pub fn harvest_fees(ctx: Context<HarvestFees>) -> Result<()> { ... }
pub fn pause(ctx: Context<Pause>) -> Result<()> { ... }
```

### Hard wallet rules (enforced on-chain)

1. `execute_swap` accepts **only** venue programs in a const allow-list.
   Adding a venue requires an authority-signed upgrade.
2. **No transfer instructions** to non-vault-owned accounts in any
   handler — fees go to a vault-owned treasury account, never to an
   external wallet.
3. `notional_usdc` per call **≤ 25%** of `last_nav * total_shares` to
   bound single-trade exposure even if the executor key is compromised.
4. Vault `paused=true` → `deposit/withdraw/execute_swap` all reject.
   Pause is a **signer-required** action (not automated) so it can't
   be triggered by oracle manipulation.

## 5. NAV oracle

NAV is recomputed in `execute_swap` and `harvest_fees` by reading:
- USDC vault balance,
- Drift perp position notional (via Drift program),
- Spot positions (Jupiter quote at swap-out path).

On Solana we read this **synchronously inside the same tx** — no
external oracle. NAV manipulation requires moving prices on multiple
venues simultaneously, which is detectable and bounded by rule (4).

## 6. Off-chain integration

- `app/payments/ton.py` (already built) handles USDT-on-TON deposits
  for Phase-1 SaaS subscriptions.
- `app/payments/solana.py` (Phase-2): `POST /vault/deposit` returns a
  serialized Solana tx (Anchor `deposit` ix); the client signs in
  Phantom/Solflare; we never touch the private key.
- `POST /vault/withdraw`: same pattern, returns the `withdraw` ix.
- Strategy executor service: a separate worker (in `signalx/worker/`,
  Phase-2) holds the `strategy_executor` keypair in a hardware HSM
  (AWS CloudHSM or YubiKey), subscribes to the in-process signal
  pipeline, and constructs `execute_swap` txs. **Never touches
  user-deposited funds outside the rule-bound program**.

## 7. KYC + compliance

- `POST /vault/deposit` requires `require_kyc(enforce=True)` (already
  built — same dependency as `/payments/ton/invoice`).
- Sanctions screening: `KycProfile.sanctions_hit` hard-blocks deposits
  even after admin override.
- Geo-block: deposits refused for users with `country` in
  `app.kyc.geo.OFAC_BLOCKED` or EU/UN list.
- Travel Rule: vault deposits ≥ €1k trigger a Travel Rule API call
  through Sumsub for the originator wallet — refused if counterparty
  KYC info missing.

## 8. Audit + launch sequence

1. Spec freeze (this doc).
2. Anchor program implementation + 90% test coverage (Anchor LocalNet).
3. Internal review checklist from `.agents/skills/security/SKILL.md`
   (OWASP-equivalent for smart contracts: reentrancy, integer overflow,
   PDA confusion, oracle manipulation, upgrade authority misuse).
4. External audit — OtterSec or Neodyme (4–6 weeks, $40k–$80k).
5. Devnet deploy + 30-day live-fire test with team funds only.
6. Mainnet deploy with hard `deposit_cap_usdc` = $250k.
7. Cap raises after 90 days mainnet uptime + zero-incident review.

## 9. Out-of-scope for v1

- Cross-vault rebalancing (each strategy = its own vault).
- Tokenized stocks on TON — premature (daily volume <$50k).
- Insurance fund / nexus — phase-3 once AUM > $5M.
- Voting / governance token — not planned (single-strategy product).
