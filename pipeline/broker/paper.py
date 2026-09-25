"""PaperBroker — wraps the existing SQLite-backed paper trading layer.

Satisfies the Broker protocol so paper_trader.py can be completely agnostic
about whether it is talking to paper or a live venue.

All prices come from the price argument passed by paper_trader.py (i.e. the
Yahoo Finance last price that is already in use).  Books and tickers return
null / zero fields because paper mode has no venue data source.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .types import Balance, Book, Order, Position, Ticker

try:
    from .. import db as _db
except ImportError:
    import db as _db  # type: ignore[no-redef]


class PaperBroker:
    """Paper execution broker backed by the local SQLite database."""

    name: str = "paper"

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def __init__(self, capital: float = 100_000.0) -> None:
        self._capital = capital
        # current_prices is injected by paper_trader between calls so that
        # equity snapshots work correctly — same as the original code.
        self._current_prices: dict[str, float] = {}

    def set_prices(self, prices: dict[str, float]) -> None:
        """Update the internal mark-price map.  Called by PaperTrader."""
        self._current_prices = prices

    # ------------------------------------------------------------------
    # Broker protocol implementation
    # ------------------------------------------------------------------

    def fetch_balance(self) -> Balance:
        cash = _db.get_cash_balance()
        positions_raw = _db.get_positions()
        equity = cash
        for p in positions_raw:
            sym = p["symbol"]
            tf = p["timeframe"]
            key = sym if tf == "1m" else f"{sym}:{tf}"
            mark = self._current_prices.get(key, self._current_prices.get(sym, p["avg_entry_price"]))
            equity += p["qty"] * mark
        return Balance(
            venue="paper",
            free_usdt=cash,
            used_usdt=0.0,
            total_usdt=cash,
            equity=equity,
            raw_ts=time.time(),
        )

    def fetch_position(self, symbol: str) -> Position | None:
        row = _db.get_position(symbol, "1m")
        if not row or row["qty"] <= 1e-12:
            return None
        entry = row["avg_entry_price"]
        mark = self._current_prices.get(symbol, entry)
        unrealized_pnl_pct = round(((mark - entry) / entry) * 100, 4) if entry > 0 else 0.0
        return Position(
            symbol=symbol,
            qty=row["qty"],
            avg_entry=entry,
            unrealized_pnl_pct=unrealized_pnl_pct,
            tp_price=row.get("tp_price"),
            sl_price=row.get("sl_price"),
            tp_order_id=None,
            sl_order_id=None,
            entry_order_id=None,
        )

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        # Paper mode has no pending open orders; TP/SL are watched locally.
        return []

    def create_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        if price is None or price <= 0:
            raise ValueError(f"PaperBroker.create_entry: price must be positive, got {price}")
        if quantity <= 1e-12:
            raise ValueError(f"PaperBroker.create_entry: quantity too small ({quantity})")

        oid = f"paper-{uuid.uuid4().hex[:12]}"
        _db.execute_trade(
            symbol,
            "1m",
            side,
            quantity,
            price,
            0.0,
            "agent",
            self._current_prices,
        )
        return Order(
            id=oid,
            symbol=symbol,
            side=side,
            qty=quantity,
            price=price,
            status="closed",
            fee_usdt=0.0,
            raw_type="paper_market",
            client_id=None,
        )

    def attach_tp_sl(
        self,
        symbol: str,
        entry: Order,
        tp_price: float,
        sl_price: float,
    ) -> tuple[Order | None, Order | None]:
        """Store TP/SL in the local database.  Paper does not generate separate orders."""
        _db.update_tp_sl_in_db(symbol, "1m", tp_price, sl_price, "jev")
        return None, None  # no separate order objects in paper mode

    def cancel_open_orders(self, symbol: str) -> None:
        # Paper mode has no venue orders to cancel.
        pass

    def fetch_ticker(self, symbol: str) -> Ticker:
        mark = self._current_prices.get(symbol, 0.0)
        return Ticker(
            symbol=symbol,
            bid=mark,
            ask=mark,
            last=mark,
            base_volume=0.0,
            quote_volume=0.0,
        )

    def fetch_order_book(self, symbol: str, limit: int = 25) -> Book:
        mark = self._current_prices.get(symbol, 0.0)
        return Book(
            symbol=symbol,
            bid=mark,
            ask=mark,
            spread_bps=0.0,
            mid=mark,
            bid_depth_usdt_l1=0.0,
            ask_depth_usdt_l1=0.0,
            bid_depth_usdt_l10=0.0,
            ask_depth_usdt_l10=0.0,
            book_imbalance=0.0,
        )

    def flatten(self, symbol: str) -> Order | None:
        """Market-sell the entire base position (used by kill switch / local SL)."""
        row = _db.get_position(symbol, "1m")
        if not row or row["qty"] <= 1e-12:
            return None
        qty = row["qty"]
        price = self._current_prices.get(symbol, row["avg_entry_price"])
        oid = f"paper-flatten-{uuid.uuid4().hex[:12]}"
        _db.execute_trade(
            symbol,
            "1m",
            "sell",
            qty,
            price,
            0.0,
            "flatten",
            self._current_prices,
        )
        return Order(
            id=oid,
            symbol=symbol,
            side="sell",
            qty=qty,
            price=price,
            status="closed",
            fee_usdt=0.0,
            raw_type="paper_flatten",
            client_id=None,
        )

    # ------------------------------------------------------------------
    # Helpers the paper_trader.py old interface still uses directly
    # ------------------------------------------------------------------

    def execute_trade(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Thin shim so legacy code that still calls db.execute_trade works."""
        return _db.execute_trade(*args, **kwargs)
