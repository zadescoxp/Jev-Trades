"""Schema for Jev's TypeSafe state and questions.

Phase 0/1: original fields — unchanged.
Phase 2: additive additions for liquidity, volume, volatility, and execution fields.
  realized_vol_20: std of last 20 completed 1m log returns, stored raw (not annualized).
  atr_pct: atr14 / last_close.

Do NOT rename existing question keys. Only additive changes are allowed.
"""

State = {
  "symbol": "BTC-USD",
  "current_price": "332.41",
  "oscillators": {
    "relative_strength_index_14": 62.64,
    "stochastic_percent_k_14_3_3": 81.43,
    "commodity_channel_index_20": 102.57,
    "average_directional_index_14": 19.27,
    "awesome_oscillator": 0.38,
    "momentum_10": 0.37,
    "macd_level_12_26": 0.16,
    "stochastic_rsi_fast_3_3_14_14": 66.05,
    "williams_percent_range_14": -11.11,
    "bull_bear_power": 0.34,
    "ultimate_oscillator_7_14_28": 53.28
  },
  "moving_averages": {
    "ema_10": 332.31,
    "sma_10": 332.34,
    "ema_20": 332.17,
    "sma_20": 332.09,
    "ema_30": 332.09,
    "sma_30": 332.09,
    "ema_50": 332.10,
    "sma_50": 331.70,
    "ema_100": 332.36,
    "sma_100": 332.56,
    "ema_200": 332.62,
    "sma_200": 332.86,
    "ichimoku_base_line_9_26_52_26": 332.05,
    "vwma_20": 332.27,
    "hull_ma_9": 332.40
  },
  "position" : "None",
  "qauntity" : 0,
  "time_frame" : "1 minute",
  "cash_balance" : "",
  "position_quantity" : "",
  "average_entry_price" : "",
  "unrealized_pnl_pct" : "",
  "position_age_bars" : "",
  "stop_loss_price": "",
  "take_profit_price": "",
  "stop_loss_pct": "",
  "take_profit_pct": "",
  "max_wallet_position_pct" : "75%",
  "price" : {
    # atr14: Wilder ATR of last 14 bars
    "atr14" : "day high - day low",
    "change_percent" : "",
    "day_high" : "",
    "day_low" : "",
    "open_price" : "",
    "day_volume" : ""
  },
  # --- Phase 2 additions (null-ok in paper/yfinance mode) ---
  "liquidity": {
    # All null when MARKET_SOURCE=yfinance
    "bid": None,
    "ask": None,
    "spread_bps": None,
    "mid": None,
    "bid_depth_usdt_l1": None,
    "ask_depth_usdt_l1": None,
    "bid_depth_usdt_l10": None,
    "ask_depth_usdt_l10": None,
    # book_imbalance = (bid_qty_l10 - ask_qty_l10) / (bid_qty_l10 + ask_qty_l10)
    # uses BASE qty, not USDT. Null if denom=0 or yfinance.
    "book_imbalance": None,
  },
  "volume": {
    "last_bar_base": None,
    "last_bar_quote": None,
    "volume_sma_20": None,
    "volume_ratio": None,          # last_bar_base / volume_sma_20
    "day_volume_quote": None,
  },
  "volatility": {
    "atr14": None,
    # atr_pct = atr14 / last_close (raw ratio, not %)
    "atr_pct": None,
    # realized_vol_20 = std of last 20 completed 1m log returns — raw, not annualized
    "realized_vol_20": None,
    "range_pct": None,             # (day_high - day_low) / day_low * 100
  },
  "execution": {
    "venue": "paper",              # paper | bybit-testnet | bybit-live
    "min_qty": None,
    "min_notional": None,
    "tick_size": None,
    "taker_fee_bps": None,
    "last_spread_bps": None,
  },
}


