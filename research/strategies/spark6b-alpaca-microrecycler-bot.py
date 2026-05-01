"""Aggressive Spark-6B Alpaca quote-imbalance micro recycler variant.

This keeps spark6 intact as the disciplined recycler baseline and turns this file
into the hotter sibling for faster competition iteration.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_base_module():
    base_path = Path(__file__).resolve().with_name("spark6-alpaca-microrecycler-bot.py")
    spec = importlib.util.spec_from_file_location("spark6_base", base_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load base module from {base_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spark6 = load_base_module()

# Faster wake-up and much looser recycler thresholds.
spark6.POLL_SECONDS = 0.6
spark6.ENTRY_SPREAD_MAX = 0.00175
spark6.MIN_RANGE_PCT = 0.00007
spark6.MAX_RANGE_PCT = 0.00220
spark6.MAX_ABS_SLOW_MOMENTUM = 0.00060
spark6.MIN_BOUNCE_PCT = 0.00002
spark6.MAX_RANGE_POS = 0.48
spark6.MIN_DRIFT_PCT = 0.0
spark6.MIN_IMBALANCE = 1.08
spark6.MIN_SIGNAL_SCORE = 0.55
spark6.TP_PCT = 0.00028
spark6.SL_PCT = 0.00030
spark6.EXIT_DECAY_PCT = -0.00006
spark6.MAX_HOLD_SECONDS = 3.8
spark6.WIN_COOLDOWN_SECONDS = 0.25
spark6.LOSS_COOLDOWN_SECONDS = 1.8
spark6.BASE_SIZE_PCT = 0.36
spark6.MAX_SIZE_PCT = 0.56
spark6.MAX_ENTRY_SLIP_PCT = 0.00125

# Reset mutable runtime state for this variant's own run.
spark6.runtime["started_at"] = spark6.time.time()
spark6.runtime["last_trade_at"] = spark6.time.time()
spark6.runtime["entries"] = 0
spark6.runtime["closes"] = 0
spark6.runtime["no_fills"] = 0
spark6.runtime["status_tick"] = 0
spark6.runtime["last_account_refresh"] = 0.0
spark6.runtime["account_cache"] = None
spark6.runtime["position_state"] = None


if __name__ == "__main__":
    print("=" * 88)
    print("SPARK-6B ALPACA AGGRESSIVE MICRO RECYCLER")
    print("=" * 88)
    spark6.main()
