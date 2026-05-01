"""Focused Spark-6C Alpaca recycler contender.

This keeps spark6/spark6b intact while giving the recycler family a different
exit posture: more room for the snapback, but quicker scratch behavior when
the bounce stalls immediately.
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

# Use the live spark6b-style aggressive recycler entry posture.
spark6.POLL_SECONDS = 0.6
spark6.ENTRY_SPREAD_MAX = 0.00175
spark6.MIN_RANGE_PCT = 0.00007
spark6.MAX_RANGE_PCT = 0.00220
spark6.MAX_ABS_SLOW_MOMENTUM = 0.00060
spark6.MIN_BOUNCE_PCT = 0.0
spark6.MAX_RANGE_POS = 0.48
spark6.MIN_DRIFT_PCT = -0.00001
spark6.MIN_IMBALANCE = 1.0
spark6.MIN_SIGNAL_SCORE = 0.42
spark6.BASE_SIZE_PCT = 0.36
spark6.MAX_SIZE_PCT = 0.56
spark6.MAX_ENTRY_SLIP_PCT = 0.00125

# Exit logic is the real experiment here.
spark6.TP_PCT = 0.00024
spark6.SL_PCT = 0.00036
spark6.EXIT_DECAY_PCT = -0.00002
spark6.MAX_HOLD_SECONDS = 3.0
spark6.WIN_COOLDOWN_SECONDS = 0.25
spark6.LOSS_COOLDOWN_SECONDS = 1.4


def should_exit(position, quote):
    if not quote:
        return False, "NO_QUOTE", 0.0

    current = quote["mid"]
    pnl_pct = (current - position["entry"]) / position["entry"]
    held = spark6.time.time() - position["opened_at"]
    tape = spark6.quote_tape[position["data_symbol"]]
    decay = 0.0
    if len(tape) >= spark6.FAST_LOOKBACK + 1:
        decay_base = tape[-1 - spark6.FAST_LOOKBACK]["mid"]
        decay = (current - decay_base) / decay_base

    if pnl_pct >= spark6.TP_PCT:
        return True, "TP", pnl_pct
    if pnl_pct <= -spark6.SL_PCT:
        return True, "SL", pnl_pct
    if held >= 1.2 and pnl_pct < -0.00008:
        return True, "SCRATCH", pnl_pct
    if held >= 1.3 and decay <= spark6.EXIT_DECAY_PCT:
        return True, "DECAY", pnl_pct
    if held >= spark6.MAX_HOLD_SECONDS:
        return True, "TIME", pnl_pct
    return False, "", pnl_pct


spark6.should_exit = should_exit

# Reset mutable runtime state for this fork's own run.
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
    print("SPARK-6C ALPACA MICRO RECYCLER CONTENDER")
    print("=" * 88)
    spark6.main()
