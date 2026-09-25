"""Market feed collector — supports Yahoo Finance (default) and Bybit venue data.

MARKET_SOURCE=yfinance  → yfinance WebSocket (current default)
MARKET_SOURCE=bybit     → Bybit public OHLCV + kline polling (no keys required)

BROKER is independent of MARKET_SOURCE.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd

try:
    from .paper_trader import PaperTrader
    from .broker.risk import is_kill_active, arm_kill_switch
except ImportError:
    from paper_trader import PaperTrader  # type: ignore[no-redef]
    from broker.risk import is_kill_active, arm_kill_switch  # type: ignore[no-redef]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


_load_dotenv(Path(__file__).with_name(".env"))

# -------------------------------------------------------------------
# Env
# -------------------------------------------------------------------
MARKET_SOURCE = os.getenv("MARKET_SOURCE", "yfinance").lower().strip()
BROKER_NAME = os.getenv("BROKER", "paper").lower().strip()

SUPPORTED_SYMBOLS = ("ADA-USD", "XRP-USD", "ETH-USD", "BTC-USD", "SOL-USD", "BNB-USD", "TRX-USD")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
MAX_BARS = 1000
STORE_DIR = Path(__file__).with_name("market_data")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("data_collector")

# Conditionally import yfinance only if needed
if MARKET_SOURCE == "yfinance":
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        yf = None  # type: ignore[assignment]


class MarketState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.selected_symbol = "BTC-USD"
        self.trading_enabled = False
        self.markets: dict[str, dict[str, Any]] = {
            symbol: {"bars": [], "current": None, "last_tick": None, "status": "starting"}
            for symbol in SUPPORTED_SYMBOLS
        }
        self.capital = 100_000.0
        self.max_wallet_position_pct = 0.75
        self.risk_appetite = "balanced"
        self.status = "starting"
        self.active_timeframes = ["1m"]
        self.chart_timeframe = "1m"
        # Liquidity snapshots (Phase 1/2 — populated when MARKET_SOURCE=bybit)
        self.books: dict[str, Any] = {}
        self.tickers: dict[str, Any] = {}

    def configure(
        self,
        symbol: str | None = None,
        capital: float | None = None,
        max_wallet_position_pct: float | None = None,
        risk_appetite: str | None = None,
        trading_enabled: bool | None = None,
    ) -> None:
        with self.lock:
            if symbol in SUPPORTED_SYMBOLS:
                self.selected_symbol = symbol
            if capital is not None:
                self.capital = capital
            if max_wallet_position_pct is not None:
                self.max_wallet_position_pct = max_wallet_position_pct
            if risk_appetite in {"conservative", "balanced", "aggressive"}:
                self.risk_appetite = risk_appetite
            if trading_enabled is not None:
                self.trading_enabled = trading_enabled

    def set_active_timeframes(self, timeframes: list[str]) -> None:
        with self.lock:
            self.active_timeframes = timeframes

    def snapshot(self, symbol: str | None = None, timeframe: str | None = None) -> dict[str, Any]:
        with self.lock:
            selected = symbol if symbol in SUPPORTED_SYMBOLS else self.selected_symbol
            tf = timeframe or self.chart_timeframe or "1m"
            market = self.markets[selected]
            raw_bars = market["bars"][-MAX_BARS:]
            current = market["current"]
            all_raw = raw_bars + ([current] if current else [])
            price = current["close"] if current else (raw_bars[-1]["close"] if raw_bars else None)

            if tf != "1m" and all_raw:
                display_bars = resample_bars(all_raw, tf)
                completed_for_indicators = resample_bars(raw_bars, tf)
            else:
                display_bars = all_raw
                completed_for_indicators = raw_bars

            indicator_series = calculate_indicator_series(completed_for_indicators)
            book = self.books.get(selected)
            ticker = self.tickers.get(selected)

            return {
                "symbol": selected,
                "supported_symbols": SUPPORTED_SYMBOLS,
                "status": market["status"],
                "server_time": int(time.time()),
                "last_tick": market["last_tick"],
                "price": price,
                "bars": display_bars,
                "indicators": calculate_indicators(completed_for_indicators),
                "indicator_series": indicator_series,
                "trading": TRADER.snapshot(price, selected),
                "settings": {
                    "capital": self.capital,
                    "max_wallet_position_pct": self.max_wallet_position_pct,
                    "risk_appetite": self.risk_appetite,
                    "active_timeframes": self.active_timeframes,
                    "chart_timeframe": tf,
                },
                "trading_enabled": self.trading_enabled,
                # Liquidity (null when MARKET_SOURCE=yfinance)
                "book": _book_to_dict(book) if book else None,
                "ticker": _ticker_to_dict(ticker) if ticker else None,
            }


STATE = MarketState()
TRADER = PaperTrader()


def _book_to_dict(book: Any) -> dict[str, Any] | None:
    if book is None:
        return None
    return {
        "bid": book.bid,
        "ask": book.ask,
        "spread_bps": book.spread_bps,
        "mid": book.mid,
        "bid_depth_usdt_l1": book.bid_depth_usdt_l1,
        "ask_depth_usdt_l1": book.ask_depth_usdt_l1,
        "bid_depth_usdt_l10": book.bid_depth_usdt_l10,
        "ask_depth_usdt_l10": book.ask_depth_usdt_l10,
        "book_imbalance": book.book_imbalance,
    }


def _ticker_to_dict(ticker: Any) -> dict[str, Any] | None:
    if ticker is None:
        return None
    return {
        "bid": ticker.bid,
        "ask": ticker.ask,
        "last": ticker.last,
        "base_volume": ticker.base_volume,
        "quote_volume": ticker.quote_volume,
    }


# -------------------------------------------------------------------
# Indicator math (unchanged)
# -------------------------------------------------------------------

def _series(bars: list[dict[str, Any]], key: str) -> pd.Series:
    return pd.Series([bar[key] for bar in bars], dtype="float64")


def _last(value: Any) -> float | None:
    return None if pd.isna(value) else round(float(value), 6)


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _wma(series: pd.Series, period: int) -> pd.Series:
    weights = pd.Series(range(1, period + 1), dtype="float64")
    return series.rolling(period).apply(
        lambda values: float((values * weights.to_numpy()).sum() / weights.sum()), raw=True
    )


def calculate_indicators(bars: list[dict[str, Any]]) -> dict[str, float | None]:
    """Calculate the indicator contract in pipeline/schema.py from completed OHLCV bars."""
    if not bars:
        return {}

    close = _series(bars, "close")
    high = _series(bars, "high")
    low = _series(bars, "low")
    volume = _series(bars, "volume")
    typical = (high + low + close) / 3
    ma: dict[str, pd.Series] = {}
    for period in (10, 20, 30, 50, 100, 200):
        ma[f"ema_{period}"] = close.ewm(span=period, adjust=False, min_periods=period).mean()
        ma[f"sma_{period}"] = close.rolling(period).mean()
    ma["ichimoku_base_line_9_26_52_26"] = (high.rolling(26).max() + low.rolling(26).min()) / 2
    ma["vwma_20"] = (close * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, float("nan"))
    hma_half = _wma(close, 9 // 2)
    hma_full = _wma(close, 9)
    ma["hull_ma_9"] = _wma(2 * hma_half - hma_full, int(9 ** 0.5))

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    average_gain = _wilder(gain, 14)
    average_loss = _wilder(loss, 14)
    rsi = 100 - (100 / (1 + average_gain / average_loss.replace(0, float("nan"))))
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100)
    rsi = rsi.mask((average_gain == 0) & (average_loss > 0), 0)
    trailing_high = high.rolling(14).max()
    trailing_low = low.rolling(14).min()
    raw_stochastic = 100 * (close - trailing_low) / (trailing_high - trailing_low).replace(0, float("nan"))
    stochastic_k = raw_stochastic.rolling(3).mean()
    mean_deviation = typical.rolling(20).apply(
        lambda values: float(abs(values - values.mean()).mean()), raw=True
    )
    cci = (typical - typical.rolling(20).mean()) / (0.015 * mean_deviation)

    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
    atr14 = _wilder(true_range, 14)
    plus_di = 100 * _wilder(plus_dm, 14) / atr14
    minus_di = 100 * _wilder(minus_dm, 14) / atr14
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    adx = _wilder(dx, 14)

    macd = close.ewm(span=12, adjust=False, min_periods=26).mean() - close.ewm(
        span=26, adjust=False, min_periods=26
    ).mean()
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    stoch_rsi = 100 * (rsi - rsi.rolling(14).min()) / (
        rsi.rolling(14).max() - rsi.rolling(14).min()
    ).replace(0, float("nan"))
    stoch_rsi_fast = stoch_rsi.rolling(3).mean()
    williams = -100 * (trailing_high - close) / (trailing_high - trailing_low).replace(0, float("nan"))
    awesome = (high + low).div(2).rolling(5).mean() - (high + low).div(2).rolling(34).mean()
    momentum = close - close.shift(10)
    bull_bear = (high - close.ewm(span=13, adjust=False, min_periods=13).mean()) + (
        low - close.ewm(span=13, adjust=False, min_periods=13).mean()
    )
    true_low = pd.concat([low, close.shift()], axis=1).min(axis=1)
    buying_pressure = close - true_low
    uo = (
        4 * buying_pressure.rolling(7).sum() / true_range.rolling(7).sum()
        + 2 * buying_pressure.rolling(14).sum() / true_range.rolling(14).sum()
        + buying_pressure.rolling(28).sum() / true_range.rolling(28).sum()
    ) / 7 * 100

    # realized_vol_20: std of last 20 completed 1m log returns, raw (not annualized)
    # atr_pct = atr14 / last_close
    log_returns = close.pct_change().add(1).apply(pd.np.log if hasattr(pd, 'np') else __import__('numpy').log)
    realized_vol_20 = _last(log_returns.rolling(20).std().iloc[-1])
    last_close = _last(close.iloc[-1])
    atr_pct = round(float(atr14.iloc[-1]) / float(close.iloc[-1]), 6) if last_close and last_close > 0 else None

    result = {name: _last(values.iloc[-1]) for name, values in ma.items()}
    result.update(
        {
            "relative_strength_index_14": _last(rsi.iloc[-1]),
            "stochastic_percent_k_14_3_3": _last(stochastic_k.iloc[-1]),
            "commodity_channel_index_20": _last(cci.iloc[-1]),
            "average_directional_index_14": _last(adx.iloc[-1]),
            "awesome_oscillator": _last(awesome.iloc[-1]),
            "momentum_10": _last(momentum.iloc[-1]),
            "macd_level_12_26": _last(macd.iloc[-1]),
            "stochastic_rsi_fast_3_3_14_14": _last(stoch_rsi_fast.iloc[-1]),
            "williams_percent_range_14": _last(williams.iloc[-1]),
            "bull_bear_power": _last(bull_bear.iloc[-1]),
            "ultimate_oscillator_7_14_28": _last(uo.iloc[-1]),
            "ema20": _last(ma["ema_20"].iloc[-1]),
            "sma50": _last(ma["sma_50"].iloc[-1]),
            "rsi14": _last(rsi.iloc[-1]),
            "macd": _last(macd.iloc[-1]),
            "signal": _last(macd_signal.iloc[-1]),
            "atr14": _last(atr14.iloc[-1]),
            "atr_pct": atr_pct,
            "realized_vol_20": realized_vol_20,
        }
    )
    return result


def calculate_indicator_series(bars: list[dict[str, Any]]) -> dict[str, list[float | None]]:
    close = _series(bars, "close")
    series: dict[str, pd.Series] = {}
    for period in (10, 20, 30, 50, 100, 200):
        series[f"ema_{period}"] = close.ewm(span=period, adjust=False, min_periods=period).mean()
        series[f"sma_{period}"] = close.rolling(period).mean()
    return {
        name.replace("_", "") if name in ("ema_20", "sma_50") else name: [
            _last(value) for value in values
        ]
        for name, values in series.items()
    }


def resample_bars(bars: list[dict[str, Any]], tf: str) -> list[dict[str, Any]]:
    if not bars or tf == "1m":
        return bars
    df = pd.DataFrame(bars)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("datetime", inplace=True)
    rule = tf.upper().replace("M", "T")
    resampled = df.resample(rule, label="left", closed="left").agg(
        {"time": "first", "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return resampled.to_dict("records")


# -------------------------------------------------------------------
# Tick ingestion
# -------------------------------------------------------------------

def add_tick(symbol: str, timestamp: float, price: float, volume: float | None = None) -> None:
    minute = int(timestamp // 60) * 60
    with STATE.lock:
        market = STATE.markets[symbol]

        if market["current"] is not None and minute < market["current"]["time"]:
            return

        market["last_tick"] = timestamp
        bar_closed = market["current"] is not None and market["current"]["time"] != minute

        if market["current"] is None or bar_closed:
            if market["current"] is not None:
                market["bars"].append(market["current"])
                market["bars"] = market["bars"][-MAX_BARS:]
                append_stored_bar(symbol, market["bars"][-1])

            market["current"] = {
                "time": minute, "open": price, "high": price, "low": price,
                "close": price, "volume": volume or 0,
            }
        else:
            market["current"]["high"] = max(market["current"]["high"], price)
            market["current"]["low"] = min(market["current"]["low"], price)
            market["current"]["close"] = price
            if volume is not None:
                market["current"]["volume"] = max(market["current"]["volume"], volume)
        market["status"] = "live"

    # TP/SL check
    for tf in STATE.active_timeframes:
        TRADER.check_tp_sl(symbol, price, tf)

    # Submit to Jev
    if STATE.trading_enabled and symbol == STATE.selected_symbol:
        for tf in STATE.active_timeframes:
            TRADER.submit(build_agent_state(symbol, tf))


def append_stored_bar(symbol: str, bar: dict[str, Any]) -> None:
    store_path = STORE_DIR / f"{symbol.lower().replace('-', '_')}_1m.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with store_path.open("a", encoding="utf-8") as store:
        store.write(json.dumps(bar, separators=(",", ":")) + "\n")


def read_stored_bars(symbol: str) -> dict[int, dict[str, Any]]:
    store_path = STORE_DIR / f"{symbol.lower().replace('-', '_')}_1m.jsonl"
    if not store_path.exists():
        return {}
    bars: dict[int, dict[str, Any]] = {}
    with store_path.open("r", encoding="utf-8") as store:
        for line in store:
            try:
                bar = json.loads(line)
                bars[int(bar["time"])] = bar
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
    return bars


# -------------------------------------------------------------------
# Agent state builder (Phase 2 additions)
# -------------------------------------------------------------------

def build_agent_state(symbol: str, timeframe: str = "1m") -> dict[str, Any]:
    with STATE.lock:
        market = STATE.markets[symbol]
        completed = market["bars"][-MAX_BARS:]
        current = market["current"]
        bars = completed + ([current] if current else [])

        resampled_completed = resample_bars(completed, timeframe)
        resampled_bars = resample_bars(bars, timeframe)

        indicators = calculate_indicators(resampled_completed)
        price = current["close"] if current else (completed[-1]["close"] if completed else None)
        if price is None:
            return {}
        day_start = int(time.time() // 86400) * 86400
        day_bars = [bar for bar in bars if bar["time"] >= day_start]
        day_high = max((bar["high"] for bar in day_bars), default=price)
        day_low = min((bar["low"] for bar in day_bars), default=price)
        day_open = day_bars[0]["open"] if day_bars else price
        day_volume = sum(bar["volume"] for bar in day_bars)

        # Volume metrics (Phase 2)
        volumes = [bar["volume"] for bar in resampled_completed[-20:]] if resampled_completed else []
        last_bar_volume = resampled_completed[-1]["volume"] if resampled_completed else 0
        volume_sma_20 = (sum(volumes) / len(volumes)) if volumes else None
        volume_ratio = (last_bar_volume / volume_sma_20) if volume_sma_20 and volume_sma_20 > 0 else None

        trading = TRADER.snapshot(price, symbol)["account"]
        position_key = symbol if timeframe == "1m" else f"{symbol}:{timeframe}"
        position = trading["positions"].get(position_key, {})

        # Liquidity fields (null when yfinance)
        book = STATE.books.get(symbol)
        ticker = STATE.tickers.get(symbol)
        ask_price = ticker.ask if ticker else price
        bid_price = ticker.bid if ticker else price

        # Venue info (Phase 2)
        venue_info: dict[str, Any] = {
            "venue": BROKER_NAME,
            "min_qty": None,
            "min_notional": None,
            "tick_size": None,
            "taker_fee_bps": None,
            "last_spread_bps": book.spread_bps if book else None,
        }

        return {
            "symbol": symbol,
            "current_price": price,
            "oscillators": {
                key: value
                for key, value in indicators.items()
                if key not in {
                    "ema20", "sma50", "rsi14", "macd", "signal",
                    "ema_10", "sma_10", "ema_20", "sma_20", "ema_30", "sma_30",
                    "ema_50", "sma_50", "ema_100", "sma_100", "ema_200", "sma_200",
                    "ichimoku_base_line_9_26_52_26", "vwma_20", "hull_ma_9",
                    "atr_pct", "realized_vol_20",
                }
            },
            "moving_averages": {
                key: indicators.get(key)
                for key in (
                    "ema_10", "sma_10", "ema_20", "sma_20", "ema_30", "sma_30",
                    "ema_50", "sma_50", "ema_100", "sma_100", "ema_200", "sma_200",
                    "ichimoku_base_line_9_26_52_26", "vwma_20", "hull_ma_9",
                )
            },
            "position": position.get("position", "None"),
            "quantity": position.get("quantity", 0.0),
            "time_frame": f"{timeframe.replace('m', ' minute').replace('h', ' hour')}",
            "cash_balance": trading["cash_balance"],
            "capital": trading["starting_cash"],
            "equity": trading["equity"],
            "available_cash": trading["available_cash"],
            "position_quantity": position.get("quantity", 0.0),
            "average_entry_price": position.get("average_entry_price"),
            "unrealized_pnl_pct": position.get("unrealized_pnl_pct", 0.0),
            "stop_loss_price": position.get("stop_loss_price"),
            "take_profit_price": position.get("take_profit_price"),
            "stop_loss_pct": position.get("stop_loss_pct"),
            "take_profit_pct": position.get("take_profit_pct"),
            "position_age_bars": 0,
            "max_wallet_position_pct": STATE.max_wallet_position_pct,
            "risk_appetite": STATE.risk_appetite,
            "price": {
                "change_percent": ((price - day_open) / day_open) * 100 if day_open else 0,
                "day_high": day_high,
                "day_low": day_low,
                "open_price": day_open,
                "day_volume": day_volume,
                "atr14": indicators.get("atr14"),
            },
            # Phase 2 additions (null-ok in paper/yfinance mode)
            "liquidity": {
                "bid": bid_price if book else None,
                "ask": ask_price if book else None,
                "spread_bps": book.spread_bps if book else None,
                "mid": book.mid if book else None,
                "bid_depth_usdt_l1": book.bid_depth_usdt_l1 if book else None,
                "ask_depth_usdt_l1": book.ask_depth_usdt_l1 if book else None,
                "bid_depth_usdt_l10": book.bid_depth_usdt_l10 if book else None,
                "ask_depth_usdt_l10": book.ask_depth_usdt_l10 if book else None,
                "book_imbalance": book.book_imbalance if book else None,
            },
            "volume": {
                "last_bar_base": last_bar_volume,
                "last_bar_quote": last_bar_volume * price if price else None,
                "volume_sma_20": volume_sma_20,
                "volume_ratio": volume_ratio,
                "day_volume_quote": day_volume * price if price else None,
            },
            "volatility": {
                "atr14": indicators.get("atr14"),
                "atr_pct": indicators.get("atr_pct"),
                "realized_vol_20": indicators.get("realized_vol_20"),
                "range_pct": round((day_high - day_low) / day_low * 100, 4) if day_low else None,
            },
            "execution": venue_info,
        }


# -------------------------------------------------------------------
# Warm-start
# -------------------------------------------------------------------

def warm_start_yfinance() -> None:
    if yf is None:
        log.warning("yfinance not installed, skipping warm start")
        return
    for symbol in SUPPORTED_SYMBOLS:
        try:
            history = yf.download(symbol, period="1d", interval="1m", progress=False, auto_adjust=False)
        except Exception as exc:
            log.warning("yfinance warm start failed for %s: %s", symbol, exc)
            continue
        stored = read_stored_bars(symbol)
        merged: dict[int, dict[str, Any]] = dict(stored)
        for index, row in history.iterrows():
            def scalar(key: str) -> float:
                value = row[key]
                return float(value.iloc[0] if hasattr(value, "iloc") else value)
            timestamp = int(index.timestamp())
            merged[timestamp] = {
                "time": timestamp,
                "open": scalar("Open"),
                "high": scalar("High"),
                "low": scalar("Low"),
                "close": scalar("Close"),
                "volume": scalar("Volume"),
            }
        if not merged:
            continue
        with STATE.lock:
            rows = [merged[ts] for ts in sorted(merged)[-MAX_BARS:]]
            STATE.markets[symbol]["bars"] = rows[:-1]
            STATE.markets[symbol]["current"] = rows[-1]
            STATE.markets[symbol]["status"] = "history_ready"
    log.info("yfinance warm start complete")


def warm_start_bybit() -> None:
    try:
        from venue_feed import fetch_ohlcv_bars  # type: ignore[import]
    except ImportError:
        try:
            from pipeline.venue_feed import fetch_ohlcv_bars  # type: ignore[import]
        except ImportError:
            log.error("venue_feed not available for bybit warm start")
            return

    for symbol in SUPPORTED_SYMBOLS:
        try:
            bars = fetch_ohlcv_bars(symbol, limit=MAX_BARS + 1)
        except Exception as exc:
            log.warning("Bybit warm start failed for %s: %s", symbol, exc)
            continue
        if not bars:
            continue
        # Verify all 7 symbols resolve via the market map (Phase 1 gate)
        with STATE.lock:
            STATE.markets[symbol]["bars"] = bars[:-1]
            STATE.markets[symbol]["current"] = bars[-1]
            STATE.markets[symbol]["status"] = "history_ready"
    log.info("Bybit warm start complete for %d symbols", len(SUPPORTED_SYMBOLS))


# -------------------------------------------------------------------
# Book/ticker polling thread (Phase 1)
# -------------------------------------------------------------------

def _book_ticker_poller() -> None:
    """Poll Bybit public book + ticker every 3s for the active symbol."""
    try:
        from venue_feed import fetch_order_book, fetch_ticker  # type: ignore[import]
    except ImportError:
        return

    while True:
        sym = STATE.selected_symbol
        try:
            book = fetch_order_book(sym)
            if book:
                with STATE.lock:
                    STATE.books[sym] = book
        except Exception:
            pass
        try:
            ticker = fetch_ticker(sym)
            if ticker:
                with STATE.lock:
                    STATE.tickers[sym] = ticker
        except Exception:
            pass
        time.sleep(3)


# -------------------------------------------------------------------
# Kill switch monitor
# -------------------------------------------------------------------

def _kill_switch_monitor() -> None:
    """Monitor for kill switch activation and flatten all positions."""
    _was_killed = False
    while True:
        if is_kill_active() and not _was_killed:
            _was_killed = True
            log.critical("[kill-switch] ACTIVATED — cancelling orders and flattening positions")
            broker = getattr(TRADER, "broker", None)
            if broker and hasattr(broker, "cancel_open_orders") and hasattr(broker, "flatten"):
                for sym in SUPPORTED_SYMBOLS:
                    try:
                        broker.cancel_open_orders(sym)
                    except Exception as exc:
                        log.error("[kill-switch] cancel_open_orders(%s) failed: %s", sym, exc)
                    try:
                        broker.flatten(sym)
                    except Exception as exc:
                        log.error("[kill-switch] flatten(%s) failed: %s", sym, exc)
            STATE.trading_enabled = False
            log.critical("[kill-switch] All positions flattened. Trading disabled until restart.")
        time.sleep(2)


# -------------------------------------------------------------------
# yfinance websocket worker
# -------------------------------------------------------------------

def receive_tick(message: dict[str, Any]) -> None:
    symbol = message.get("id") or message.get("symbol")
    if symbol not in SUPPORTED_SYMBOLS:
        return
    price = message.get("price")
    timestamp = message.get("time") or message.get("timestamp")
    if price is None or timestamp is None:
        return
    try:
        tick_time = float(timestamp)
        if tick_time > 100_000_000_000:
            tick_time /= 1000
        add_tick(symbol, tick_time, float(price), float(message.get("dayVolume", 0)))
    except (TypeError, ValueError):
        return


def websocket_worker() -> None:
    while True:
        try:
            with yf.WebSocket(verbose=False) as websocket:
                websocket.subscribe(list(SUPPORTED_SYMBOLS))
                websocket.listen(receive_tick)
        except Exception as error:
            STATE.status = f"reconnecting: {type(error).__name__}"
            time.sleep(3)


# -------------------------------------------------------------------
# HTTP server
# -------------------------------------------------------------------

class FeedHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        selected = query.get("symbol", [None])[0]
        path = urlparse(self.path).path

        if path == "/health":
            self.send_json(
                {
                    "ok": True,
                    "status": "live" if any(m["status"] == "live" for m in STATE.markets.values()) else "starting",
                }
            )
            return

        if path == "/config":
            # Expose broker metadata — no secrets
            broker = getattr(TRADER, "broker", None)
            broker_name = getattr(broker, "name", "paper")
            is_sandbox = getattr(broker, "_sandbox", True)
            is_armed = getattr(broker, "_armed", False)
            tp_sl_mode = getattr(broker, "_tp_sl_mode", "local")
            from broker.risk import is_kill_active as _kill, live_symbols  # type: ignore[import]
            self.send_json(
                {
                    "ok": True,
                    "broker": broker_name,
                    "sandbox": is_sandbox,
                    "armed": is_armed,
                    "market_source": MARKET_SOURCE,
                    "live_symbols": live_symbols(),
                    "tp_sl_mode": tp_sl_mode,
                    "kill_switch": _kill(),
                }
            )
            return

        if path == "/history":
            try:
                try:
                    from . import db
                except ImportError:
                    import db  # type: ignore[no-redef]
                trades = db.get_trades(100)
                ledger = db.get_ledger(100)
                self.send_json({"ok": True, "trades": trades, "ledger": ledger})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
            return

        if path != "/stream":
            self.send_error(404)
            return

        timeframe = query.get("timeframe", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(STATE.snapshot(selected, timeframe), separators=(",", ":"))
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/config":
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                STATE.configure(
                    payload.get("symbol"),
                    float(payload["capital"]) if payload.get("capital") is not None else None,
                    float(payload["max_wallet_position_pct"]) if payload.get("max_wallet_position_pct") is not None else None,
                    payload.get("risk_appetite"),
                    bool(payload["trading_enabled"]) if payload.get("trading_enabled") is not None else None,
                )
                if payload.get("active_timeframes"):
                    STATE.set_active_timeframes(payload["active_timeframes"])
                TRADER.configure(
                    STATE.capital,
                    STATE.max_wallet_position_pct,
                    STATE.risk_appetite,
                    payload.get("typesafe_api_key"),
                )
                self.send_json(
                    {
                        "ok": True,
                        "settings": {
                            "symbol": STATE.selected_symbol,
                            "capital": STATE.capital,
                            "max_wallet_position_pct": STATE.max_wallet_position_pct,
                            "risk_appetite": STATE.risk_appetite,
                            "trading_enabled": STATE.trading_enabled,
                            "active_timeframes": STATE.active_timeframes,
                        },
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                self.send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/order":
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                action = payload.get("action")
                symbol = payload.get("symbol") or STATE.selected_symbol
                market_snapshot = STATE.snapshot(symbol)
                current_price = market_snapshot.get("price")
                if not current_price or current_price <= 0:
                    self.send_json({"ok": False, "error": f"No active market price available for {symbol}"}, status=400)
                    return

                if action == "buy":
                    quantity = float(payload["quantity"]) if payload.get("quantity") is not None and float(payload["quantity"]) > 0 else None
                    amount_usd = float(payload["amount_usd"]) if payload.get("amount_usd") is not None and float(payload["amount_usd"]) > 0 else None
                    stop_loss_pct = float(payload["stop_loss_pct"]) if payload.get("stop_loss_pct") is not None and float(payload["stop_loss_pct"]) > 0 else None
                    stop_loss_price = float(payload["stop_loss_price"]) if payload.get("stop_loss_price") is not None and float(payload["stop_loss_price"]) > 0 else None
                    take_profit_pct = float(payload["take_profit_pct"]) if payload.get("take_profit_pct") is not None and float(payload["take_profit_pct"]) > 0 else None
                    take_profit_price = float(payload["take_profit_price"]) if payload.get("take_profit_price") is not None and float(payload["take_profit_price"]) > 0 else None

                    trade = TRADER.manual_buy(
                        symbol=symbol,
                        price=current_price,
                        quantity=quantity,
                        amount_usd=amount_usd,
                        stop_loss_pct=stop_loss_pct,
                        stop_loss_price=stop_loss_price,
                        take_profit_pct=take_profit_pct,
                        take_profit_price=take_profit_price,
                    )
                    self.send_json({"ok": True, "trade": trade, "snapshot": TRADER.snapshot(current_price, symbol)})
                    return

                elif action in ("sell", "exit"):
                    pct = float(payload.get("pct_of_position", 1.0)) if action != "exit" else 1.0
                    quantity = float(payload["quantity"]) if payload.get("quantity") is not None and float(payload["quantity"]) > 0 else None
                    trade = TRADER.manual_sell(symbol=symbol, price=current_price, quantity=quantity, pct_of_position=pct)
                    self.send_json({"ok": True, "trade": trade, "snapshot": TRADER.snapshot(current_price, symbol)})
                    return

                elif action == "update_tp_sl":
                    stop_loss_pct = float(payload["stop_loss_pct"]) if payload.get("stop_loss_pct") is not None else None
                    stop_loss_price = float(payload["stop_loss_price"]) if payload.get("stop_loss_price") is not None else None
                    take_profit_pct = float(payload["take_profit_pct"]) if payload.get("take_profit_pct") is not None else None
                    take_profit_price = float(payload["take_profit_price"]) if payload.get("take_profit_price") is not None else None

                    updated = TRADER.update_tp_sl(
                        symbol=symbol,
                        stop_loss_pct=stop_loss_pct,
                        stop_loss_price=stop_loss_price,
                        take_profit_pct=take_profit_pct,
                        take_profit_price=take_profit_price,
                    )
                    self.send_json({"ok": True, "position": updated, "snapshot": TRADER.snapshot(current_price, symbol)})
                    return

                elif action == "kill_switch":
                    arm_kill_switch("manual_dashboard")
                    self.send_json({"ok": True, "message": "Kill switch armed"})
                    return

                else:
                    self.send_json({"ok": False, "error": f"Unsupported order action '{action}'"}, status=400)
                    return
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
            return

        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        return


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting data_collector MARKET_SOURCE=%s BROKER=%s", MARKET_SOURCE, BROKER_NAME)

    # Warm start
    if MARKET_SOURCE == "bybit":
        warm_start_bybit()
        # Start book/ticker poller
        threading.Thread(target=_book_ticker_poller, daemon=True, name="bybit-book-ticker").start()
        # Start kline poller
        try:
            from venue_feed import make_kline_poller  # type: ignore[import]
            make_kline_poller(add_tick, list(SUPPORTED_SYMBOLS), interval=10)
        except ImportError:
            log.warning("venue_feed not available — kline polling disabled")
    else:
        warm_start_yfinance()
        if yf is not None:
            threading.Thread(target=websocket_worker, daemon=True, name="yfinance-ws").start()

    # Kill switch monitor (always active)
    threading.Thread(target=_kill_switch_monitor, daemon=True, name="kill-switch-monitor").start()

    log.info("Market feed listening on http://%s:%d", HOST, PORT)
    ThreadingHTTPServer((HOST, PORT), FeedHandler).serve_forever()
