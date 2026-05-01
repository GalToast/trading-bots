#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


RAVE_RSI_MR_BASELINE_PARAMS: dict[str, Any] = {
    "rsi_period": 3,
    "os_thresh": 30,
    "tp_pct": 25,
    "max_hold": 48,
    "sl_pct": 0,
}

FEE_TIERS: dict[str, float] = {
    "40bps": 0.0040,
    "25bps": 0.0025,
    "15bps": 0.0015,
    "10bps": 0.0010,
}

BUILTIN_FILL_MODELS: dict[str, dict[str, float]] = {
    "perfect": {"fill_prob": 1.0, "entry_slippage_bps": 0.0, "exit_slippage_bps": 0.0},
    "realistic": {"fill_prob": 0.75, "entry_slippage_bps": 100.0, "exit_slippage_bps": 20.0},
    "harsh": {"fill_prob": 0.50, "entry_slippage_bps": 100.0, "exit_slippage_bps": 100.0},
    "measured_forward": {"fill_prob": 1.0, "entry_slippage_bps": 6.2, "exit_slippage_bps": 0.0},
    "measured_forward_session_gated": {"fill_prob": 1.0, "entry_slippage_bps": 7.1, "exit_slippage_bps": 0.0},
}


def framework_execution_kwargs(model: dict[str, float]) -> dict[str, float]:
    return {
        "fill_probability": float(model["fill_prob"]),
        "latency_bars": 0,
        "entry_slippage_pct": float(model["entry_slippage_bps"]) / 100.0,
        "exit_slippage_pct": float(model["exit_slippage_bps"]) / 100.0,
    }
