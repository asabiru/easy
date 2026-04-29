"""Per-client deposit-address allocation.

For MVP we don't run hot wallets per-user (operational footgun —
private keys per client × 5 chains = 5N keys to secure). Instead we
use the **shared treasury address + per-client memo / tag** pattern
where the chain supports it (TRON, TON, Solana — all have memo or
SPL-memo fields). For EVM chains (ERC20, BSC) we use a deterministic
counterfactual address derived from the master HD key per (user_id,
chain) — the user sends to a unique contract address that forwards to
the master treasury once a small minimum is collected.

This module exposes the `allocate_address` interface and a
deterministic mock for tests / dev. The real chain integrations live
behind the same interface but read keys from secrets and call out to
the chain provider.
"""
from __future__ import annotations

import hashlib
from typing import Final, Literal

Chain = Literal["trc20", "erc20", "ton", "sol", "bsc"]

SUPPORTED_CHAINS: Final[tuple[Chain, ...]] = ("trc20", "erc20", "ton", "sol", "bsc")

# USDT token identifiers per chain — surfaced to the client UI so
# wallets can validate the asset before sending.
USDT_CONTRACT: Final[dict[Chain, str]] = {
    "trc20": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    "erc20": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "bsc": "0x55d398326f99059fF775485246999027B3197955",
    "sol": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "ton": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
}

# Minimum-credit thresholds. Below this we do NOT credit (gas-cost
# protection — sweeping a $0.50 USDT-ERC20 deposit costs more than the
# deposit). Operator-tunable later via env.
MIN_DEPOSIT_USDT: Final[dict[Chain, float]] = {
    "trc20": 5.0,    # ~$1 fee
    "erc20": 50.0,   # high gas
    "bsc": 5.0,
    "sol": 1.0,
    "ton": 1.0,
}


def _det_address(seed: str, chain: Chain) -> str:
    """Deterministic mock address for tests / dev.

    Real production code calls into the HD-wallet derivation tools per
    chain (e.g. `tronpy` / `web3` / `tonsdk` / `solana-py`) and reads
    `MASTER_TREASURY_XPRIV_*` from secrets. The interface is stable so
    tests don't have to mock any chain libs."""
    h = hashlib.sha256(f"{seed}:{chain}".encode()).hexdigest()
    if chain == "trc20":
        return "T" + h[:33]
    if chain == "erc20" or chain == "bsc":
        return "0x" + h[:40]
    if chain == "sol":
        return h[:43]
    if chain == "ton":
        return "EQ" + h[:46]
    return h[:40]


def allocate_address(user_id: int, chain: Chain) -> dict:
    """Return the address payload the client UI shows to the user.

    `chain` MUST be one of `SUPPORTED_CHAINS`."""
    if chain not in SUPPORTED_CHAINS:
        raise ValueError(f"unsupported chain: {chain}")
    address = _det_address(f"signalx:{user_id}", chain)
    needs_memo = chain in ("trc20", "ton", "sol")
    memo = f"SX-{user_id}" if needs_memo else None
    return {
        "chain": chain,
        "address": address,
        "memo": memo,
        "min_amount_usdt": MIN_DEPOSIT_USDT[chain],
        "asset": "USDT",
        "asset_contract": USDT_CONTRACT[chain],
        "derivation_path": f"m/44'/{_chain_coin_type(chain)}'/0'/0/{user_id}",
    }


def _chain_coin_type(chain: Chain) -> int:
    """BIP-44 coin type for derivation-path documentation."""
    return {
        "trc20": 195,
        "erc20": 60,
        "bsc": 60,
        "sol": 501,
        "ton": 607,
    }[chain]