Questions = {
  "action_choice": {
    "type": "choice",
    "instructions": (
      "Given `current_price`, `oscillators`, `moving_averages`, `change_percent`, "
      "`day_high`, `day_low`, the current `position`/`quantity`/`average_entry_price`, "
      "and `risk_appetite`, which action best matches the weight of evidence right now? "
      "Respect the supplied risk appetite: aggressive may act on a promising but incomplete "
      "setup, balanced requires broader agreement, and conservative requires strong confirmation. "
      "Also hold when liquidity.book_imbalance strongly favors sellers, volume.volume_ratio < 0.5 "
      "(dead volume), or execution.last_spread_bps is more than 30% of the planned TP distance."
    ),
    "criteria": {
      "buy": (
        "Momentum and trend indicators are broadly bullish-aligned, no oscillator shows extreme "
        "overbought exhaustion, and there is no open position or an existing long the evidence "
        "supports adding to. Liquidity and volume regimes are at least normal."
      ),
      "hold": (
        "Signals are mixed, contradictory, or already reflected in the existing position. "
        "Also hold when the book is thin vs intended size, volume is dead, or spread is too wide."
      ),
      "sell": (
        "Oscillators show overbought exhaustion or moving averages are rolling over, "
        "and there is an open long position the evidence supports reducing or closing."
      ),
    },
  },
  "trend_alignment": {
    "type": "choice",
    "instructions": (
      "Compare `current_price` against short (10-20), medium (30-50), and long (100-200) "
      "period moving averages. Classify the trend structure."
    ),
    "criteria": {
      "strong_uptrend": "Price above all tiers, shorter averages stacked above longer ones.",
      "weak_or_transitional": "Averages clustered close together or crossing.",
      "downtrend": "Price below all tiers, shorter averages stacked below longer ones.",
    },
  },
  "overbought_condition": {
    "type": "noul",
    "instructions": (
      "Do RSI, Stochastic %K, Stochastic RSI Fast, and Williams %R collectively indicate "
      "the instrument is overbought and due for a pause, given `current_price` is also near `day_high`?"
    ),
  },
  "volatility_regime": {
    "type": "score",
    "instructions": (
      "Using the spread between `day_high` and `day_low` relative to `current_price`, "
      "`volatility.atr_pct`, `volatility.realized_vol_20`, and how tightly the moving "
      "averages are clustered, how volatile is the current environment?"
    ),
    "criteria": [
      "Calm: tight day range, moving averages closely bunched, low realized vol.",
      "Normal: moderate day range, some separation between MA tiers.",
      "Volatile: wide day range, MA tiers widely separated or whipsawing, high realized vol.",
    ],
  },
  "signal_confluence": {
    "type": "score",
    "instructions": "How much agreement is there between `oscillators` and `moving_averages`?",
    "criteria": [
      "Conflicting: they point in clearly opposite directions.",
      "Partial: most agree, a subset disagrees.",
      "Strong: near-total alignment.",
    ],
  },
  "buying_quantity": {
    "type": "score",
    "instructions": (
      "Assuming the evidence favored a Buy, and factoring in `risk_appetite`, the current "
      "volatility regime, `max_wallet_position_pct`, and `liquidity.ask_depth_usdt_l10`, "
      "how large should the position be relative to a full-size position? "
      "Prefer a probe if `liquidity.ask_depth_usdt_l10` is less than 3x the intended notional. "
      "Aggressive can choose a larger size when evidence is promising; conservative should prefer "
      "a probe unless alignment is strong."
    ),
    "criteria": [
      "Small probe: directionally bullish but limited confidence, volatile regime, or thin ask-side depth.",
      "Standard size: solid agreement across indicators in a normal volatility regime with adequate depth.",
      "Full size: near-total alignment with no overbought warning, calm-to-normal regime, deep book.",
    ],
  },
  "selling_quantity": {
    "type": "score",
    "instructions": (
      "Assuming the evidence favored a Sell, and there is an open position with a given "
      "`unrealized_pnl_pct`, how much should be reduced?"
    ),
    "criteria": [
      "Partial trim: one mild warning sign, trend structure still intact.",
      "Half reduction: multiple exhaustion signals or MAs flattening.",
      "Full exit: broad-based reversal evidence or a clear trend breakdown.",
    ],
  },
  "stop_loss_target": {
    "type": "choice",
    "instructions": (
      "If entering or managing a position, what stop loss distance is appropriate given "
      "the current volatility regime, `volatility.atr14`, recent swing low, and support levels?"
    ),
    "criteria": {
      "tight": "Tight stop loss (0.75% to 1.5% below entry) for quick scalp or high conviction setups with tight invalidation.",
      "moderate": "Standard stop loss (2.0% to 3.5% below entry) placed below key short-term moving average support (EMA 20 / SMA 50).",
      "wide": "Wide stop loss (4.0% to 6.0% below entry) for volatile swings or longer holding periods.",
    },
  },
  "take_profit_target": {
    "type": "choice",
    "instructions": (
      "If entering or managing a position, what take profit target aligns best with "
      "momentum and upside resistance?"
    ),
    "criteria": {
      "conservative": "Quick profit target (1.5% to 3.0% gain) near immediate local resistance or oscillator peak.",
      "balanced": "Balanced target (3.5% to 6.5% gain) aiming for trend expansion with healthy risk-reward.",
      "aggressive": "Extended runner target (7.0% to 12.0%+ gain) targeting multi-tier breakout or strong momentum rally.",
    },
  },
  "low_reliability_setup": {
    "type": "noul",
    "instructions": (
      "Given `time_frame` is 1 minute and the current volatility regime, are the signals more "
      "likely to be noise than a reliable, tradeable edge? Also flag as low-reliability if "
      "liquidity.spread_bps is wide, volume.volume_ratio < 0.5 (dead volume), or the book is thin."
    ),
  },
  "conviction_level": {
    "type": "score",
    "instructions": (
      "Independent of direction, how much conviction does the full picture provide for taking "
      "any action at all, versus staying flat?"
    ),
    "criteria": [
      "Low: indicators mostly Neutral or contradictory.",
      "Moderate: clear majority agree, a few holdouts.",
      "High: near-unanimous agreement.",
    ],
  },
  # --- Phase 2 additions ---
  "liquidity_regime": {
    "type": "score",
    "instructions": (
      "Using `liquidity.spread_bps`, `liquidity.bid_depth_usdt_l10`, "
      "`liquidity.ask_depth_usdt_l10`, and the intended notional size, "
      "classify the current liquidity environment."
    ),
    "criteria": [
      "Thin: spread is wide relative to TP distance, or L10 depth < intended notional. Avoid full-size entries.",
      "Normal: spread is reasonable and L10 depth comfortably covers intended size.",
      "Deep: L10 depth > 5x intended notional and spread is tight. Full-size entries acceptable.",
    ],
  },
  "volume_regime": {
    "type": "score",
    "instructions": (
      "Using `volume.volume_ratio` (last bar vs 20-bar SMA), classify current volume activity."
    ),
    "criteria": [
      "Dead: volume_ratio < 0.5. Low conviction in any directional move.",
      "Normal: volume_ratio 0.5–1.5. Standard market activity.",
      "Expanding: volume_ratio > 1.5. Elevated conviction in the current move.",
    ],
  },
  "spread_too_wide": {
    "type": "noul",
    "instructions": (
      "Is `execution.last_spread_bps` greater than 30% of the planned take-profit distance in bps? "
      "If yes (spread eats too much of the edge), output 1, else 0. "
      "If spread data is null (paper/yfinance mode), output 0."
    ),
  },
}