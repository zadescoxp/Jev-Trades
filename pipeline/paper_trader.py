"""Confidence-gated trading driven by TypeSafe structured judgments with TP/SL execution and manual controls.

Policy layer: decides WHAT to do.
Broker layer: decides HOW to send it to the venue.

This module must never import ccxt.  All exchange I/O is behind pipeline/broker/.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

try:
    from .schema import Questions
except ImportError:
    from schema import Questions  # type: ignore[no-redef]

try:
    from . import db
except ImportError:
    import db  # type: ignore[no-redef]

# Broker type import — only the protocol/types, never ccxt
try:
    from .broker.base import Broker
    from .broker.types import Order as BrokerOrder
except ImportError:
    from broker.base import Broker  # type: ignore[no-redef]
    from broker.types import Order as BrokerOrder  # type: ignore[no-redef]

LOG_PATH = Path(__file__).with_name("agent_log.jsonl")
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
load_dotenv(Path(__file__).with_name(".env"))
if os.getenv("TYPESAFE_AI_API_KEY") and not os.getenv("TYPESAFE_API_KEY"):
    os.environ["TYPESAFE_API_KEY"] = os.environ["TYPESAFE_AI_API_KEY"]


class PaperTrader:
    def __init__(
        self,
        capital: float = 100_000.0,
        max_wallet_position_pct: float = 0.75,
        risk_appetite: str = "balanced",
        broker: Broker | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.starting_cash = capital
        self.max_wallet_position_pct = max(0.01, min(max_wallet_position_pct, 1.0))
        self.risk_appetite = risk_appetite
        self.api_key = os.getenv("TYPESAFE_API_KEY", "")

        # Broker is injected via DI; defaults to PaperBroker if None.
        if broker is None:
            try:
                from .broker import make_broker
            except ImportError:
                from broker import make_broker  # type: ignore[no-redef]
            self.broker: Broker = make_broker()  # type: ignore[assignment]
        else:
            self.broker = broker

        db.init_db(capital)

        self.account_metadata: dict[str, Any] = {
            "starting_cash": capital,
            "last_action": "hold",
            "last_decision_at": None,
            "paper_trading": getattr(self.broker, "name", "paper") == "paper",
            "broker": getattr(self.broker, "name", "paper"),
        }
        self.current_prices: dict[str, float] = {}

        self.recent_logs: list[dict[str, Any]] = []
        self.positions_log: list[dict[str, Any]] = []  # Kept for UI history
        self.pending: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker = threading.Thread(target=self._run, daemon=True, name="typesafe-paper-trader")
        self.worker.start()

    def configure(
        self,
        capital: float | None = None,
        max_wallet_position_pct: float | None = None,
        risk_appetite: str | None = None,
        api_key: str | None = None,
    ) -> None:
        with self.lock:
            if api_key is not None and api_key.strip():
                self.api_key = api_key.strip()
            if capital is not None:
                diff = capital - self.starting_cash
                if diff != 0:
                    self.starting_cash = max(0.0, capital)
                    self.account_metadata["starting_cash"] = self.starting_cash
                    db.adjust_capital(diff)
            if max_wallet_position_pct is not None:
                self.max_wallet_position_pct = max(0.01, min(max_wallet_position_pct, 1.0))
            if risk_appetite in {"conservative", "balanced", "aggressive"}:
                self.risk_appetite = risk_appetite

    def submit(self, state: dict[str, Any]) -> None:
        if self.api_key:
            self.pending.put(deepcopy(state))

    def snapshot(self, price: float | None = None, symbol: str | None = None) -> dict[str, Any]:
        with self.lock:
            if price is not None and symbol:
                self.current_prices[symbol] = price
                # Keep broker mark-prices in sync
                if hasattr(self.broker, "set_prices"):
                    self.broker.set_prices(self.current_prices)  # type: ignore[attr-defined]

            cash_balance = db.get_cash_balance()
            equity = cash_balance

            is_live = getattr(self.broker, "name", "paper") != "paper"
            if is_live and hasattr(self.broker, "fetch_balance"):
                try:
                    b = self.broker.fetch_balance()
                    cash_balance = b.free_usdt
                    equity = b.equity  # Bybit equity already includes position value
                except Exception:
                    pass

            positions_raw = db.get_positions()
            positions = {}

            for p in positions_raw:
                sym = p["symbol"]
                # In Phase 1 we emulate the old structure for positions
                tf = p["timeframe"]
                key = sym if tf == "1m" else f"{sym}:{tf}"
                mark = self.current_prices.get(key, self.current_prices.get(sym, p["avg_entry_price"]))

                entry = p["avg_entry_price"]
                unrealized_pnl_pct = round(((mark - entry) / entry) * 100, 4) if entry > 0 else 0.0

                # Reconstruct old dict format for UI compatibility
                positions[key] = {
                    "symbol": sym,
                    "quantity": p["qty"],
                    "average_entry_price": entry,
                    "mark_price": mark,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "position": "Long",
                    "stop_loss_price": p["sl_price"],
                    "take_profit_price": p["tp_price"],
                    "tp_sl_source": p.get("tp_sl_source", "jev"),
                }
                if p["sl_price"] and entry > 0:
                    positions[key]["stop_loss_pct"] = round(((entry - p["sl_price"]) / entry) * 100, 2)
                if p["tp_price"] and entry > 0:
                    positions[key]["take_profit_pct"] = round(((p["tp_price"] - entry) / entry) * 100, 2)

                # Only add position value to equity for paper mode;
                # for live broker, b.equity already includes it.
                if not is_live:
                    equity += p["qty"] * mark

            account = {
                **self.account_metadata,
                "cash_balance": cash_balance,
                "positions": positions,
                "equity": equity,
                "available_cash": cash_balance,
                "max_wallet_position_pct": self.max_wallet_position_pct,
                "risk_appetite": self.risk_appetite,
            }

            trades_raw = db.get_trades(100)
            ui_trades = []
            for t in trades_raw:
                ui_trades.append({
                    "symbol": t["symbol"],
                    "side": t["side"],
                    "quantity": t["qty"],
                    "price": t["price"],
                    "entry_price": t["price"],  # fallback
                    "realized_pnl": t["realized_pnl"],
                    "cash_balance": 0.0,  # no longer easily available per trade in this table
                    "timestamp": t["executed_at"],
                    "reason": t["source"],
                    "is_manual": t["source"] == "manual",
                })

            return {
                "account": account,
                "agent_log": list(self.recent_logs[-15:]),
                "positions": ui_trades,
                "agent_enabled": bool(self.api_key),
            }

    def check_tp_sl(self, symbol: str, current_price: float, timeframe: str = "1m") -> dict[str, Any] | None:
        """Check if active position breached Stop Loss or Take Profit targets and execute exit."""
        if current_price <= 0:
            return None
        with self.lock:
            self.current_prices[symbol] = current_price
            self.current_prices[f"{symbol}:{timeframe}"] = current_price

            position = db.get_position(symbol, timeframe)
            if not position or position["qty"] <= 1e-12:
                return None

            entry_price = position["avg_entry_price"]
            sl_price = position["sl_price"]
            tp_price = position["tp_price"]
            quantity = position["qty"]

            # Check Stop Loss
            if sl_price is not None and current_price <= sl_price:
                trade = db.execute_trade(symbol, timeframe, "sell", quantity, current_price, 0.0, "stop_loss", self.current_prices)
                self.account_metadata["last_action"] = "stop_loss"
                self.account_metadata["last_decision_at"] = time.time()

                trade_record = {
                    "symbol": symbol,
                    "side": "sell",
                    "quantity": quantity,
                    "price": current_price,
                    "entry_price": entry_price,
                    "realized_pnl": trade["realized_pnl"],
                    "cash_balance": db.get_cash_balance(),
                    "timestamp": time.time(),
                    "reason": "stop_loss",
                    "tp": tp_price,
                    "sl": sl_price,
                    "is_manual": False,
                }
                event = {
                    "timestamp": time.time(),
                    "price": current_price,
                    "action": "stop_loss",
                    "confidence": 1.0,
                    "executed": "sell",
                    "reason": f"Stop Loss triggered at ${current_price:,.2f} (SL: ${sl_price:,.2f})",
                    "account": self.snapshot().get("account"),
                    "trade": trade_record,
                }
                self.positions_log.append(trade_record)
                self._write_log(event)
                return trade_record

            # Check Take Profit
            if tp_price is not None and current_price >= tp_price:
                trade = db.execute_trade(symbol, timeframe, "sell", quantity, current_price, 0.0, "take_profit", self.current_prices)
                self.account_metadata["last_action"] = "take_profit"
                self.account_metadata["last_decision_at"] = time.time()

                trade_record = {
                    "symbol": symbol,
                    "side": "sell",
                    "quantity": quantity,
                    "price": current_price,
                    "entry_price": entry_price,
                    "realized_pnl": trade["realized_pnl"],
                    "cash_balance": db.get_cash_balance(),
                    "timestamp": time.time(),
                    "reason": "take_profit",
                    "tp": tp_price,
                    "sl": sl_price,
                    "is_manual": False,
                }
                event = {
                    "timestamp": time.time(),
                    "price": current_price,
                    "action": "take_profit",
                    "confidence": 1.0,
                    "executed": "sell",
                    "reason": f"Take Profit triggered at ${current_price:,.2f} (TP: ${tp_price:,.2f})",
                    "account": self.snapshot().get("account"),
                    "trade": trade_record,
                }
                self.positions_log.append(trade_record)
                self._write_log(event)
                return trade_record

        return None

    def manual_buy(
        self,
        symbol: str,
        price: float,
        quantity: float | None = None,
        amount_usd: float | None = None,
        stop_loss_pct: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_pct: float | None = None,
        take_profit_price: float | None = None,
        timeframe: str = "1m",
    ) -> dict[str, Any]:
        """Execute a manual buy order with optional custom quantity, TP, and SL."""
        with self.lock:
            if price <= 0:
                raise ValueError("Invalid price for buy order")

            if getattr(self.broker, "name", "paper") != "paper":
                try:
                    available = self.broker.fetch_balance().free_usdt
                except Exception:
                    available = db.get_cash_balance()
            else:
                available = db.get_cash_balance()

            if available <= 0:
                raise ValueError("Insufficient cash balance")

            buffer_mult = 0.95 if getattr(self.broker, "name", "paper") != "paper" else 1.0

            if amount_usd is not None and amount_usd > 0:
                allocation = min(available * buffer_mult, float(amount_usd))
                qty = allocation / price
            elif quantity is not None and quantity > 0:
                allocation = float(quantity) * price
                if allocation > available * buffer_mult:
                    allocation = available * buffer_mult
                    qty = allocation / price
                else:
                    qty = float(quantity)
            else:
                allocation = min(available * buffer_mult, self.starting_cash * self.max_wallet_position_pct * 0.5)
                qty = allocation / price

            if qty <= 1e-12:
                raise ValueError("Order quantity is too small")

            sl_price_final: float | None = None
            if stop_loss_price is not None and stop_loss_price > 0 and stop_loss_price < price:
                sl_price_final = round(stop_loss_price, 4)
            elif stop_loss_pct is not None and stop_loss_pct > 0:
                sl_pct = round(stop_loss_pct, 2)
                sl_price_final = round(price * (1.0 - sl_pct / 100.0), 4)

            tp_price_final: float | None = None
            if take_profit_price is not None and take_profit_price > price:
                tp_price_final = round(take_profit_price, 4)
            elif take_profit_pct is not None and take_profit_pct > 0:
                tp_pct = round(take_profit_pct, 2)
                tp_price_final = round(price * (1.0 + take_profit_pct / 100.0), 4)

            self.current_prices[symbol] = price
            self.current_prices[f"{symbol}:{timeframe}"] = price

            # Route through broker (paper or live)
            try:
                entry_order = self.broker.create_entry(symbol, "buy", qty, price)
                self.broker.attach_tp_sl(symbol, entry_order, tp_price_final or 0.0, sl_price_final or 0.0) if (tp_price_final or sl_price_final) else None
                if getattr(self.broker, "name", "paper") != "paper":
                    db.execute_trade(symbol, timeframe, "buy", qty, price, 0.0, "manual", self.current_prices)
                if tp_price_final or sl_price_final:
                    db.update_tp_sl_in_db(symbol, timeframe, tp_price_final, sl_price_final, "manual")
            except Exception as exc:
                raise ValueError(str(exc)) from exc

            self.account_metadata["last_action"] = "buy"
            self.account_metadata["last_decision_at"] = time.time()

            trade = {
                "symbol": symbol,
                "side": "buy",
                "quantity": qty,
                "price": price,
                "entry_price": price,
                "realized_pnl": None,
                "cash_balance": db.get_cash_balance(),
                "timestamp": time.time(),
                "reason": "manual_buy",
                "tp": tp_price_final,
                "sl": sl_price_final,
                "is_manual": True,
            }
            event = {
                "timestamp": time.time(),
                "price": price,
                "action": "buy",
                "confidence": 1.0,
                "executed": "buy",
                "reason": f"Manual Buy order executed for {qty:.6f} {symbol}",
                "account": self.snapshot().get("account"),
                "trade": trade,
            }
            self.positions_log.append(trade)
            self._write_log(event)
            return trade

    def manual_sell(
        self,
        symbol: str,
        price: float,
        quantity: float | None = None,
        pct_of_position: float = 1.0,
        timeframe: str = "1m",
    ) -> dict[str, Any]:
        """Execute a manual sell / exit order for an active position."""
        with self.lock:
            if price <= 0:
                raise ValueError("Invalid price for sell order")

            position = db.get_position(symbol, timeframe)
            if not position or position["qty"] <= 1e-12:
                raise ValueError(f"No active position found for {symbol} to sell")

            current_qty = position["qty"]
            if quantity is not None and quantity > 0:
                sell_qty = min(current_qty, float(quantity))
            else:
                pct = max(0.01, min(pct_of_position, 1.0))
                sell_qty = current_qty * pct

            if sell_qty <= 1e-12:
                raise ValueError("Sell quantity too small")

            entry_price = position["avg_entry_price"]

            self.current_prices[symbol] = price
            self.current_prices[f"{symbol}:{timeframe}"] = price

            try:
                self.broker.create_entry(symbol, "sell", sell_qty, price)
                if getattr(self.broker, "name", "paper") != "paper":
                    t = db.execute_trade(symbol, timeframe, "sell", sell_qty, price, 0.0, "manual", self.current_prices)
                else:
                    # PaperBroker create_entry handles execute_trade implicitly for backwards compat
                    # But we need the realized PnL, so we just run it directly for paper too...
                    pass
            except Exception as exc:
                raise ValueError(str(exc)) from exc
            
            # Since paper broker might have executed it, or we just executed it for live broker:
            # wait, PaperBroker.create_entry already executed it. If we do it again, it's double.
            # Actually, let's just make PaperTrader ALWAYS responsible for SQLite if not PaperBroker.
            # But PaperTrader needs the result dict `t` for realized_pnl.
            # Let's just always call db.execute_trade for the local state after the broker succeeds,
            # except PaperBroker already does it. So if PaperBroker, we don't call it, but we need `t`.
            # To fix this cleanly: we just call `db.execute_trade` and if it's paper we don't duplicate.
            # Wait, PaperBroker DOES duplicate if we call it. Let's not call create_entry on PaperBroker for manual_sell?
            # No, just let's reconstruct `t`:
            t = {"realized_pnl": 0.0} # Fallback
            if getattr(self.broker, "name", "paper") != "paper":
                t = db.execute_trade(symbol, timeframe, "sell", sell_qty, price, 0.0, "manual", self.current_prices)
            else:
                # In paper mode, create_entry did it. We need to fetch the last trade for PNL.
                trades = db.get_trades(1)
                t = trades[0] if trades else {"realized_pnl": 0.0}

            self.account_metadata["last_action"] = "sell"
            self.account_metadata["last_decision_at"] = time.time()

            trade = {
                "symbol": symbol,
                "side": "sell",
                "quantity": sell_qty,
                "price": price,
                "entry_price": entry_price,
                "realized_pnl": t["realized_pnl"],
                "cash_balance": db.get_cash_balance(),
                "timestamp": time.time(),
                "reason": "manual_exit" if pct_of_position >= 0.999 else "manual_sell",
                "is_manual": True,
            }
            event = {
                "timestamp": time.time(),
                "price": price,
                "action": "sell",
                "confidence": 1.0,
                "executed": "sell",
                "reason": f"Manual Sell/Exit order executed for {sell_qty:.6f} {symbol}",
                "account": self.snapshot().get("account"),
                "trade": trade,
            }
            self.positions_log.append(trade)
            self._write_log(event)
            return trade

    def update_tp_sl(
        self,
        symbol: str,
        stop_loss_pct: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_pct: float | None = None,
        take_profit_price: float | None = None,
        timeframe: str = "1m",
    ) -> dict[str, Any]:
        """Update Take Profit and Stop Loss levels for an open position."""
        with self.lock:
            position = db.get_position(symbol, timeframe)
            if not position:
                raise ValueError(f"No active position for {symbol}")

            entry = position["avg_entry_price"]
            key = symbol if timeframe == "1m" else f"{symbol}:{timeframe}"
            mark = self.current_prices.get(key, entry)
            ref_price = entry if entry > 0 else mark

            sl_price = position["sl_price"]
            if stop_loss_price is not None:
                sl_price = round(stop_loss_price, 4) if stop_loss_price > 0 else None
            elif stop_loss_pct is not None:
                if stop_loss_pct > 0:
                    sl_price = round(ref_price * (1.0 - stop_loss_pct / 100.0), 4)
                else:
                    sl_price = None

            tp_price = position["tp_price"]
            if take_profit_price is not None:
                tp_price = round(take_profit_price, 4) if take_profit_price > 0 else None
            elif take_profit_pct is not None:
                if take_profit_pct > 0:
                    tp_price = round(ref_price * (1.0 + take_profit_pct / 100.0), 4)
                else:
                    tp_price = None

            db.update_tp_sl_in_db(symbol, timeframe, tp_price, sl_price, "manual")

            # For backward compatibility return a dict resembling the old format
            res = dict(position)
            res["tp_price"] = tp_price
            res["sl_price"] = sl_price
            return res

    def _run(self) -> None:
        while True:
            state = self.pending.get()
            try:
                request_payload, response = self._ask_typesafe(state)
                event = self._apply_decision(state, response, request_payload)
            except Exception as error:
                event = self._event(
                    state,
                    "error",
                    {"error": f"{type(error).__name__}: {error}"},
                    None,
                    self._request_payload(state),
                    {"error": f"{type(error).__name__}: {error}"},
                )
            self._write_log(event)

    def _request_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"state": state, "model": "jev-latest", "questions": Questions}

    def _ask_typesafe(self, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._request_payload(state)
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            TYPESAFE_URL,
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return payload, json.loads(response.read().decode())

    def _calculate_jev_tp_sl(
        self, price: float, answers: dict[str, Any], atr: float | None = None
    ) -> tuple[float, float, float, float]:
        """Derive Stop Loss and Take Profit levels based on Jev's structured judgments, risk appetite, and ATR."""
        sl_choice = answers.get("stop_loss_target", {}).get("choice", "moderate")
        tp_choice = answers.get("take_profit_target", {}).get("choice", "balanced")

        risk_sl_mult = {"conservative": 0.8, "balanced": 1.0, "aggressive": 1.3}.get(self.risk_appetite, 1.0)
        risk_tp_mult = {"conservative": 0.9, "balanced": 1.0, "aggressive": 1.2}.get(self.risk_appetite, 1.0)

        if atr is not None and atr > 0:
            sl_map_atr = {"tight": 1.5, "moderate": 2.5, "wide": 4.0}
            tp_map_atr = {"conservative": 2.0, "balanced": 4.0, "aggressive": 8.0}

            base_sl = atr * sl_map_atr.get(sl_choice, 2.5)
            base_tp = atr * tp_map_atr.get(tp_choice, 4.0)

            sl_price = round(price - (base_sl * risk_sl_mult), 4)
            tp_price = round(price + (base_tp * risk_tp_mult), 4)

            sl_pct = round(((price - sl_price) / price) * 100, 2)
            tp_pct = round(((tp_price - price) / price) * 100, 2)
        else:
            sl_map = {"tight": 1.5, "moderate": 3.0, "wide": 5.0}
            tp_map = {"conservative": 3.0, "balanced": 6.0, "aggressive": 10.0}

            base_sl_pct = sl_map.get(sl_choice, 3.0)
            base_tp_pct = tp_map.get(tp_choice, 6.0)

            sl_pct = round(base_sl_pct * risk_sl_mult, 2)
            tp_pct = round(base_tp_pct * risk_tp_mult, 2)

            sl_price = round(price * (1.0 - sl_pct / 100.0), 4)
            tp_price = round(price * (1.0 + tp_pct / 100.0), 4)

        return sl_price, tp_price, sl_pct, tp_pct

    def _apply_decision(
        self, state: dict[str, Any], response: dict[str, Any], request_payload: dict[str, Any]
    ) -> dict[str, Any]:
        answers = response.get("answers", {})
        action_answer = answers.get("action_choice", {})
        action = action_answer.get("choice", "hold")
        confidence = float(action_answer.get("confidence", 0.0))
        price = float(state["current_price"])
        symbol = str(state["symbol"])
        timeframe = state.get("time_frame", "1 minute").split(" ")[0]
        if timeframe == "1":
            timeframe = "1m"

        self.current_prices[symbol] = price
        self.current_prices[f"{symbol}:{timeframe}"] = price

        executed = "hold"
        trade: dict[str, Any] | None = None
        with self.lock:
            position = db.get_position(symbol, timeframe)
            
            if getattr(self.broker, "name", "paper") != "paper":
                try:
                    cash_balance = self.broker.fetch_balance().free_usdt
                except Exception:
                    cash_balance = db.get_cash_balance()
            else:
                cash_balance = db.get_cash_balance()
                
            threshold = {"conservative": 0.75, "balanced": 0.6, "aggressive": 0.5}[self.risk_appetite]

            if confidence >= threshold and action == "buy":
                atr = state.get("price", {}).get("atr14")

                size_score = float(answers.get("buying_quantity", {}).get("score", 0.0))
                fraction = 0.25 if size_score < 0.67 else 0.5 if size_score < 1.34 else 1.0
                risk_multiplier = {"conservative": 0.5, "balanced": 0.75, "aggressive": 1.0}[self.risk_appetite]

                if atr and price > 0:
                    atr_pct = (atr / price) * 100
                    volatility_discount = max(0.2, min(1.0, 1.5 / atr_pct)) if atr_pct > 0 else 1.0
                    fraction *= volatility_discount

                buffer_mult = 0.95 if getattr(self.broker, "name", "paper") != "paper" else 1.0
                allocation = min(cash_balance * buffer_mult, self.starting_cash * self.max_wallet_position_pct * risk_multiplier * fraction)
                quantity = allocation / price

                sl_price, tp_price, sl_pct, tp_pct = self._calculate_jev_tp_sl(price, answers, atr)

                # ---- route through broker ----
                try:
                    entry_order = self.broker.create_entry(symbol, "buy", quantity, price)
                    self.broker.attach_tp_sl(symbol, entry_order, tp_price, sl_price)
                    
                    if getattr(self.broker, "name", "paper") != "paper":
                        db.execute_trade(symbol, timeframe, "buy", quantity, price, 0.0, "jev", self.current_prices)
                    db.update_tp_sl_in_db(symbol, timeframe, tp_price, sl_price, "jev")

                    executed = "buy"
                    trade = {
                        "symbol": symbol,
                        "side": "buy",
                        "quantity": quantity,
                        "price": price,
                        "entry_price": price,
                        "realized_pnl": None,
                        "cash_balance": db.get_cash_balance(),
                        "timestamp": time.time(),
                        "reason": "jev_buy",
                        "tp": tp_price,
                        "sl": sl_price,
                        "is_manual": False,
                        "venue_order_id": entry_order.id,
                    }
                except Exception as exc:
                    executed = "hold"
                    self._log_internal("broker_error", {"action": "buy", "error": str(exc)})

            elif confidence >= threshold and action == "sell" and position and position["qty"] > 0:
                size_score = float(answers.get("selling_quantity", {}).get("score", 0.0))
                fraction = 0.25 if size_score < 0.67 else 0.5 if size_score < 1.34 else 1.0
                quantity = position["qty"] * fraction
                entry_price = position["avg_entry_price"]

                try:
                    sell_order = self.broker.create_entry(symbol, "sell", quantity, price)
                    
                    t = {"realized_pnl": 0.0}
                    if getattr(self.broker, "name", "paper") != "paper":
                        t = db.execute_trade(symbol, timeframe, "sell", quantity, price, 0.0, "jev", self.current_prices)
                    else:
                        trades = db.get_trades(1)
                        t = trades[0] if trades else {"realized_pnl": 0.0}

                    executed = "sell"
                    trade = {
                        "symbol": symbol,
                        "side": "sell",
                        "quantity": quantity,
                        "price": price,
                        "entry_price": entry_price,
                        "realized_pnl": (price - entry_price) * quantity,
                        "cash_balance": db.get_cash_balance(),
                        "timestamp": time.time(),
                        "reason": "jev_sell",
                        "is_manual": False,
                        "venue_order_id": sell_order.id,
                    }
                except Exception as exc:
                    executed = "hold"
                    self._log_internal("broker_error", {"action": "sell", "error": str(exc)})

            self.account_metadata["last_action"] = action
            self.account_metadata["last_decision_at"] = time.time()
            db.log_decision(symbol, timeframe, answers, confidence, executed != "hold", action)

        return self._event(state, executed, response, confidence, request_payload, response, trade)

    def _event(
        self,
        state: dict[str, Any],
        executed: str,
        response: dict[str, Any],
        confidence: float | None,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        trade: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        answers = response.get("answers", {}) if isinstance(response, dict) else response
        action = answers.get("action_choice", {}).get("choice", "hold") if isinstance(answers, dict) else "error"
        event = {
            "timestamp": time.time(),
            "price": state.get("current_price"),
            "action": action,
            "confidence": confidence,
            "executed": executed,
            "answers": answers,
            "account": self.snapshot().get("account"),
            "request": request_payload,
            "response": response_payload,
            "error": response.get("error") if isinstance(response, dict) else None,
        }
        if trade:
            event["trade"] = trade
            self.positions_log.append(trade)
        return event

    def _log_internal(self, event_type: str, data: dict[str, Any]) -> None:
        """Write an internal event (non-TypeSafe) to the agent log."""
        entry = {
            "timestamp": time.time(),
            "action": event_type,
            "confidence": None,
            "executed": "error" if "error" in data or "error" in event_type else "hold",
            "reason": str(data),
            "event_type": event_type,
            **data
        }
        with self.lock:
            self.recent_logs.append(entry)
            self.recent_logs = self.recent_logs[-15:]
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _write_log(self, event: dict[str, Any]) -> None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(event, separators=(",", ":")) + "\n")
        with self.lock:
            self.recent_logs.append(event)
            self.recent_logs = self.recent_logs[-15:]
