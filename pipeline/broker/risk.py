"""Risk caps and kill switch for live trading.

Reads env variables and enforces:
  - LIVE_MAX_NOTIONAL_USDT  — per-trade size cap
  - LIVE_DAILY_LOSS_USDT    — session loss trip
  - LIVE_KILL_SWITCH=1      — env-level halt
  - pipeline/KILL file      — filesystem-level halt

Phase 4: daily loss tripping kills all new entries.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_KILL_FILE = Path(__file__).parent / "KILL"

# Session start equity (set by CcxtSpotBroker at startup)
_session_start_equity: float | None = None
_kill_active: bool = False


def set_session_start_equity(equity: float) -> None:
    global _session_start_equity
    _session_start_equity = equity


def is_kill_active() -> bool:
    """Return True if the kill switch is armed (env, file, or daily loss trip)."""
    global _kill_active
    if _kill_active:
        return True
    if os.getenv("LIVE_KILL_SWITCH", "0").strip() in {"1", "true"}:
        _kill_active = True
        return True
    if _KILL_FILE.exists():
        _kill_active = True
        return True
    return False


def arm_kill_switch(reason: str = "unknown") -> None:
    """Arm the kill switch programmatically (e.g. daily loss trip)."""
    global _kill_active
    _kill_active = True
    # Create sentinel file so the state survives env re-reads
    _KILL_FILE.write_text(f"kill armed at {time.time()} reason={reason}\n")


def check_daily_loss(current_equity: float) -> bool:
    """Return True if daily loss limit has been exceeded and kill switch was armed."""
    global _session_start_equity
    if _session_start_equity is None:
        return False
    limit_str = os.getenv("LIVE_DAILY_LOSS_USDT", "").strip()
    if not limit_str:
        return False
    try:
        limit = float(limit_str)
    except ValueError:
        return False
    loss = _session_start_equity - current_equity
    if loss >= limit:
        arm_kill_switch(reason=f"daily_loss_exceeded_loss={loss:.2f}_limit={limit:.2f}")
        return True
    return False


def max_notional() -> float | None:
    """Return LIVE_MAX_NOTIONAL_USDT as a float, or None if not set."""
    val = os.getenv("LIVE_MAX_NOTIONAL_USDT", "").strip()
    try:
        return float(val) if val else None
    except ValueError:
        return None


def is_armed() -> bool:
    """Return True only if LIVE_ARMED=1 is set."""
    return os.getenv("LIVE_ARMED", "0").strip() in {"1", "true"}


def live_symbols() -> list[str]:
    """Return the list of app symbol ids allowed for live trading."""
    raw = os.getenv("LIVE_SYMBOLS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def allow_aggressive() -> bool:
    return os.getenv("LIVE_ALLOW_AGGRESSIVE", "0").strip() in {"1", "true"}
