"use client";

import dynamic from "next/dynamic";
import Image from "next/image";
import { useEffect, useMemo, useState } from "react";

const MarketChart = dynamic(() => import("./market-chart"), { ssr: false });

type Bar = { time: number; open: number; high: number; low: number; close: number; volume: number };
type Indicators = Record<string, number | null>;
type Trade = {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  entry_price: number;
  realized_pnl: number | null;
  cash_balance: number;
  timestamp: number;
  reason?: string;
  tp?: number | null;
  sl?: number | null;
  is_manual?: boolean;
};
type Position = {
  symbol: string;
  quantity: number;
  average_entry_price: number;
  mark_price: number;
  unrealized_pnl_pct: number;
  position: string;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  stop_loss_pct?: number | null;
  take_profit_pct?: number | null;
  tp_sl_source?: "jev" | "manual";
};
type AgentEvent = {
  timestamp: number;
  price?: number;
  action: string;
  confidence: number | null;
  executed: string;
  reason?: string;
  request?: unknown;
  response?: unknown;
  error?: string | null;
  trade?: Trade;
};
type Trading = {
  account: {
    starting_cash: number;
    cash_balance: number;
    available_cash: number;
    equity: number;
    positions: Record<string, Position>;
    max_wallet_position_pct: number;
    risk_appetite: string;
    paper_trading: boolean;
  };
  agent_log: AgentEvent[];
  positions: Trade[];
  agent_enabled: boolean;
};
type Snapshot = {
  symbol: string;
  supported_symbols: string[];
  status: string;
  trading_enabled: boolean;
  price: number | null;
  bars: Bar[];
  indicators: Indicators;
  indicator_series: { ema20: (number | null)[]; sma50: (number | null)[] };
  last_tick: number | null;
  trading: Trading;
  settings: { capital: number; max_wallet_position_pct: number; risk_appetite: string; trading_enabled: boolean };
};
type BrokerConfig = {
  broker: string;
  sandbox: boolean;
  armed: boolean;
  market_source: string;
  tp_sl_mode: string;
  kill_switch: boolean;
};

const feedBase = process.env.NEXT_PUBLIC_MARKET_FEED_URL ?? "http://127.0.0.1:8765";
const streamBase = process.env.NEXT_PUBLIC_MARKET_STREAM_URL ?? `${feedBase}/stream`;
const symbols = ["ADA-USD", "XRP-USD", "ETH-USD", "BTC-USD", "SOL-USD", "BNB-USD", "TRX-USD"];
const assetLogos: Record<string, string> = {
  "ADA-USD": "/logos/ada.png",
  "XRP-USD": "/logos/xrp.svg",
  "ETH-USD": "/logos/eth.svg",
  "BTC-USD": "/logos/btc.svg",
  "SOL-USD": "/logos/sol.svg",
  "BNB-USD": "/logos/bnb.png",
  "TRX-USD": "/logos/trx.png",
};
const indicatorGroups: { title: string; items: [string, string][] }[] = [
  {
    title: "Moving averages",
    items: [
      ["ema_10", "EMA 10"],
      ["sma_10", "SMA 10"],
      ["ema_20", "EMA 20"],
      ["sma_20", "SMA 20"],
      ["ema_30", "EMA 30"],
      ["sma_30", "SMA 30"],
      ["ema_50", "EMA 50"],
      ["sma_50", "SMA 50"],
      ["ema_100", "EMA 100"],
      ["sma_100", "SMA 100"],
      ["ema_200", "EMA 200"],
      ["sma_200", "SMA 200"],
      ["ichimoku_base_line_9_26_52_26", "Ichimoku base"],
      ["vwma_20", "VWMA 20"],
      ["hull_ma_9", "Hull MA 9"],
    ],
  },
  {
    title: "Oscillators",
    items: [
      ["relative_strength_index_14", "RSI 14"],
      ["stochastic_percent_k_14_3_3", "Stochastic %K"],
      ["commodity_channel_index_20", "CCI 20"],
      ["average_directional_index_14", "ADX 14"],
      ["awesome_oscillator", "Awesome oscillator"],
      ["momentum_10", "Momentum 10"],
      ["macd_level_12_26", "MACD 12/26"],
      ["stochastic_rsi_fast_3_3_14_14", "Stochastic RSI"],
      ["williams_percent_range_14", "Williams %R"],
      ["bull_bear_power", "Bull/Bear power"],
      ["ultimate_oscillator_7_14_28", "Ultimate oscillator"],
    ],
  },
];
const allIndicatorKeys = indicatorGroups.flatMap((group) => group.items.map(([key]) => key));

