"""CcxtSpotBroker — Bybit spot execution via the CCXT unified API.

Supports:
  - sandbox=True  → Bybit testnet (set_sandbox_mode)
  - sandbox=False + armed=True → Bybit mainnet

Never call this with sandbox=False unless you are intentionally trading real funds.

Key invariants enforced here:
  - enableRateLimit=True always
  - load_markets() once at startup
  - amount_to_precision + below-min guard on every create_entry
  - No CCXT objects leak past this module's public API
  - Keys come from constructor args only; never from env inside this class
  - Kill switch checked before every create_entry
  - On 10003 (key/host mismatch): crash startup with clear message
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace as dc_replace
from typing import Any

from .symbols import to_ccxt, to_app
from .types import Balance, Book, Order, Position, Ticker
from . import risk as _risk

try:
    import ccxt
except ImportError as exc:
    raise ImportError(
        "CcxtSpotBroker requires ccxt. Run: pip install ccxt"
    ) from exc

log = logging.getLogger(__name__)


class CcxtSpotBroker:
    """Bybit spot execution broker via CCXT unified API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        armed: bool = False,
    ) -> None:
        self._sandbox = sandbox
        self._armed = armed

        mode = "testnet" if sandbox else "mainnet"
        self.name = f"bybit-{'testnet' if sandbox else 'live'}"

        # ----------------------------------------------------------------
        # Phase 4 mainnet startup guard
        # ----------------------------------------------------------------
        if not sandbox and not armed:
            # Boot is allowed — create_entry will block each call.
            log.warning(
                "[broker] BROKER=bybit BROKER_SANDBOX=0 LIVE_ARMED=0 — "
                "broker is booted but will refuse every order (blocked_unarmed). "
                "Set LIVE_ARMED=1 to arm mainnet trading."
            )

        if not sandbox:
            missing = []
            if not os.getenv("LIVE_MAX_NOTIONAL_USDT", "").strip():
                missing.append("LIVE_MAX_NOTIONAL_USDT")
            if not os.getenv("LIVE_DAILY_LOSS_USDT", "").strip():
                missing.append("LIVE_DAILY_LOSS_USDT")
            if not os.getenv("LIVE_SYMBOLS", "").strip():
                missing.append("LIVE_SYMBOLS")
            if missing and armed:
                raise SystemExit(
                    f"[broker] Cannot arm mainnet — missing required env vars: "
                    f"{', '.join(missing)}\n"
                    f"Set them in pipeline/.env before setting LIVE_ARMED=1."
                )

        # ----------------------------------------------------------------
        # Build exchange
        # ----------------------------------------------------------------
        self._ex = ccxt.bybit(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                },
            }
        )

        if sandbox:
            self._ex.set_sandbox_mode(True)

        # Warm up markets once
        try:
            self._ex.load_markets()
        except ccxt.AuthenticationError as exc:
            raise SystemExit(
                f"[broker] Authentication failed on startup ({mode}). "
                f"Check your API key, secret, and that testnet keys are used "
                f"with BROKER_SANDBOX=1 and mainnet keys with BROKER_SANDBOX=0.\n"
                f"Bybit error 10003 means key/host mismatch.\n"
                f"Original: {exc}"
            ) from exc

        # Balance cache
        self._balance_cache: tuple[Balance, float] | None = None
        self._BALANCE_TTL = 3.0

        # TP/SL mode (will be determined on first attach_tp_sl call)
        self._tp_sl_mode: str = "unknown"

        # Set session start equity for daily loss tracking
        try:
            bal = self.fetch_balance()
            _risk.set_session_start_equity(bal.equity)
        except Exception:
            pass

        log.info("[broker] %s ready (sandbox=%s armed=%s)", self.name, sandbox, armed)

    @property
    def tp_sl_mode(self) -> str:
        return self._tp_sl_mode

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ccxt_sym(self, app_symbol: str) -> str:
        return to_ccxt(app_symbol)

    def _client_order_id(self, symbol: str, side: str) -> str:
        ts = int(time.time())
        safe = symbol.replace("-", "").lower()
        return f"jev-{safe}-{side}-{ts}"

    def _handle_ccxt_error(self, exc: Exception, context: str) -> None:
        log.error("[broker] %s error: %s: %s", context, type(exc).__name__, exc)

    def _parse_order(self, raw: dict[str, Any], app_symbol: str) -> Order:
        status_map = {
            "open": "open",
            "closed": "closed",
            "canceled": "canceled",
            "cancelled": "canceled",
            "rejected": "rejected",
            "partial": "partial",
            "partially_filled": "partial",
        }
        raw_status = str(raw.get("status", "")).lower()
        status = status_map.get(raw_status, raw_status)

        fee_info = raw.get("fee") or {}
        fee_usdt = float(fee_info.get("cost") or 0)

        return Order(
            id=str(raw.get("id", "")),
            symbol=app_symbol,
            side=str(raw.get("side", "")).lower(),
            qty=float(raw.get("filled") or raw.get("amount") or 0),
            price=float(raw["average"]) if raw.get("average") else (float(raw["price"]) if raw.get("price") else None),
            status=status,
            fee_usdt=fee_usdt,
            raw_type=str(raw.get("type", "")),
            client_id=str(raw.get("clientOrderId") or ""),
        )

    # ------------------------------------------------------------------
    # Kill-switch + armed checks
    # ------------------------------------------------------------------

    def _check_pre_order(self, symbol: str) -> str | None:
        """Return a block reason string if the order should be rejected, else None."""
        if _risk.is_kill_active():
            return "kill_switch_active"
        if not self._armed and not self._sandbox:
            return "blocked_unarmed"
        # Phase 4: symbol gate
        allowed = _risk.live_symbols()
        if allowed and symbol not in allowed:
            return f"symbol_not_in_live_symbols ({symbol} not in {allowed})"
        return None

    # ------------------------------------------------------------------
    # Broker protocol
    # ------------------------------------------------------------------

    def fetch_balance(self) -> Balance:
        now = time.time()
        if self._balance_cache and (now - self._balance_cache[1]) < self._BALANCE_TTL:
            return self._balance_cache[0]

        try:
            raw = self._ex.fetch_balance()
            usdt = raw.get("USDT", {})
            free = float(usdt.get("free") or 0)
            used = float(usdt.get("used") or 0)
            total = float(usdt.get("total") or 0)
            # Equity = USDT total + value of all non-USDT spot holdings
            equity = total
            for asset, info in raw.items():
                if asset in {"USDT", "info", "timestamp", "datetime", "free", "used", "total"}:
                    continue
                if not isinstance(info, dict):
                    continue
                qty = float(info.get("total") or 0)
                if qty <= 0:
                    continue
                # Try to get a price estimate
                try:
                    ccxt_sym = f"{asset}/USDT"
                    if ccxt_sym in self._ex.markets:
                        t = self._ex.fetch_ticker(ccxt_sym)
                        equity += qty * float(t.get("last") or 0)
                except Exception:
                    pass
            bal = Balance(
                venue=self.name,
                free_usdt=free,
                used_usdt=used,
                total_usdt=total,
                equity=equity,
                raw_ts=now,
            )
            self._balance_cache = (bal, now)
            return bal
        except ccxt.NetworkError as exc:
            self._handle_ccxt_error(exc, "fetch_balance")
            raise
        except ccxt.RateLimitExceeded as exc:
            self._handle_ccxt_error(exc, "fetch_balance (429)")
            raise

    def fetch_position(self, symbol: str) -> Position | None:
        """Reconstruct a position from the spot balance for the base asset."""
        try:
            raw = self._ex.fetch_balance()
            ccxt_sym = self._ccxt_sym(symbol)
            base = ccxt_sym.split("/")[0]
            asset_info = raw.get(base, {})
            qty = float(asset_info.get("total") or 0)
            if qty <= 1e-9:
                return None
            # We don't have avg_entry from spot balance — use 0 as placeholder
            # Real avg_entry comes from the order log kept in paper_trader
            return Position(
                symbol=symbol,
                qty=qty,
                avg_entry=0.0,  # Unknown from balance alone
                unrealized_pnl_pct=0.0,
                tp_price=None,
                sl_price=None,
                tp_order_id=None,
                sl_order_id=None,
                entry_order_id=None,
            )
        except Exception as exc:
            self._handle_ccxt_error(exc, "fetch_position")
            return None

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        try:
            ccxt_sym = self._ccxt_sym(symbol)
            raw_orders = self._ex.fetch_open_orders(ccxt_sym)
            return [self._parse_order(o, symbol) for o in raw_orders]
        except Exception as exc:
            self._handle_ccxt_error(exc, "fetch_open_orders")
            return []

    def create_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        block_reason = self._check_pre_order(symbol)
        if block_reason:
            log.warning("[broker] create_entry blocked: %s", block_reason)
            raise ValueError(f"Order blocked: {block_reason}")

        # Daily loss check
        try:
            bal = self.fetch_balance()
            if _risk.check_daily_loss(bal.equity):
                raise ValueError("Order blocked: daily_loss_limit_exceeded — kill switch armed")
        except ValueError:
            raise
        except Exception:
            pass

        ccxt_sym = self._ccxt_sym(symbol)
        market = self._ex.markets.get(ccxt_sym, {})

        # Precision
        try:
            qty_precise = float(self._ex.amount_to_precision(ccxt_sym, quantity))
        except Exception:
            qty_precise = quantity

        # Min quantity guard
        limits = market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min") or 0
        min_cost = (limits.get("cost") or {}).get("min") or 0

        if qty_precise <= 0 or qty_precise < min_amount:
            raise ValueError(
                f"below_min_qty: qty={qty_precise} min={min_amount} for {symbol}"
            )

        # Notional guard
        ref_price = price or 0
        if ref_price <= 0:
            try:
                ticker = self._ex.fetch_ticker(ccxt_sym)
                ref_price = float(ticker.get("ask") or ticker.get("last") or 0)
            except Exception:
                pass

        notional = qty_precise * ref_price
        if min_cost > 0 and notional < min_cost:
            raise ValueError(
                f"below_min_notional: notional={notional:.4f} min_cost={min_cost} for {symbol}"
            )

        # Max notional cap (Phase 4)
        max_not = _risk.max_notional()
        if max_not is not None and notional > max_not:
            qty_precise = float(self._ex.amount_to_precision(ccxt_sym, max_not / ref_price)) if ref_price > 0 else qty_precise
            if qty_precise <= 0 or qty_precise < min_amount:
                raise ValueError(
                    f"below_min_qty after notional cap: qty={qty_precise} for {symbol}"
                )

        client_oid = self._client_order_id(symbol, side)
        order_params: dict[str, Any] = {"clientOrderId": client_oid}

        # Use limit or market
        if price is not None and price > 0:
            order_type = "limit"
            price_precise = float(self._ex.price_to_precision(ccxt_sym, price))
        else:
            order_type = "market"
            price_precise = None

        try:
            if order_type == "limit":
                raw = self._ex.create_order(
                    ccxt_sym, "limit", side, qty_precise, price_precise, order_params
                )
            else:
                raw = self._ex.create_order(
                    ccxt_sym, "market", side, qty_precise, None, order_params
                )
        except ccxt.InsufficientFunds as exc:
            raise ValueError(f"insufficient_funds: {exc}") from exc
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            self._handle_ccxt_error(exc, "create_entry network")
            raise ValueError(f"network_error: {exc}") from exc
        except ccxt.RateLimitExceeded as exc:
            self._handle_ccxt_error(exc, "create_entry 429")
            raise ValueError(f"rate_limit: {exc}") from exc

        return self._parse_order(raw, symbol)

    def attach_tp_sl(
        self,
        symbol: str,
        entry: Order,
        tp_price: float,
        sl_price: float,
    ) -> tuple[Order | None, Order | None]:
        """Try to attach TP/SL to an entry order.  Falls back through modes 1→2→3."""
        if tp_price <= 0 and sl_price <= 0:
            return None, None

        ccxt_sym = self._ccxt_sym(symbol)

        # ----------------------------------------------------------------
        # Mode 1: Amend the entry order with takeProfit / stopLoss params
        # ----------------------------------------------------------------
        if entry.status in ("open", "closed", "partial"):
            try:
                params: dict[str, Any] = {}
                if tp_price > 0:
                    params["takeProfit"] = {
                        "triggerPrice": self._ex.price_to_precision(ccxt_sym, tp_price),
                        "triggerBy": "LastPrice",
                    }
                if sl_price > 0:
                    params["stopLoss"] = {
                        "triggerPrice": self._ex.price_to_precision(ccxt_sym, sl_price),
                        "triggerBy": "LastPrice",
                    }
                self._ex.edit_order(entry.id, ccxt_sym, entry.raw_type, entry.side, entry.qty, params=params)
                self._tp_sl_mode = "server"
                log.info("[broker] TP/SL attached via mode 1 (server amend) for %s", symbol)
                return None, None  # TP/SL are server-side, no separate order objects
            except Exception as exc:
                log.warning("[broker] Mode 1 TP/SL failed (%s), trying mode 2: %s", symbol, exc)

        # ----------------------------------------------------------------
        # Mode 2: Separate conditional sell orders
        # ----------------------------------------------------------------
        tp_order: Order | None = None
        sl_order: Order | None = None
        try:
            qty_str = self._ex.amount_to_precision(ccxt_sym, entry.qty)
            qty_f = float(qty_str)

            if tp_price > 0:
                tp_params: dict[str, Any] = {
                    "takeProfitPrice": self._ex.price_to_precision(ccxt_sym, tp_price),
                    "triggerBy": "LastPrice",
                    "orderFilter": "StopOrder",
                    "clientOrderId": self._client_order_id(symbol, "tp"),
                }
                try:
                    raw_tp = self._ex.create_order(ccxt_sym, "market", "sell", qty_f, None, tp_params)
                    tp_order = self._parse_order(raw_tp, symbol)
                except Exception as exc:
                    log.warning("[broker] Mode 2 TP order failed for %s: %s", symbol, exc)

            if sl_price > 0:
                sl_params: dict[str, Any] = {
                    "stopLossPrice": self._ex.price_to_precision(ccxt_sym, sl_price),
                    "triggerBy": "LastPrice",
                    "orderFilter": "StopOrder",
                    "clientOrderId": self._client_order_id(symbol, "sl"),
                }
                try:
                    raw_sl = self._ex.create_order(ccxt_sym, "market", "sell", qty_f, None, sl_params)
                    sl_order = self._parse_order(raw_sl, symbol)
                except Exception as exc:
                    log.warning("[broker] Mode 2 SL order failed for %s: %s", symbol, exc)

            if tp_order or sl_order:
                self._tp_sl_mode = "split"
                log.info("[broker] TP/SL attached via mode 2 (split orders) for %s", symbol)
                return tp_order, sl_order
        except Exception as exc:
            log.warning("[broker] Mode 2 TP/SL entirely failed (%s): %s", symbol, exc)

        # ----------------------------------------------------------------
        # Mode 3: Local price watch (signal back to paper_trader)
        # ----------------------------------------------------------------
        self._tp_sl_mode = "local"
        log.warning(
            "[broker] TP/SL falling back to mode 3 (local) for %s. "
            "Process MUST stay alive for TP/SL to fire. See LIVE.md.",
            symbol,
        )
        return None, None

    def cancel_open_orders(self, symbol: str) -> None:
        try:
            ccxt_sym = self._ccxt_sym(symbol)
            self._ex.cancel_all_orders(ccxt_sym)
            log.info("[broker] Cancelled all open orders for %s", symbol)
        except Exception as exc:
            self._handle_ccxt_error(exc, f"cancel_open_orders({symbol})")

    def fetch_ticker(self, symbol: str) -> Ticker:
        ccxt_sym = self._ccxt_sym(symbol)
        try:
            raw = self._ex.fetch_ticker(ccxt_sym)
            return Ticker(
                symbol=symbol,
                bid=float(raw.get("bid") or 0),
                ask=float(raw.get("ask") or 0),
                last=float(raw.get("last") or 0),
                base_volume=float(raw.get("baseVolume") or 0),
                quote_volume=float(raw.get("quoteVolume") or 0),
            )
        except Exception as exc:
            self._handle_ccxt_error(exc, "fetch_ticker")
            raise

    def fetch_order_book(self, symbol: str, limit: int = 25) -> Book:
        ccxt_sym = self._ccxt_sym(symbol)
        try:
            raw = self._ex.fetch_order_book(ccxt_sym, limit=limit)
            bids: list[list[float]] = raw.get("bids", [])
            asks: list[list[float]] = raw.get("asks", [])

            best_bid = float(bids[0][0]) if bids else 0.0
            best_ask = float(asks[0][0]) if asks else 0.0
            mid = (best_bid + best_ask) / 2

            def depth_usdt(levels: list[list[float]], n: int) -> float:
                return sum(float(p) * float(q) for p, q in levels[:n])

            def base_qty(levels: list[list[float]], n: int) -> float:
                return sum(float(q) for _, q in levels[:n])

            spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0
            bid_base_l10 = base_qty(bids, 10)
            ask_base_l10 = base_qty(asks, 10)
            denom = bid_base_l10 + ask_base_l10
            imbalance = (bid_base_l10 - ask_base_l10) / denom if denom > 0 else 0.0

            return Book(
                symbol=symbol,
                bid=best_bid,
                ask=best_ask,
                spread_bps=round(spread_bps, 4),
                mid=round(mid, 8),
                bid_depth_usdt_l1=round(depth_usdt(bids, 1), 2),
                ask_depth_usdt_l1=round(depth_usdt(asks, 1), 2),
                bid_depth_usdt_l10=round(depth_usdt(bids, 10), 2),
                ask_depth_usdt_l10=round(depth_usdt(asks, 10), 2),
                book_imbalance=round(imbalance, 6),
            )
        except Exception as exc:
            self._handle_ccxt_error(exc, "fetch_order_book")
            raise

    def flatten(self, symbol: str) -> Order | None:
        """Market-sell entire base position. Used by kill switch and local SL."""
        block_reason = self._check_pre_order(symbol)
        # Allow flatten even when unarmed — it's a safety operation
        if block_reason and block_reason != "blocked_unarmed":
            log.warning("[broker] flatten blocked for non-safety reason: %s", block_reason)
            return None
        try:
            pos = self.fetch_position(symbol)
            if not pos or pos.qty <= 1e-9:
                return None
            ccxt_sym = self._ccxt_sym(symbol)
            qty_precise = float(self._ex.amount_to_precision(ccxt_sym, pos.qty))
            if qty_precise <= 0:
                return None
            raw = self._ex.create_order(ccxt_sym, "market", "sell", qty_precise, None, {})
            log.info("[broker] Flatten executed for %s qty=%s", symbol, qty_precise)
            return self._parse_order(raw, symbol)
        except Exception as exc:
            self._handle_ccxt_error(exc, f"flatten({symbol})")
            return None

    # ------------------------------------------------------------------
    # Startup validation (Phase 4)
    # ------------------------------------------------------------------

    @classmethod
    def validate_mainnet_env(cls) -> None:
        """Call this at process startup when BROKER_SANDBOX=0.

        Raises SystemExit with a human-readable message if mandatory
        mainnet env vars are absent or LIVE_ARMED is not set.
        """
        missing = []
        if not _risk.is_armed():
            missing.append("LIVE_ARMED=1")
        if not os.getenv("LIVE_MAX_NOTIONAL_USDT", "").strip():
            missing.append("LIVE_MAX_NOTIONAL_USDT")
        if not os.getenv("LIVE_DAILY_LOSS_USDT", "").strip():
            missing.append("LIVE_DAILY_LOSS_USDT")
        if not os.getenv("LIVE_SYMBOLS", "").strip():
            missing.append("LIVE_SYMBOLS")
        if missing:
            raise SystemExit(
                f"[broker] Mainnet startup refused. Missing or unset:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\n\nSee LIVE.md for the full arming checklist."
            )
