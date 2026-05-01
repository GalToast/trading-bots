"""Spark-6E Alpaca recycler recovery lane.

Purpose:
- Trade the same quote-imbalance recycler family that has shown the lowest bleed.
- Shift from pure competition aggression into "recover from drawdown" mode.
- Favor cleaner entries, smaller sizing, and faster scratch exits so the bot can
  keep taking shots without donating the rest of a damaged small account.
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

# Recovery posture: slower than the hot recycler forks, but still awake.
spark6.SYMBOLS = [
    ("BTCUSD", "BTC/USD"),
    ("ETHUSD", "ETH/USD"),
]
spark6.POLL_SECONDS = 0.7
spark6.ENTRY_SPREAD_MAX = 0.00120
spark6.MIN_RANGE_PCT = 0.00008
spark6.MAX_RANGE_PCT = 0.00145
spark6.MAX_ABS_SLOW_MOMENTUM = 0.00034
spark6.MIN_BOUNCE_PCT = 0.00003
spark6.MAX_RANGE_POS = 0.30
spark6.MIN_DRIFT_PCT = 0.00001
spark6.MIN_IMBALANCE = 1.12
spark6.MIN_SIGNAL_SCORE = 0.68
spark6.TP_PCT = 0.00020
spark6.SL_PCT = 0.00024
spark6.EXIT_DECAY_PCT = -0.00001
spark6.MAX_HOLD_SECONDS = 2.4
spark6.WIN_COOLDOWN_SECONDS = 0.5
spark6.LOSS_COOLDOWN_SECONDS = 4.0
spark6.BASE_SIZE_PCT = 0.24
spark6.MAX_SIZE_PCT = 0.36
spark6.MAX_ENTRY_SLIP_PCT = 0.00085


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
    if held >= 0.9 and pnl_pct < -0.00005:
        return True, "SCRATCH", pnl_pct
    if held >= 1.4 and decay <= spark6.EXIT_DECAY_PCT and pnl_pct <= 0.00004:
        return True, "DECAY", pnl_pct
    if pnl_pct <= -spark6.SL_PCT:
        return True, "SL", pnl_pct
    if held >= spark6.MAX_HOLD_SECONDS:
        return True, "TIME", pnl_pct
    return False, "", pnl_pct


spark6.should_exit = should_exit

# Reset mutable runtime state for this fork's own run.
spark6.quote_tape = {
    data_symbol: spark6.deque(maxlen=spark6.QUOTE_WINDOW)
    for _, data_symbol in spark6.SYMBOLS
}
spark6.cooldowns = {trade_symbol: 0.0 for trade_symbol, _ in spark6.SYMBOLS}
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
    print("SPARK-6E ALPACA RECOVERY RECYCLER")
    print("=" * 88)
    spark6.main()
