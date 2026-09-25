"""Bybit public venue feed — no API keys required.

Fetches OHLCV, ticker, and order book from Bybit's public endpoints via CCXT.
Used when MARKET_SOURCE=bybit.  The PaperBroker path is completely unaffected.

Only public endpoints are used here.  Private auth only happens in CcxtSpotBroker.
"""

from __future__ import annotations

import time
from typing import Any

from broker.symbols import APP_TO_CCXT
from broker.types import Book, Ticker

try:
    import ccxt
except ImportError as exc:
    raise ImportError(
        "MARKET_SOURCE=bybit requires ccxt. Run: pip install ccxt"
    ) from exc

# ---------------------------------------------------------------------------
# Module-level singleton exchange object (public, no auth)
# ---------------------------------------------------------------------------

_exchange: ccxt.bybit | None = None


def _get_exchange() -> ccxt.bybit:
    global _exchange
    if _exchange is None:
        _exchange = ccxt.bybit({"enableRateLimit": True})
        _exchange.load_markets()
    return _exchange


# ---------------------------------------------------------------------------
# OHLCV warm-up
# ---------------------------------------------------------------------------

def fetch_ohlcv_bars(app_symbol: str, limit: int = 1000) -> list[dict[str, Any]]:
    """Return up to `limit` completed 1m bars from Bybit for the given app symbol.

    Returns a list of {time, open, high, low, close, volume} dicts compatible
    with the existing bar format used throughout data_collector.py.
    """
    ex = _get_exchange()
    ccxt_sym = APP_TO_CCXT[app_symbol]
    try:
        raw = ex.fetch_ohlcv(ccxt_sym, timeframe="1m", limit=limit)
    except Exception:
        return []

    bars: list[dict[str, Any]] = []
    for candle in raw:
        ts_ms, o, h, l, c, v = candle
        bars.append(
            {
                "time": int(ts_ms // 1000),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }
        )
    # Return all bars except the last (still-open) one
    return bars[:-1] if len(bars) > 1 else bars


def fetch_latest_bar(app_symbol: str) -> dict[str, Any] | None:
    """Return the single most-recent completed 1m bar from Bybit."""
    bars = fetch_ohlcv_bars(app_symbol, limit=2)
    return bars[-1] if bars else None


# ---------------------------------------------------------------------------
# Ticker (cached 200–500 ms)
# ---------------------------------------------------------------------------

_ticker_cache: dict[str, tuple[Ticker, float]] = {}
_TICKER_TTL = 0.4  # seconds


def fetch_ticker(app_symbol: str) -> Ticker | None:
    """Fetch the current ticker for an app symbol, with a 400ms cache."""
    now = time.monotonic()
    cached = _ticker_cache.get(app_symbol)
    if cached and (now - cached[1]) < _TICKER_TTL:
        return cached[0]

    ex = _get_exchange()
    ccxt_sym = APP_TO_CCXT[app_symbol]
    try:
        raw = ex.fetch_ticker(ccxt_sym)
    except Exception:
        return None

    ticker = Ticker(
        symbol=app_symbol,
        bid=float(raw.get("bid") or 0),
        ask=float(raw.get("ask") or 0),
        last=float(raw.get("last") or 0),
        base_volume=float(raw.get("baseVolume") or 0),
        quote_volume=float(raw.get("quoteVolume") or 0),
    )
    _ticker_cache[app_symbol] = (ticker, now)
    return ticker


# ---------------------------------------------------------------------------
# Order book
# ---------------------------------------------------------------------------

_book_cache: dict[str, tuple[Book, float]] = {}
_BOOK_TTL = 3.0  # seconds — fetch every 2–5s per the plan


def fetch_order_book(app_symbol: str, limit: int = 25) -> Book | None:
    """Fetch order book and compute depth / imbalance metrics.  Cached 3s."""
    now = time.monotonic()
    cached = _book_cache.get(app_symbol)
    if cached and (now - cached[1]) < _BOOK_TTL:
        return cached[0]

    ex = _get_exchange()
    ccxt_sym = APP_TO_CCXT[app_symbol]
    try:
        raw = ex.fetch_order_book(ccxt_sym, limit=limit)
    except Exception:
        return None

    bids: list[list[float]] = raw.get("bids", [])
    asks: list[list[float]] = raw.get("asks", [])

    if not bids or not asks:
        return None

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0

    def depth_usdt(levels: list[list[float]], n: int) -> float:
        return sum(float(p) * float(q) for p, q in levels[:n])

    def base_qty(levels: list[list[float]], n: int) -> float:
        return sum(float(q) for _, q in levels[:n])

    bid_l1_usdt = depth_usdt(bids, 1)
    ask_l1_usdt = depth_usdt(asks, 1)
    bid_l10_usdt = depth_usdt(bids, 10)
    ask_l10_usdt = depth_usdt(asks, 10)

    # Imbalance uses base quantity (not USDT) per plan §10
    bid_base_l10 = base_qty(bids, 10)
    ask_base_l10 = base_qty(asks, 10)
    denom = bid_base_l10 + ask_base_l10
    imbalance = (bid_base_l10 - ask_base_l10) / denom if denom > 0 else 0.0

    book = Book(
        symbol=app_symbol,
        bid=best_bid,
        ask=best_ask,
        spread_bps=round(spread_bps, 4),
        mid=round(mid, 8),
        bid_depth_usdt_l1=round(bid_l1_usdt, 2),
        ask_depth_usdt_l1=round(ask_l1_usdt, 2),
        bid_depth_usdt_l10=round(bid_l10_usdt, 2),
        ask_depth_usdt_l10=round(ask_l10_usdt, 2),
        book_imbalance=round(imbalance, 6),
    )
    _book_cache[app_symbol] = (book, now)
    return book


# ---------------------------------------------------------------------------
# Kline streaming (polling loop — replaces yfinance websocket)
# ---------------------------------------------------------------------------

def make_kline_poller(
    add_tick_fn: Any,
    symbols: list[str],
    interval: float = 10.0,
) -> None:
    """Poll Bybit for the latest 1m close price and inject via add_tick_fn.

    This runs in a dedicated daemon thread.  `add_tick_fn(symbol, ts, price, volume)`
    has the same signature as the existing data_collector.add_tick.

    The tick is based on the most-recent completed bar's close so Jev always
    sees confirmed prices, not live partial-bar data (plan §9).
    """
    import threading

    def _poll() -> None:
        while True:
            for sym in symbols:
                try:
                    bar = fetch_latest_bar(sym)
                    if bar:
                        add_tick_fn(sym, float(bar["time"]), float(bar["close"]), float(bar["volume"]))
                except Exception:
                    pass
            time.sleep(interval)

    t = threading.Thread(target=_poll, daemon=True, name="bybit-kline-poller")
    t.start()
