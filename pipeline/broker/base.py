"""Broker Protocol — the interface every broker implementation must satisfy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import Balance, Book, Order, Position, Ticker


@runtime_checkable
class Broker(Protocol):
    name: str  # paper | bybit-testnet | bybit-live

    def fetch_balance(self) -> Balance: ...

    def fetch_position(self, symbol: str) -> Position | None: ...

    def fetch_open_orders(self, symbol: str) -> list[Order]: ...

    def create_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> Order: ...

    def attach_tp_sl(
        self,
        symbol: str,
        entry: Order,
        tp_price: float,
        sl_price: float,
    ) -> tuple[Order | None, Order | None]: ...

    def cancel_open_orders(self, symbol: str) -> None: ...

    def fetch_ticker(self, symbol: str) -> Ticker: ...

    def fetch_order_book(self, symbol: str, limit: int = 25) -> Book: ...

    def flatten(self, symbol: str) -> Order | None:
        """Market-sell entire base position. Used by kill switch and local SL."""
        ...
