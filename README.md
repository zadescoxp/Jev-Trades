# Jev Trades

A Next.js dashboard for live crypto market data and TypeSafe-powered trading.

The system streams live market data from Yahoo Finance through `yfinance` (default), calculates technical indicators across multiple timeframes, sends explicitly enabled trading states to TypeSafe, and applies the returned decisions to a portfolio powered by a local SQLite database. It features both autonomous trading by the Jev agent and a fully-featured manual trade desk with automatic Take Profit and Stop Loss execution.

**Default mode: paper trading (no real money, no exchange keys required).**
For Bybit live/testnet execution see [LIVE.md](LIVE.md).

## See It In Action

![Jev Trades making a paper trade](assets/Jev_making_trade.gif)

The dashboard combines live market data, technical indicators, TypeSafe decisions, and a paper-trading portfolio in one workspace.

<table>
  <tr>
    <td><img src="assets/trading_UI.png" alt="Jev Trades trading dashboard" /></td>
    <td><img src="assets/portfolio_position.png" alt="Jev Trades paper portfolio position" /></td>
  </tr>
  <tr>
    <td align="center"><strong>Trading UI</strong></td>
    <td align="center"><strong>Portfolio position</strong></td>
  </tr>
</table>

## Features

- **Live Market Data & Dynamic Timeframes:** Streams live market data via Yahoo Finance websockets. The chart and technical indicators dynamically update based on the selected timeframe (1m, 5m, 15m, 1h, 4h).
- **Autonomous Agent (Jev):** Powered by TypeSafe, the Jev autonomous decision engine analyzes the market on *every incoming tick* across all active timeframes simultaneously, providing structured judgments for trading.
- **Paper Trading Portfolio:** Simulated trading environment with a persistent SQLite database. Capital can be dynamically adjusted through the UI, accurately updating the account ledger and portfolio equity on the fly.
- **Automated Risk Management:** Automatic calculation and execution of Take Profit and Stop Loss triggers, determined by Jev's analysis, volatility (ATR), and user risk appetite.
- **Manual Trade Execution:** A complete manual trading desk allowing users to execute buys and sells, set custom TP/SL targets, or exit active positions directly from the dashboard.
- **Robust Data Handling:** Built-in safeguards to filter out delayed or out-of-order market ticks, preventing data corruption and chart crashes.
- **Interactive UI:** Dynamic chart overlays, selectable indicator panels, and detailed historical logs for both agent decisions and executed trades.


## Supported assets

- `ADA-USD`
- `XRP-USD`
- `ETH-USD`
- `BTC-USD`
- `SOL-USD`
- `BNB-USD`
- `TRX-USD`

The Python feed subscribes to all supported assets. The dashboard lets you select which asset to inspect and trade.

## Requirements

- Node.js 20 or newer
- Python 3.12 recommended
- A TypeSafe API key for agent decisions

## Setup

From the repository root:

```powershell
cd "C:\Users\Local User\OneDrive\Desktop\jev-trades"

.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
npm install
```

The project uses the root `.venv`. Do not use the older `pipeline\venv` environment.

Create `pipeline/.env` and add your TypeSafe key. Both names are accepted:

```text
TYPESAFE_AI_API_KEY=your_key_here
```

or:

```text
TYPESAFE_API_KEY=your_key_here
```

Keep this file server-side and never expose the key through `NEXT_PUBLIC_*` variables.

The dashboard also has an optional `TYPESAFE KEY` field. A visitor can enter their own key and click `Apply portfolio`; the key is sent to the Python feed over its configuration endpoint and kept in that process memory only. It is not stored in the browser or written to agent logs. Do not use this pattern for a shared public trading account: the current Python service has one shared in-memory paper portfolio and one active key for all connected dashboard users.

## Vercel deployment

Vercel can host the Next.js dashboard, but it cannot host this whole application by itself. The Python collector is a long-running process that owns the Yahoo websocket, SSE stream, paper portfolio, and local JSONL files. Run that service on a separate always-on host such as Railway, Render, Fly.io, or a VPS, then set this Vercel environment variable to its public base URL:

