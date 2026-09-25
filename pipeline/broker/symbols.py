"""Symbol translation between dashboard app ids and venue symbols.

Dashboard / schema / paper_trader always use app ids (e.g. ETH-USD).
Only broker implementations and venue feeds see the exchange symbol (e.g. ETH/USDT).
"""

from __future__ import annotations

# App id → CCXT spot symbol (never perps — spot only)
APP_TO_CCXT: dict[str, str] = {
    "BTC-USD": "BTC/USDT",
    "ETH-USD": "ETH/USDT",
    "SOL-USD": "SOL/USDT",
    "BNB-USD": "BNB/USDT",
    "XRP-USD": "XRP/USDT",
    "ADA-USD": "ADA/USDT",
    "TRX-USD": "TRX/USDT",
}

# Reverse map for translating venue responses back to app ids
CCXT_TO_APP: dict[str, str] = {v: k for k, v in APP_TO_CCXT.items()}


def to_ccxt(app_symbol: str) -> str:
    """Translate an app id to its CCXT spot symbol.

    Raises KeyError if the symbol is not in the supported set.
    """
    try:
        return APP_TO_CCXT[app_symbol]
    except KeyError:
        raise KeyError(
            f"Unknown app symbol '{app_symbol}'. "
            f"Supported: {sorted(APP_TO_CCXT)}"
        ) from None


def to_app(ccxt_symbol: str) -> str:
    """Translate a CCXT spot symbol back to an app id.

    Raises KeyError if the symbol is not recognised.
    """
    try:
        return CCXT_TO_APP[ccxt_symbol]
    except KeyError:
        raise KeyError(
            f"Unknown CCXT symbol '{ccxt_symbol}'. "
            f"Supported: {sorted(CCXT_TO_APP)}"
        ) from None
