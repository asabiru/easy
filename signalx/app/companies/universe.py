"""Tradable universe loader."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.config.settings import get_settings


@dataclass(frozen=True)
class Company:
    company: str
    ticker: str
    exchange_symbol: str
    keywords: tuple[str, ...]
    sector: str
    related_tickers: tuple[str, ...]


@lru_cache(maxsize=1)
def load_universe() -> list[Company]:
    path = get_settings().data_dir / "companies.json"
    with path.open("r", encoding="utf-8") as f:
        rows = json.load(f)
    return [
        Company(
            company=r["company"],
            ticker=r["ticker"],
            exchange_symbol=r["exchange_symbol"],
            keywords=tuple(r.get("keywords", [])),
            sector=r.get("sector", ""),
            related_tickers=tuple(r.get("related_tickers", [])),
        )
        for r in rows
    ]


@lru_cache(maxsize=1)
def by_ticker() -> dict[str, Company]:
    return {c.ticker.upper(): c for c in load_universe()}