export default function Home() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [brokerConfig, setBrokerConfig] = useState<BrokerConfig | null>(null);
  const [symbol, setSymbol] = useState("BTC-USD");
  const [capital, setCapital] = useState("100000");
  const [maxWalletPositionPct, setMaxWalletPositionPct] = useState("75");
  const [riskAppetite, setRiskAppetite] = useState("balanced");
  const [typeSafeKey, setTypeSafeKey] = useState("");
  const [tradingEnabled, setTradingEnabled] = useState(false);
  const [overlays, setOverlays] = useState(["ema20"]);
  const [selectedIndicators, setSelectedIndicators] = useState(["ema_20", "sma_50", "relative_strength_index_14", "macd_level_12_26"]);
  const [activityTab, setActivityTab] = useState<"manual" | "logs" | "positions">("manual");
  const [executionMode, setExecutionMode] = useState<"jev" | "manual">("jev");
  const [activeTimeframes, setActiveTimeframes] = useState<string[]>(["1m"]);
  const [tradeTimeframe, setTradeTimeframe] = useState<string>("1m");
  const [chartTimeframe, setChartTimeframe] = useState<string>("1m");

  // Manual Trading State
  const [orderSide, setOrderSide] = useState<"buy" | "sell">("buy");
  const [sizingMode, setSizingMode] = useState<"usd" | "crypto">("usd");
  const [orderAmountUsd, setOrderAmountUsd] = useState("5000");
  const [orderQuantityCrypto, setOrderQuantityCrypto] = useState("0.05");
  const [tpEnabled, setTpEnabled] = useState(true);
  const [tpPct, setTpPct] = useState("5.0");
  const [slEnabled, setSlEnabled] = useState(true);
  const [slPct, setSlPct] = useState("2.5");
  const [orderLoading, setOrderLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Edit TP/SL state for active position
  const [isEditingTpSl, setIsEditingTpSl] = useState(false);
  const [editTpVal, setEditTpVal] = useState("");
  const [editSlVal, setEditSlVal] = useState("");

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const res = await fetch(`${feedBase}/config`);
        const data = await res.json();
        if (data.ok) setBrokerConfig(data);
      } catch (err) {
        console.error(err);
      }
    };
    void fetchConfig();
    const interval = setInterval(() => { void fetchConfig(); }, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const source = new EventSource(`${streamBase}?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(chartTimeframe)}`);
    source.onmessage = (event) => {
      const next = JSON.parse(event.data) as Snapshot;
      setSnapshot(next);
      setTradingEnabled(next.trading_enabled);
    };
    source.onerror = () => setSnapshot((current) => (current ? { ...current, status: "feed unavailable" } : null));
    return () => source.close();
  }, [symbol, chartTimeframe]);

  const configurePortfolio = async (enabled = tradingEnabled, selectedSymbol = symbol) => {
    try {
      const response = await fetch(`${feedBase}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: selectedSymbol,
          capital: Number(capital),
          max_wallet_position_pct: Number(maxWalletPositionPct) / 100,
          risk_appetite: riskAppetite,
          trading_enabled: enabled,
          typesafe_api_key: typeSafeKey.trim() || undefined,
          active_timeframes: activeTimeframes,
        }),
      });
      if (!response.ok) throw new Error(`Configuration request failed (${response.status})`);
      setTradingEnabled(enabled);
    } catch (error) {
      console.error("Could not configure the paper-trading feed", error);
    }
  };

  const price = snapshot?.price;
  const change = useMemo(() => {
    const first = snapshot?.bars[0]?.open;
    return price && first ? ((price - first) / first) * 100 : null;
  }, [price, snapshot?.bars]);

  const selectedPosition = snapshot?.trading.account.positions[symbol];
  const availableCash = snapshot?.trading.account.available_cash ?? 0;

  const toggle = (name: string) => setOverlays((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]));
  const toggleIndicator = (name: string) => setSelectedIndicators((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]));
  const labelFor = (name: string) => indicatorGroups.flatMap((group) => group.items).find(([key]) => key === name)?.[1] ?? name;

  // Execute manual order (buy, sell, exit, kill_switch)
  const handleExecuteOrder = async (action: "buy" | "sell" | "exit" | "kill_switch", extraParams: Record<string, any> = {}) => {
    setOrderLoading(true);
    setFeedback(null);
    try {
      const payload: Record<string, any> = {
        action,
        symbol,
        timeframe: tradeTimeframe,
        ...extraParams,
      };

      if (action === "buy") {
        if (sizingMode === "usd") {
          payload.amount_usd = Number(orderAmountUsd);
        } else {
          payload.quantity = Number(orderQuantityCrypto);
        }
        if (tpEnabled && Number(tpPct) > 0) {
          payload.take_profit_pct = Number(tpPct);
        }
        if (slEnabled && Number(slPct) > 0) {
          payload.stop_loss_pct = Number(slPct);
        }
      } else if (action === "sell") {
        if (!extraParams.pct_of_position) {
          if (sizingMode === "crypto" && Number(orderQuantityCrypto) > 0) {
            payload.quantity = Number(orderQuantityCrypto);
          } else {
            payload.pct_of_position = 1.0;
          }
        }
      }

      const res = await fetch(`${feedBase}/order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || "Order execution failed");
      }
      setFeedback({ type: "success", text: `${action.toUpperCase()} order executed successfully!` });
      setTimeout(() => setFeedback(null), 4000);
    } catch (err: any) {
      setFeedback({ type: "error", text: err.message || "Execution error" });
      setTimeout(() => setFeedback(null), 5000);
    } finally {
      setOrderLoading(false);
    }
  };

  const handleUpdatePositionTpSl = async () => {
    setOrderLoading(true);
    try {
      const res = await fetch(`${feedBase}/order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "update_tp_sl",
          symbol,
          timeframe: tradeTimeframe,
          take_profit_pct: editTpVal ? Number(editTpVal) : undefined,
          stop_loss_pct: editSlVal ? Number(editSlVal) : undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || "Failed to update TP/SL");
      }
      setIsEditingTpSl(false);
      setFeedback({ type: "success", text: "Updated TP & SL targets successfully!" });
      setTimeout(() => setFeedback(null), 4000);
    } catch (err: any) {
      setFeedback({ type: "error", text: err.message || "Failed to update TP/SL" });
      setTimeout(() => setFeedback(null), 5000);
    } finally {
      setOrderLoading(false);
    }
  };

  const setCashPercent = (pct: number) => {
    if (!availableCash) return;
    const targetUsd = (availableCash * (pct / 100)).toFixed(2);
    setOrderAmountUsd(targetUsd);
    if (price && price > 0) {
      setOrderQuantityCrypto((Number(targetUsd) / price).toFixed(6));
    }
  };

  return (
    <main className="dashboard">
      {brokerConfig && brokerConfig.broker !== "paper" ? (
        <div className={`broker-banner ${brokerConfig.kill_switch ? "kill-active" : brokerConfig.sandbox ? "testnet" : "mainnet"}`}>
          <div className="banner-content">
            <span className="broker-mode">
              {brokerConfig.kill_switch ? "⚠️ KILL SWITCH ACTIVE" : brokerConfig.sandbox ? "🧪 BYBIT TESTNET" : "⚠️ BYBIT MAINNET (LIVE)"}
            </span>
            <span className="broker-details">
              Market Data: {brokerConfig.market_source} | TP/SL Mode: {brokerConfig.tp_sl_mode}
            </span>
          </div>
          {!brokerConfig.kill_switch && (
            <button className="kill-btn" onClick={() => void handleExecuteOrder("kill_switch")}>
              STOP ALL & FLATTEN (KILL SWITCH)
            </button>
          )}
        </div>
      ) : null}
      <header className="topbar">
        <div>
          <p className="eyebrow">JEV TRADES / LIVE FEED</p>
          <h1>{symbol}</h1>
          <p className="muted">
            {symbol} <span className="dot" /> {chartTimeframe === "1m" ? "1 minute" : chartTimeframe === "5m" ? "5 minute" : chartTimeframe === "15m" ? "15 minute" : chartTimeframe === "1h" ? "1 hour" : "4 hour"} candles
          </p>
        </div>
        <div className="topbar-meta">
          <a className="creator-link" href="https://x.com/zadescoxp" target="_blank" rel="noreferrer">
            made by @zade
          </a>
          <div className="connection">
            <span className={`status-dot ${snapshot?.status === "live" ? "is-live" : ""}`} />
            {snapshot?.status ?? "connecting"}
          </div>
        </div>
      </header>

      <section className="quote-grid">
        <div className="quote">
          <span className="label">LAST PRICE</span>
          <strong>{price ? `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "--"}</strong>
          <span className={change !== null && change >= 0 ? "positive" : "negative"}>{change === null ? "Waiting for ticks" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}% session`}</span>
        </div>
        <Metric label="RSI (14)" value={snapshot?.indicators.rsi14?.toFixed(2) ?? "--"} />
        <Metric label="MACD" value={snapshot?.indicators.macd?.toFixed(2) ?? "--"} />
        <Metric label="LAST TICK" value={snapshot?.last_tick ? new Date(snapshot.last_tick * 1000).toLocaleTimeString() : "--"} />
      </section>

      <div className="workspace-grid">
        <div className="main-column">
          <section className="chart-panel">
            <div className="panel-head">
              <div>
                <p className="eyebrow">PRICE ACTION</p>
                <h2>{symbol} / USD</h2>
              </div>
              <div className="controls">
                <div className="tf-switcher">
                  {["1m", "5m", "15m", "1h", "4h"].map((tf) => (
                    <button key={tf} className={chartTimeframe === tf ? "control active" : "control"} onClick={() => setChartTimeframe(tf)}>
                      {tf}
                    </button>
                  ))}
                </div>
                {[
                  ["ema20", "EMA 20"],
                  ["sma50", "SMA 50"],
                ].map(([name, label]) => (
                  <button key={name} className={overlays.includes(name) ? "control active" : "control"} onClick={() => toggle(name)}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {snapshot ? (
              <MarketChart bars={snapshot.bars} indicatorSeries={snapshot.indicator_series} overlays={overlays} position={selectedPosition} />
            ) : (
              <div className="loading">Waiting for the market feed...</div>
            )}
            <p className="attribution">Charts powered by TradingView Lightweight Charts with dynamic Entry, TP, and SL lines. Data: Yahoo Finance.</p>
          </section>

          <section className="indicator-panel">
            <div className="panel-head">
              <div>
                <p className="eyebrow">INDICATOR LIBRARY</p>
                <h2>Choose live values to display</h2>
              </div>
              <span className="muted">
                {selectedIndicators.length} selected / {allIndicatorKeys.length}
              </span>
            </div>
            <div className="indicator-groups">
              {indicatorGroups.map((group) => (
                <div className="indicator-group" key={group.title}>
                  <span className="label">{group.title}</span>
                  <div className="indicator-options">
                    {group.items.map(([key, label]) => (
                      <button key={key} className={selectedIndicators.includes(key) ? "indicator-option active" : "indicator-option"} onClick={() => toggleIndicator(key)}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="indicator-values">
              {selectedIndicators.map((key) => (
                <div className="indicator-value" key={key}>
                  <span>{labelFor(key)}</span>
                  <strong>{snapshot?.indicators[key] === null || snapshot?.indicators[key] === undefined ? "Warming up" : snapshot.indicators[key]?.toFixed(4)}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="lower-grid">
            <Metric label="EMA 20" value={snapshot?.indicators.ema20?.toFixed(2) ?? "--"} />
            <Metric label="SMA 50" value={snapshot?.indicators.sma50?.toFixed(2) ?? "Warming up"} />
            <div className="note">
              <span className="label">PIPELINE</span>
              <p>Jev autonomous decision engine & manual trade execution with automated Take Profit & Stop Loss triggers.</p>
            </div>
          </section>
        </div>

        <aside className="trading-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">
                {brokerConfig && brokerConfig.broker.includes("bybit")
                  ? brokerConfig.sandbox ? "BYBIT TESTNET" : "BYBIT MAINNET"
                  : "PAPER TRADING"}
              </p>
              <h2>Trade Desk</h2>
            </div>
            <span className={`paper-badge ${brokerConfig && brokerConfig.broker.includes("bybit") ? (brokerConfig.sandbox ? "testnet-badge" : "mainnet-badge") : ""}`}>
              {brokerConfig && brokerConfig.broker.includes("bybit")
                ? brokerConfig.sandbox ? "TESTNET" : "REAL MONEY"
                : "SIMULATION"}
            </span>
          </div>

          {/* Account Overview */}
          <div className="account-grid">
            <Metric label="AVAILABLE CASH" value={snapshot ? `$${snapshot.trading.account.available_cash.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "--"} />
            <Metric label="EQUITY" value={snapshot ? `$${snapshot.trading.account.equity.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "--"} />
            <Metric label={`${symbol} POSITION`} value={selectedPosition ? `${selectedPosition.quantity.toFixed(4)} ${symbol.split("-")[0]}` : "Flat"} />
            <Metric
              label={`${symbol} P&L`}
              value={
                selectedPosition
                  ? `${selectedPosition.unrealized_pnl_pct >= 0 ? "+" : ""}${selectedPosition.unrealized_pnl_pct.toFixed(2)}%`
                  : "--"
              }
            />
          </div>

          {/* Unified Settings */}
          <div className="unified-settings">
            <div className="settings-row">
              <label className="setting-field">
                <span className="field-label">ASSET</span>
                <div className="asset-picker">
                  <Image src={assetLogos[symbol]} alt="" className="asset-logo" width={18} height={18} />
                  <select
                    value={symbol}
                    onChange={(event) => {
                      const nextSymbol = event.target.value;
                      setSymbol(nextSymbol);
                      void configurePortfolio(false, nextSymbol);
                    }}
                  >
                    {symbols.map((item) => (
                      <option key={item}>{item}</option>
                    ))}
                  </select>
                </div>
              </label>
              <label className="setting-field">
                <span className="field-label">CAPITAL ($)</span>
                <input type="number" min="0" step="100" value={capital} onChange={(event) => setCapital(event.target.value)} />
              </label>
            </div>
            <div className="settings-row">
              <label className="setting-field">
                <span className="field-label">MAX POS %</span>
                <input type="number" min="1" max="100" step="1" value={maxWalletPositionPct} onChange={(event) => setMaxWalletPositionPct(event.target.value)} />
              </label>
              <label className="setting-field">
                <span className="field-label">RISK</span>
                <select value={riskAppetite} onChange={(event) => setRiskAppetite(event.target.value)}>
                  <option value="conservative">Conservative</option>
                  <option value="balanced">Balanced</option>
                  <option value="aggressive">Aggressive</option>
                </select>
              </label>
              <label className="setting-field">
                <span className="field-label">TIMEFRAME</span>
                <select value={tradeTimeframe} onChange={(e) => { setTradeTimeframe(e.target.value); setActiveTimeframes([e.target.value]); }}>
                  <option value="1m">1m</option>
                  <option value="5m">5m</option>
                  <option value="15m">15m</option>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                </select>
              </label>
            </div>
            <div className="settings-row">
              <label className="setting-field key-field-unified">
                <span className="field-label">TYPESAFE KEY</span>
                <input type="password" autoComplete="off" placeholder="Optional" value={typeSafeKey} onChange={(event) => setTypeSafeKey(event.target.value)} />
              </label>
              <button className="control apply-btn" onClick={() => void configurePortfolio()}>
                Apply
              </button>
            </div>
          </div>

          {/* Active Position Spotlight & Quick Exit */}
          {selectedPosition && selectedPosition.quantity > 0 ? (
            <div className="active-position-card">
              <div className="pos-card-header">
                <div className="pos-badge">
                  <span className="pos-side">LONG {selectedPosition.symbol}</span>
                  <span className="pos-source">{selectedPosition.tp_sl_source === "jev" ? "🤖 JEV AUTO" : "👤 MANUAL"}</span>
                </div>
                <div className={`pos-pnl ${selectedPosition.unrealized_pnl_pct >= 0 ? "positive" : "negative"}`}>
                  {selectedPosition.unrealized_pnl_pct >= 0 ? "+" : ""}
                  {selectedPosition.unrealized_pnl_pct.toFixed(2)}%
                </div>
              </div>

              <div className="pos-card-body">
                <div className="pos-metric">
                  <span className="pos-label">Entry:</span>
                  <strong>${selectedPosition.average_entry_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
                </div>
                <div className="pos-metric">
                  <span className="pos-label">Size:</span>
                  <strong>{selectedPosition.quantity.toFixed(6)}</strong>
                </div>
                <div className="pos-metric">
                  <span className="pos-label">Take Profit:</span>
                  <strong className="text-lime">
                    {selectedPosition.take_profit_price
                      ? `$${selectedPosition.take_profit_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (+${selectedPosition.take_profit_pct}%)`
                      : "None"}
                  </strong>
                </div>
                <div className="pos-metric">
                  <span className="pos-label">Stop Loss:</span>
                  <strong className="text-coral">
                    {selectedPosition.stop_loss_price
                      ? `$${selectedPosition.stop_loss_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (-${selectedPosition.stop_loss_pct}%)`
                      : "None"}
                  </strong>
                </div>
              </div>

              {isEditingTpSl ? (
                <div className="tpsl-edit-box">
                  <div className="tpsl-edit-inputs">
                    <label>
                      TP %
                      <input
                        type="number"
                        step="0.5"
                        placeholder="e.g. 5"
                        value={editTpVal}
                        onChange={(e) => setEditTpVal(e.target.value)}
                      />
                    </label>
                    <label>
                      SL %
                      <input
                        type="number"
                        step="0.5"
                        placeholder="e.g. 2.5"
                        value={editSlVal}
                        onChange={(e) => setEditSlVal(e.target.value)}
                      />
                    </label>
                  </div>
                  <div className="tpsl-edit-actions">
                    <button className="btn-sm btn-save" onClick={handleUpdatePositionTpSl} disabled={orderLoading}>
                      Save TP/SL
                    </button>
                    <button className="btn-sm btn-cancel" onClick={() => setIsEditingTpSl(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="pos-card-actions">
                  <button
                    className="exit-btn danger"
                    onClick={() => handleExecuteOrder("exit")}
                    disabled={orderLoading}
                    title="Instantly exit 100% of this position at current market price"
                  >
                    {orderLoading ? "Exiting..." : `Exit Position (100%)`}
                  </button>
                  <button
                    className="exit-btn secondary"
                    onClick={() => handleExecuteOrder("sell", { pct_of_position: 0.5 })}
                    disabled={orderLoading}
                    title="Exit 50% of this position"
                  >
                    Exit 50%
                  </button>
                  <button
                    className="exit-btn outline"
                    onClick={() => {
                      setEditTpVal(selectedPosition.take_profit_pct?.toString() || "");
                      setEditSlVal(selectedPosition.stop_loss_pct?.toString() || "");
                      setIsEditingTpSl(true);
                    }}
                  >
                    Edit TP/SL
                  </button>
                </div>
              )}
            </div>
          ) : null}

          {/* Execution Mode Toggle */}
          <div className="execution-mode-toggle">
            <button
              className={`exec-mode-btn ${executionMode === "jev" ? "active" : ""}`}
              onClick={() => setExecutionMode("jev")}
            >
              🤖 Jev Auto
            </button>
            <button
              className={`exec-mode-btn ${executionMode === "manual" ? "active" : ""}`}
              onClick={() => setExecutionMode("manual")}
            >
              👤 Manual Trade
            </button>
          </div>

          {feedback ? <div className={`feedback-banner ${feedback.type}`}>{feedback.text}</div> : null}

          {/* --- Jev Auto Mode --- */}
          {executionMode === "jev" ? (
            <div className="jev-auto-panel">
              <p className="jev-desc">
                Jev will autonomously analyze indicators across your selected timeframe and execute trades based on your risk settings above.
              </p>
              <button
                className={tradingEnabled ? "jev-execute-btn running" : "jev-execute-btn"}
                onClick={() => void configurePortfolio(!tradingEnabled)}
              >
                {tradingEnabled
                  ? `⏹ Stop Jev Auto-Trading`
                  : brokerConfig && brokerConfig.broker.includes("bybit")
                    ? brokerConfig.sandbox ? `▶ Start Trading on Testnet` : `▶ Start Trading on Mainnet`
                    : `▶ Start Jev Auto-Trading`}
              </button>
              {tradingEnabled && (
                <div className="jev-status-pill">
                  <span className="pulse-dot" /> Jev is actively monitoring {symbol} on {tradeTimeframe}
                </div>
              )}
            </div>
          ) : (
            /* --- Manual Trade Mode --- */
            <div className="manual-order-panel">
              <div className="side-selector">
                <button
                  className={`side-btn buy-side ${orderSide === "buy" ? "active" : ""}`}
                  onClick={() => setOrderSide("buy")}
                >
                  BUY / LONG
                </button>
                <button
                  className={`side-btn sell-side ${orderSide === "sell" ? "active" : ""}`}
                  onClick={() => setOrderSide("sell")}
                >
                  SELL / EXIT
                </button>
              </div>

              {/* Sizing Mode Switch */}
              <div className="order-field-row">
                <span className="field-label">ORDER SIZE</span>
                <div className="sizing-mode-toggle">
                  <button
                    className={`mode-btn ${sizingMode === "usd" ? "active" : ""}`}
                    onClick={() => setSizingMode("usd")}
                  >
                    USD ($)
                  </button>
                  <button
                    className={`mode-btn ${sizingMode === "crypto" ? "active" : ""}`}
                    onClick={() => setSizingMode("crypto")}
                  >
                    {symbol.split("-")[0]} (Qty)
                  </button>
                </div>
              </div>

              {/* Sizing Input */}
              <div className="order-input-wrapper">
                {sizingMode === "usd" ? (
                  <div className="input-group">
                    <span className="input-prefix">$</span>
                    <input
                      type="number"
                      min="1"
                      step="100"
                      value={orderAmountUsd}
                      onChange={(e) => {
                        const val = e.target.value;
                        setOrderAmountUsd(val);
                        if (price && price > 0) {
                          setOrderQuantityCrypto((Number(val) / price).toFixed(6));
                        }
                      }}
                      placeholder="Amount in USD"
                    />
                  </div>
                ) : (
                  <div className="input-group">
                    <span className="input-prefix">{symbol.split("-")[0]}</span>
                    <input
                      type="number"
                      min="0.000001"
                      step="0.01"
                      value={orderQuantityCrypto}
                      onChange={(e) => {
                        const val = e.target.value;
                        setOrderQuantityCrypto(val);
                        if (price && price > 0) {
                          setOrderAmountUsd((Number(val) * price).toFixed(2));
                        }
                      }}
                      placeholder={`Quantity of ${symbol.split("-")[0]}`}
                    />
                  </div>
                )}
              </div>

              {/* Conversion helper */}
              <div className="size-hint">
                {sizingMode === "usd" ? (
                  <span>
                    ≈ {price && Number(orderAmountUsd) > 0 ? (Number(orderAmountUsd) / price).toFixed(6) : "0"} {symbol.split("-")[0]}
                  </span>
                ) : (
                  <span>
                    ≈ ${price && Number(orderQuantityCrypto) > 0 ? (Number(orderQuantityCrypto) * price).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "0"} USD
                  </span>
                )}
              </div>

              {/* Quick Cash Presets */}
              <div className="quick-presets">
                <button className="preset-chip" onClick={() => setCashPercent(25)}>
                  25%
                </button>
                <button className="preset-chip" onClick={() => setCashPercent(50)}>
                  50%
                </button>
                <button className="preset-chip" onClick={() => setCashPercent(75)}>
                  75%
                </button>
                <button className="preset-chip" onClick={() => setCashPercent(100)}>
                  100% Cash
                </button>
              </div>

              {orderSide === "buy" ? (
                <>
                  {/* Take Profit Setting */}
                  <div className="risk-setting-row">
                    <div className="risk-header">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={tpEnabled}
                          onChange={(e) => setTpEnabled(e.target.checked)}
                        />
                        <span>TAKE PROFIT (TP)</span>
                      </label>
                      {tpEnabled && price ? (
                        <span className="target-calc text-lime">
                          Target: ${(price * (1 + Number(tpPct) / 100)).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                        </span>
                      ) : null}
                    </div>
                    {tpEnabled ? (
                      <div className="risk-input-row">
                        <div className="input-group compact">
                          <input
                            type="number"
                            min="0.1"
                            step="0.5"
                            value={tpPct}
                            onChange={(e) => setTpPct(e.target.value)}
                          />
                          <span className="input-suffix">% gain</span>
                        </div>
                        <div className="risk-presets">
                          {["2", "5", "8", "12"].map((p) => (
                            <button
                              key={p}
                              className={`preset-chip sm ${tpPct === p ? "active" : ""}`}
                              onClick={() => setTpPct(p)}
                            >
                              +{p}%
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>

                  {/* Stop Loss Setting */}
                  <div className="risk-setting-row">
                    <div className="risk-header">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={slEnabled}
                          onChange={(e) => setSlEnabled(e.target.checked)}
                        />
                        <span>STOP LOSS (SL)</span>
                      </label>
                      {slEnabled && price ? (
                        <span className="target-calc text-coral">
                          Target: ${(price * (1 - Number(slPct) / 100)).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                        </span>
                      ) : null}
                    </div>
                    {slEnabled ? (
                      <div className="risk-input-row">
                        <div className="input-group compact">
                          <input
                            type="number"
                            min="0.1"
                            step="0.5"
                            value={slPct}
                            onChange={(e) => setSlPct(e.target.value)}
                          />
                          <span className="input-suffix">% loss</span>
                        </div>
                        <div className="risk-presets">
                          {["1.5", "2.5", "4", "6"].map((p) => (
                            <button
                              key={p}
                              className={`preset-chip sm ${slPct === p ? "active" : ""}`}
                              onClick={() => setSlPct(p)}
                            >
                              -{p}%
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </>
              ) : (
                <div className="sell-info-box">
                  <p>
                    {selectedPosition && selectedPosition.quantity > 0
                      ? `You hold ${selectedPosition.quantity.toFixed(6)} ${symbol}. Executing sell will reduce or close your position at current market price.`
                      : `No active position for ${symbol}. Enter a quantity to exit if held.`}
                  </p>
                </div>
              )}

              {/* Main Submit Button */}
              <button
                className={`main-order-btn ${orderSide === "buy" ? "buy-action" : "sell-action"}`}
                onClick={() => handleExecuteOrder(orderSide)}
                disabled={orderLoading || !price}
              >
                {orderLoading
                  ? "Executing Order..."
                  : orderSide === "buy"
                  ? `Buy ${symbol}`
                  : `Sell / Exit ${symbol}`}
              </button>
            </div>
          )}

          {/* Activity Tabs: Logs & History */}
          <div className="activity-tabs">
            <button className={activityTab === "logs" ? "activity-tab active" : "activity-tab"} onClick={() => setActivityTab("logs")}>
              Jev Logs
            </button>
            <button className={activityTab === "positions" ? "activity-tab active" : "activity-tab"} onClick={() => setActivityTab("positions")}>
              History ({snapshot?.trading.positions.length ?? 0})
            </button>
          </div>

          {activityTab === "logs" ? (
            <div className="agent-log">
              {snapshot?.trading.agent_log.length ? (
                snapshot.trading.agent_log
                  .slice()
                  .reverse()
                  .map((event, index) => (
                    <details className="agent-event" key={`${event.timestamp}-${index}`}>
                      <summary>
                        <span className={`action action-${event.executed || "unknown"}`}>{(event.executed || "UNKNOWN").toUpperCase()}</span>
                        <strong>{event.price ? `$${event.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "--"}</strong>
                        <span>{event.reason ?? (event.confidence === null ? "error" : `${(event.confidence * 100).toFixed(0)}% confidence`)}</span>
                        <time>{new Date(event.timestamp * 1000).toLocaleTimeString()}</time>
                      </summary>
                      <div className="agent-payloads">
                        {event.trade ? (
                          <div className="trade-meta-box">
                            <span className="payload-title">TRADE DETAILS</span>
                            <div className="trade-meta-grid">
                              <div>Side: {event.trade.side.toUpperCase()}</div>
                              <div>Qty: {event.trade.quantity.toFixed(6)}</div>
                              <div>Price: ${event.trade.price.toLocaleString()}</div>
                              {event.trade.tp ? <div className="text-lime">TP: ${event.trade.tp.toLocaleString()}</div> : null}
                              {event.trade.sl ? <div className="text-coral">SL: ${event.trade.sl.toLocaleString()}</div> : null}
                              {event.trade.realized_pnl !== null ? (
                                <div className={event.trade.realized_pnl >= 0 ? "text-lime" : "text-coral"}>
                                  Realized P&L: ${event.trade.realized_pnl.toFixed(2)}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                        {event.request ? (
                          <div>
                            <span className="payload-title">REQUEST SENT</span>
                            <pre>{JSON.stringify(event.request, null, 2)}</pre>
                          </div>
                        ) : null}
                        {event.response ? (
                          <div>
                            <span className="payload-title">RESPONSE RECEIVED</span>
                            <pre>{JSON.stringify(event.response, null, 2)}</pre>
                          </div>
                        ) : null}
                      </div>
                      {event.error ? <em>{event.error}</em> : null}
                    </details>
                  ))
              ) : (
                <p className="loading-log">
                  {snapshot && !snapshot.trading.agent_enabled
                    ? "TypeSafe key not loaded by the Python feed"
                    : "Waiting for trade events or Jev decisions..."}
                </p>
              )}
            </div>
          ) : (
            <div className="positions-list">
              {snapshot?.trading.positions.length ? (
                snapshot.trading.positions
                  .slice()
                  .reverse()
                  .map((trade, index) => (
                    <div className={`position-record position-${trade.side}`} key={`${trade.timestamp}-${index}`}>
                      <div className="position-head">
                        <div className="trade-tag-group">
                          <span className={`action action-${trade.side}`}>
                            {trade.symbol} {trade.side.toUpperCase()}
                          </span>
                          <span className="trade-reason-tag">
                            {trade.reason === "stop_loss"
                              ? "🛑 STOP LOSS"
                              : trade.reason === "take_profit"
                              ? "🎯 TAKE PROFIT"
                              : trade.is_manual
                              ? "👤 MANUAL"
                              : "🤖 JEV AUTO"}
                          </span>
                        </div>
                        <time>{new Date(trade.timestamp * 1000).toLocaleString()}</time>
                      </div>
                      <div className="position-details">
                        <Metric label="PRICE" value={`$${trade.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        <Metric label="QUANTITY" value={trade.quantity.toFixed(6)} />
                        <Metric label="ENTRY" value={`$${trade.entry_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        <Metric
                          label="REALIZED P&L"
                          value={trade.realized_pnl === null ? "Open" : `$${trade.realized_pnl.toFixed(2)}`}
                        />
                        <Metric
                          label="CASH AFTER"
                          value={`$${trade.cash_balance.toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
                        />
                        {trade.tp ? (
                          <Metric label="TAKE PROFIT" value={`$${trade.tp.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        ) : null}
                        {trade.sl ? (
                          <Metric label="STOP LOSS" value={`$${trade.sl.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        ) : null}
                      </div>
                    </div>
                  ))
              ) : (
                <p className="loading-log">No executed buy or sell positions yet.</p>
              )}
            </div>
          )}
          ) : (
            <div className="positions-list">
              {snapshot?.trading.positions.length ? (
                snapshot.trading.positions
                  .slice()
                  .reverse()
                  .map((trade, index) => (
                    <div className={`position-record position-${trade.side}`} key={`${trade.timestamp}-${index}`}>
                      <div className="position-head">
                        <div className="trade-tag-group">
                          <span className={`action action-${trade.side}`}>
                            {trade.symbol} {trade.side.toUpperCase()}
                          </span>
                          <span className="trade-reason-tag">
                            {trade.reason === "stop_loss"
                              ? "🛑 STOP LOSS"
                              : trade.reason === "take_profit"
                              ? "🎯 TAKE PROFIT"
                              : trade.is_manual
                              ? "👤 MANUAL"
                              : "🤖 JEV AUTO"}
                          </span>
                        </div>
                        <time>{new Date(trade.timestamp * 1000).toLocaleString()}</time>
                      </div>
                      <div className="position-details">
                        <Metric label="PRICE" value={`$${trade.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        <Metric label="QUANTITY" value={trade.quantity.toFixed(6)} />
                        <Metric label="ENTRY" value={`$${trade.entry_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        <Metric
                          label="REALIZED P&L"
                          value={trade.realized_pnl === null ? "Open" : `$${trade.realized_pnl.toFixed(2)}`}
                        />
                        <Metric
                          label="CASH AFTER"
                          value={`$${trade.cash_balance.toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
                        />
                        {trade.tp ? (
                          <Metric label="TAKE PROFIT" value={`$${trade.tp.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        ) : null}
                        {trade.sl ? (
                          <Metric label="STOP LOSS" value={`$${trade.sl.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
                        ) : null}
                      </div>
                    </div>
                  ))
              ) : (
                <p className="loading-log">No executed buy or sell positions yet.</p>
              )}
            </div>
        </aside>
      </div>

      <section className="contribute-panel">
        <div>
          <p className="eyebrow">OPEN SOURCE</p>
          <h2>Build with Jev Trades</h2>
          <p className="muted">Autonomous agentic intelligence meets precise manual execution.</p>
        </div>
        <a className="github-link" href="https://github.com/zadescoxp/Jev-Trades" target="_blank" rel="noreferrer">
          Contribute on GitHub <span aria-hidden="true">-&gt;</span>
        </a>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span className="label">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
