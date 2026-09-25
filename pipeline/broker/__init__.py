"""Broker factory — reads env and returns the correct Broker implementation.

Env variables (pipeline/.env):
    BROKER           paper | bybit          (default: paper)
    BROKER_SANDBOX   1 | 0                  (default: 1, only used when BROKER=bybit)
    LIVE_ARMED       1 | 0                  (default: 0, must be 1 for live mainnet)

Phase 0: PaperBroker
Phase 3: CcxtSpotBroker testnet (BROKER_SANDBOX=1)
Phase 4: CcxtSpotBroker mainnet (BROKER_SANDBOX=0 + LIVE_ARMED=1 + mandatory vars)
"""

from __future__ import annotations

import os
import sys


def make_broker() -> object:
    """Construct and return the configured Broker.

    Reads BROKER, BROKER_SANDBOX, LIVE_ARMED from the environment.
    Crashes with a descriptive message on any invalid / dangerous combination.
    """
    broker_name = os.getenv("BROKER", "paper").lower().strip()

    if broker_name == "paper":
        from .paper import PaperBroker  # noqa: PLC0415
        return PaperBroker()

    if broker_name == "bybit":
        # Phase 3+ — ccxt_spot broker is imported only when BROKER=bybit so
        # that Phase 0 never requires ccxt to be installed.
        try:
            from .ccxt_spot import CcxtSpotBroker  # noqa: PLC0415
        except ImportError as exc:
            sys.exit(
                f"[broker] BROKER=bybit but ccxt is not installed.\n"
                f"  Run: pip install ccxt\n"
                f"  Original error: {exc}"
            )

        sandbox_raw = os.getenv("BROKER_SANDBOX", "1").strip()
        sandbox = sandbox_raw not in {"0", "false", "no"}
        live_armed = os.getenv("LIVE_ARMED", "0").strip() in {"1", "true", "yes"}

        # Phase 4: validate all mainnet vars before allowing mainnet startup
        if not sandbox:
            CcxtSpotBroker.validate_mainnet_env()

        bybit_key = os.getenv("BYBIT_API_KEY", "").strip()
        bybit_secret = os.getenv("BYBIT_API_SECRET", "").strip()

        if not bybit_key or not bybit_secret:
            sys.exit(
                "[broker] BROKER=bybit requires BYBIT_API_KEY and BYBIT_API_SECRET "
                "in pipeline/.env — process refuses to start without credentials."
            )

        return CcxtSpotBroker(
            api_key=bybit_key,
            api_secret=bybit_secret,
            sandbox=sandbox,
            armed=live_armed,
        )

    sys.exit(
        f"[broker] Unknown BROKER value '{broker_name}'. "
        "Valid values: paper | bybit"
    )
