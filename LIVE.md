# LIVE.md — Live Broker Operations Guide

> **DANGER:** This document describes enabling real-money trading on Bybit.
> Read every section before touching `LIVE_ARMED=1`.

---

## 1. Architecture overview

```
candles + indicators + account  →  schema.py state
    → TypeSafe Jev structured answers
    → confidence / risk / wallet gates in paper_trader.py  (policy layer)
    → Broker.create_entry + Broker.attach_tp_sl            (execution layer)
    → log + SSE + dashboard
```

The **only** process that places orders is `pipeline/data_collector.py`.
No Next.js route, no browser button, no webhook touches the venue directly.

---

## 2. Venue and API key setup

### 2.1 Key scopes (Bybit)

| Permission      | Required? | Notes                          |
|-----------------|-----------|--------------------------------|
| Spot trade      | ✅ Yes    | Needed for create/cancel       |
| Read positions  | ✅ Yes    | For balance + position sync    |
| Withdraw        | ❌ NO     | Must be OFF — never enable     |
| IP whitelist    | ✅ Yes    | Add your server IP             |

### 2.2 Testnet keys

Testnet keys only work on `api-testnet.bybit.com`.
They are **not** the same as production keys.

Create testnet keys at: <https://testnet.bybit.com/app/user/api-management>

Set in `pipeline/.env`:
```
BROKER=bybit
BROKER_SANDBOX=1
MARKET_SOURCE=bybit
BYBIT_API_KEY=<testnet key>
BYBIT_API_SECRET=<testnet secret>
LIVE_ARMED=0
```

> **Warning:** Do NOT use `enable_demo_trading(True)` and `set_sandbox_mode(True)` together — CCXT raises `NotSupported`.
> Testnet = `set_sandbox_mode(True)` only.

### 2.3 Mainnet keys

Mainnet keys work on `api.bybit.com`.
They are **production keys** with real funds.

---

## 3. Testnet vs Demo vs Mainnet

| Mode         | CCXT switch                    | Host                   | Keys         |
|--------------|--------------------------------|------------------------|--------------|
| Paper        | none                           | none                   | none         |
| Bybit testnet| `set_sandbox_mode(True)`       | api-testnet.bybit.com  | testnet only |
| Bybit demo   | `enable_demo_trading(True)`    | api-demo.bybit.com     | prod keys    |
| Bybit mainnet| neither                        | api.bybit.com          | prod keys    |

Error `10003` means key/host mismatch — check you are using testnet keys with testnet and prod keys with mainnet.

---

## 4. TP/SL modes

Three modes are tried in order:

| Mode  | Description                                                                     | Banner value    |
|-------|---------------------------------------------------------------------------------|-----------------|
| 1     | Attach `takeProfitPrice` + `stopLossPrice` on the entry order (server-side)     | `tp_sl=server`  |
| 2     | Entry order + two separate conditional sell orders (server-side)                | `tp_sl=split`   |
| 3     | Local price watch + market sell if Python detects trigger (process-dependent)   | `tp_sl=local`   |

**Current mode: TBD (Phase 3 will record which mode worked on Bybit spot UTA)**

> If mode 3 is active, the process **must stay alive** for TP/SL to fire.
> A process crash will leave an unguarded position.

---

## 5. Mainnet arming checklist (Phase 4)

Do not set `LIVE_ARMED=1` until **every** item is checked:

- [ ] Testnet soak: ≥ 8 hours, ≥ 3 TP/SL clips visible on Bybit testnet UI
- [ ] No orphan sell orders after manual flatten
- [ ] `LIVE_SYMBOLS` set to the exact symbols you intend to trade (e.g. `ETH-USD`)
- [ ] `LIVE_MAX_NOTIONAL_USDT` set to a small value (e.g. 50)
- [ ] `LIVE_DAILY_LOSS_USDT` set (e.g. 15)
- [ ] Withdraw permission is **OFF** on the API key
- [ ] IP whitelist is set on the API key
- [ ] You have confirmed the machine running this is NOT a shared/public server
- [ ] `pipeline/.env` is in `.gitignore` and has never been committed
- [ ] `LIVE_ARMED=1` is only set immediately before a monitored live session

---

## 6. Runbook

### Paper (default — safe):
```bash
BROKER=paper MARKET_SOURCE=yfinance python pipeline/data_collector.py
npm run dev
```

### Bybit candles + paper orders (no keys needed):
```bash
BROKER=paper MARKET_SOURCE=bybit python pipeline/data_collector.py
```

### Bybit testnet orders:
```bash
# Ensure pipeline/.env has BROKER=bybit, BROKER_SANDBOX=1, MARKET_SOURCE=bybit
python pipeline/data_collector.py
# Open Bybit testnet UI (not production!)
```

### Kill switch (immediate):
```bash
# Set env variable:
LIVE_KILL_SWITCH=1

# Or create the sentinel file:
touch pipeline/KILL
```

The process will cancel all open orders, flatten the spot position, and halt new entries until restart.

---

## 7. Known ambiguities / fallbacks

| Item                               | Status      | Notes                                          |
|------------------------------------|-------------|------------------------------------------------|
| Bybit spot TP/SL on UTA accounts   | TBD Phase 3 | Will try attach-on-entry first                 |
| Bitget as backup venue             | Out of scope| Mention only — implement as separate Broker class later |
| Partial fill handling              | Implemented | TP/SL qty = filled qty, not requested qty      |

---

## 8. Security reminders

- Keys are read from `pipeline/.env` only — never from Next.js, SSE, or logs.
- The `/config` SSE endpoint exposes: `broker`, `sandbox`, `armed`, `market_source`, `live_symbols`, `tp_sl_mode`, `kill_switch` — **no keys**.
- Rate limiter (`enableRateLimit=True`) is always on.
- Single collector process — no second worker may place orders.