```text
NEXT_PUBLIC_MARKET_FEED_URL=https://your-feed.example.com
```

The feed must allow the Vercel origin through CORS and expose `/stream` and `/config` over HTTPS. The current local JSONL storage and in-memory portfolio are not suitable for a multi-instance production deployment; use a database or a single pinned worker if that state needs to persist.

## Contributing

Contributions are welcome. Open an issue for bugs or ideas, or fork the repository and open a pull request:

https://github.com/zadescoxp/Jev-Trades

Before opening a pull request, run:

```bash
npm run lint
npm run build
python3 -m py_compile pipeline/data_collector.py pipeline/paper_trader.py pipeline/schema.py
```

## License

Jev Trades is released under the Apache License 2.0. See [LICENSE](LICENSE) for the full license text.

## Run the application

Use two terminals.

### Terminal 1: market and paper-trading feed

```powershell
cd "C:\Users\Local User\OneDrive\Desktop\jev-trades"
.venv\Scripts\python.exe pipeline\data_collector.py
```

The Python service listens on:

- Stream: `http://127.0.0.1:8765/stream`
- Health: `http://127.0.0.1:8765/health`
- Configuration: `http://127.0.0.1:8765/config`

### Terminal 2: dashboard

```powershell
cd "C:\Users\Local User\OneDrive\Desktop\jev-trades"
npm run dev
```

Open `http://localhost:3000`.

Only run one Python feed process at a time. Multiple collectors create duplicate websocket subscriptions and duplicate agent decisions.

## Trading workflow

1. Select an asset.
2. Enter the paper-trading capital.
3. Set the maximum wallet position percentage.
4. Choose a risk profile:
   - `Conservative`: higher confidence threshold and smaller allocation multiplier.
   - `Balanced`: standard confidence and allocation behavior.
   - `Aggressive`: lower confidence threshold and larger allocation multiplier.
5. Click `Apply portfolio`.
6. Click `Start trading <asset>`.

Changing the selected asset automatically stops trading. The new asset must be started explicitly.

Market data continues to stream while trading is stopped, but TypeSafe decisions are not submitted and no paper positions are changed.

## Paper portfolio

The account is simulated and starts with the capital entered in the dashboard. The portfolio supports multiple simultaneous positions across supported assets.

Python enforces the hard limits:

- Shared available cash
- Maximum wallet position percentage
- Risk-profile allocation multiplier
- Confidence threshold for buy and sell actions

TypeSafe returns structured judgments. It does not directly execute orders or choose arbitrary capital amounts.

## Indicators

The feed calculates the configured moving averages and oscillators from completed OHLCV candles, including:

- EMA and SMA periods 10, 20, 30, 50, 100, and 200
- Ichimoku base line
- VWMA 20
- Hull MA 9
- RSI 14
- Stochastic %K
- CCI 20
- ADX 14
- Awesome Oscillator
- Momentum 10
- MACD 12/26
- Stochastic RSI
- Williams %R
- Bull/Bear Power
- Ultimate Oscillator

The dashboard provides selectable indicator values and EMA/SMA chart overlays.

## History and logs

Historical one-minute candles are warmed from yfinance and merged with local completed-candle storage under:

```text
pipeline/market_data/
```

Agent request/response events are written to:

```text
pipeline/agent_log.jsonl
```

The dashboard's `Agent logs` tab shows the request sent to TypeSafe and the response received. The `Positions` tab shows executed paper buys and sells with price, quantity, entry price, realized P&L, and cash after the trade.

## Validation commands

```powershell
npm run lint
npm run build
.venv\Scripts\python.exe -m py_compile pipeline\data_collector.py pipeline\paper_trader.py pipeline\schema.py
```

## Project structure

```text
app/
  page.tsx              Dashboard and portfolio controls
  market-chart.tsx      Lightweight Charts client component
  globals.css           Dashboard styling
pipeline/
  data_collector.py     yfinance history, websockets, indicators, SSE, configuration
  paper_trader.py       TypeSafe decisions and paper portfolio ledger
  schema.py             TypeSafe state questions
  requirements.txt      Python dependencies
  market_data/          Local per-asset candle history
```
