"""Focused Spark-3B Alpaca impulse fork.

This keeps spark3 intact as the mixed-mode HFT lab and narrows this fork
to the impulse lane only so we can compare a pure burst-resume specialist
against the broader original.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_base_module():
    base_path = Path(__file__).resolve().with_name("spark3-alpaca-scalper-bot.py")
    spec = importlib.util.spec_from_file_location("spark3_base", base_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load base module from {base_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spark3 = load_base_module()

# Re-open the impulse lane a bit, but disable the other regimes.
spark3.IMPULSE_MIN_SCORE = 1.25
spark3.IMPULSE_MIN_SLOW_MOMENTUM = 0.00006
spark3.IMPULSE_MIN_PULLBACK_PCT = 0.0
spark3.IMPULSE_MAX_PULLBACK_PCT = 0.00028
spark3.TP_PCT = 0.00030
spark3.SL_PCT = 0.00026
spark3.MAX_HOLD_SECONDS = 5.0
spark3.WIN_COOLDOWN_SECONDS = 0.35
spark3.LOSS_COOLDOWN_SECONDS = 2.2
spark3.BASE_SIZE_PCT = 0.34
spark3.MAX_SIZE_PCT = 0.50
spark3.MAX_ENTRY_SLIP_PCT = 0.0010

base_choose_signal = spark3.choose_signal


def choose_signal(equity, quotes):
    signal = base_choose_signal(equity, quotes)
    if not signal:
        return None
    if signal.get("mode") != "impulse":
        return None
    return signal


spark3.choose_signal = choose_signal

# Reset mutable runtime state for this fork's own run.
spark3.runtime["started_at"] = spark3.time.time()
spark3.runtime["last_trade_at"] = spark3.time.time()
spark3.runtime["entries"] = 0
spark3.runtime["closes"] = 0
spark3.runtime["no_fills"] = 0
spark3.runtime["status_tick"] = 0
spark3.runtime["last_account_refresh"] = 0.0
spark3.runtime["account_cache"] = None
spark3.runtime["position_state"] = None


if __name__ == "__main__":
    print("=" * 88)
    print("SPARK-3B ALPACA IMPULSE SPECIALIST")
    print("=" * 88)
    spark3.main()
