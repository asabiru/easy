"""Exchange client wrapping ccxt. Falls back to deterministic mock data when
EXCHANGE_USE_MOCK=true or when ccxt errors. Never raises into callers."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config.settings import get_settings

log = logging.getLogger(__name__)


@dataclass
class MarketData:
    exchange: str
    symbol: str
    price: float | None
    bid: float | None
    ask: float | None
    spread: float | None
    volume_1m: float | None
    volume_5m: float | None
    price_change_1m: float | None
    price_change_5m: float | None
    funding_rate: float | None
    open_interest: float | None
    error: str | None = None


def _ccxt_symbol(symbol: str) -> str:
    """NVDAUSDT → NVDA/USDT (ccxt unified format). Fallback to as-is."""
    s = symbol.upper()
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}/{quote}"
    return s


def _mock(symbol: str) -> MarketData:
    """Deterministic mock based on symbol hash so tests are stable."""
    seed = sum(ord(c) for c in symbol) % 1000
    base = 100 + seed / 10.0
    bid = round(base - 0.05, 4)
    ask = round(base + 0.05, 4)
    return MarketData(
        exchange="mock",
        symbol=symbol,
        price=round(base, 4),
        bid=bid,
        ask=ask,
        spread=round((ask - bid) / base * 10000, 2),  # bps
        volume_1m=10_000.0 + seed,
        volume_5m=55_000.0 + seed * 5,
        price_change_1m=0.05,
        price_change_5m=0.12,
        funding_rate=0.0001,
        open_interest=1_000_000.0 + seed * 100,
    )


class ExchangeClient:
    """Thin wrapper. Lazy-initialises ccxt; safe to construct without network."""

    def __init__(self) -> None:
        s = get_settings()
        self.exchange_id = s.exchange_id
        self.use_mock = s.exchange_use_mock
        self._ex = None  # ccxt instance, lazy

    def _ensure(self) -> None:
        if self._ex is not None or self.use_mock:
            return
        try:
            import ccxt  # type: ignore[import-untyped]

            klass = getattr(ccxt, self.exchange_id, None)
            if klass is None:
                log.warning("ccxt has no exchange %s; using mock", self.exchange_id)
                self.use_mock = True
                return
            self._ex = klass({"enableRateLimit": True})
        except Exception as exc:  # pragma: no cover
            log.warning("ccxt init failed err=%s; using mock", exc)
            self.use_mock = True

    def fetch_market_data(self, symbol: str) -> MarketData:
        if self.use_mock:
            return _mock(symbol)

        self._ensure()
        if self._ex is None:
            return _mock(symbol)

        unified = _ccxt_symbol(symbol)
        try:
            ticker: dict[str, Any] = self._ex.fetch_ticker(unified)
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            last = ticker.get("last") or ticker.get("close")
            spread_bps = None
            if bid and ask and last:
                spread_bps = round((ask - bid) / last * 10000, 2)

            funding = self._safe_funding(unified)
            oi = self._safe_open_interest(unified)
            ohlcv_metrics = self._safe_ohlcv_metrics(unified)

            return MarketData(
                exchange=self.exchange_id,
                symbol=symbol,
                price=float(last) if last is not None else None,
                bid=float(bid) if bid is not None else None,
                ask=float(ask) if ask is not None else None,
                spread=spread_bps,
                # Short-window metrics derived from 1m OHLCV bars; never the
                # 24h `baseVolume` / `percentage` from ticker (those are wrong
                # for short-horizon risk gates and signal confirmation).
                volume_1m=ohlcv_metrics["volume_1m"],
                volume_5m=ohlcv_metrics["volume_5m"],
                price_change_1m=ohlcv_metrics["price_change_1m"],
                price_change_5m=ohlcv_metrics["price_change_5m"],
                funding_rate=funding,
                open_interest=oi,
            )
        except Exception as exc:
            log.warning("fetch_ticker failed symbol=%s err=%s; falling back to mock", symbol, exc)
            md = _mock(symbol)
            md.error = f"{type(exc).__name__}: {exc}"
            return md

    def _safe_funding(self, unified: str) -> float | None:
        try:
            if hasattr(self._ex, "fetch_funding_rate"):
                fr = self._ex.fetch_funding_rate(unified)  # type: ignore[union-attr]
                return float(fr.get("fundingRate")) if fr.get("fundingRate") is not None else None
        except Exception:
            return None
        return None

    def _safe_open_interest(self, unified: str) -> float | None:
        try:
            if hasattr(self._ex, "fetch_open_interest"):
                oi = self._ex.fetch_open_interest(unified)  # type: ignore[union-attr]
                return float(oi.get("openInterest")) if oi.get("openInterest") is not None else None
        except Exception:
            return None
        return None

    def _safe_ohlcv_metrics(self, unified: str) -> dict[str, float | None]:
        """Fetch the last few 1m bars and derive short-window metrics.

        Returns a dict with keys: price_change_1m, price_change_5m, volume_1m,
        volume_5m. Values are floats or None when data is unavailable.
        """
        empty: dict[str, float | None] = {
            "price_change_1m": None,
            "price_change_5m": None,
            "volume_1m": None,
            "volume_5m": None,
        }
        try:
            if not hasattr(self._ex, "fetch_ohlcv"):
                return empty
            # Pull 6 bars: closed bars [-6..-2], current forming bar at [-1].
            bars = self._ex.fetch_ohlcv(unified, timeframe="1m", limit=6)  # type: ignore[union-attr]
            if not bars or len(bars) < 2:
                return empty
            # Use closed bars only — drop the in-progress final candle.
            closed = bars[:-1] if len(bars) >= 2 else bars
            if len(closed) < 2:
                return empty

            last = closed[-1]
            prev_1m = closed[-2]
            # Each row: [ts, open, high, low, close, volume]
            close_now = float(last[4])
            close_prev_1m = float(prev_1m[4])
            vol_1m = float(last[5])

            price_change_1m = None
            if close_prev_1m:
                price_change_1m = round(
                    (close_now - close_prev_1m) / close_prev_1m * 100.0, 4
                )

            window5 = closed[-5:] if len(closed) >= 5 else closed
            close_5m_ago = float(window5[0][4])
            vol_5m = sum(float(b[5]) for b in window5)
            price_change_5m = None
            if close_5m_ago:
                price_change_5m = round(
                    (close_now - close_5m_ago) / close_5m_ago * 100.0, 4
                )

            return {
                "price_change_1m": price_change_1m,
                "price_change_5m": price_change_5m,
                "volume_1m": vol_1m,
                "volume_5m": vol_5m,
            }
        except Exception as exc:
            log.warning("fetch_ohlcv failed unified=%s err=%s", unified, exc)
            return empty


_singleton: ExchangeClient | None = None


def get_exchange_client() -> ExchangeClient:
    global _singleton
    if _singleton is None:
        _singleton = ExchangeClient()
    return _singleton


def fetch_price_at(symbol: str, when_ts: float | None = None) -> float | None:
    """For performance tracker. MVP uses current price (mocked); real impl
    should use OHLCV at specific timestamps."""
    _ = when_ts
    md = get_exchange_client().fetch_market_data(symbol)
    return md.price


def now_ts() -> float:
    return time.time()
