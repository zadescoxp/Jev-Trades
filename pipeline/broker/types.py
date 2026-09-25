"""Broker dataclasses — no CCXT objects leak past this module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Balance:
    venue: str
    free_usdt: float
    used_usdt: float
    total_usdt: float
    equity: float
    raw_ts: float


@dataclass
class Position:
    symbol: str          # app id, e.g. ETH-USD
    qty: float           # base size
    avg_entry: float
    unrealized_pnl_pct: float
    tp_price: float | None
    sl_price: float | None
    tp_order_id: str | None
    sl_order_id: str | None
    entry_order_id: str | None


@dataclass
class Order:
    id: str
    symbol: str          # app id
    side: str            # buy | sell
    qty: float
    price: float | None  # avg fill if known
    status: str          # open | closed | canceled | rejected | partial
    fee_usdt: float
    raw_type: str
    client_id: str | None


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    base_volume: float
    quote_volume: float


@dataclass
class Book:
    symbol: str
    bid: float
    ask: float
    spread_bps: float
    mid: float
    bid_depth_usdt_l1: float
    ask_depth_usdt_l1: float
    bid_depth_usdt_l10: float
    ask_depth_usdt_l10: float
    book_imbalance: float
