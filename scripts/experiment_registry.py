#!/usr/bin/env python3
"""
Experiment Registry — Centralized tracker for all strategy × coin tests.

Prevents redundant testing, tracks evidence classes, and maintains
the frontier of best-known strategy per coin.

Usage:
    python scripts/experiment_registry.py --load      # Load all known results
    python scripts/experiment_registry.py --summary    # Print summary
    python scripts/experiment_registry.py --frontier   # Print best per coin
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "reports" / "experiment_registry.json"

# All verified results from the session
VERIFIED_RESULTS = [
    # Momentum Breakout (strategy_library.py, 30d, $100 start)
    {"coin": "MOG-USD", "strategy": "momentum", "params": {"lookback": 10, "tp_pct": 15, "sl_pct": 0}, "net_pnl": 117.80, "win_rate": 52.5, "trades": 59, "max_dd": 27.7, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},
    {"coin": "NOM-USD", "strategy": "momentum", "params": {"lookback": 30, "tp_pct": 8, "sl_pct": 8, "max_hold": 12}, "net_pnl": 2469.84, "win_rate": 68.3, "trades": 120, "max_dd": 16.7, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},
    {"coin": "RAVE-USD", "strategy": "momentum", "params": {"lookback": 15, "tp_pct": 10, "sl_pct": 0}, "net_pnl": 2258.89, "win_rate": 72.0, "trades": 82, "max_dd": 18.9, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},
    {"coin": "GHST-USD", "strategy": "momentum", "params": {"lookback": 20, "tp_pct": 15, "sl_pct": 3}, "net_pnl": 1167.98, "win_rate": 46.5, "trades": 71, "max_dd": 33.4, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},
    {"coin": "TRU-USD", "strategy": "momentum", "params": {"lookback": 10, "tp_pct": 10, "sl_pct": 3}, "net_pnl": 510.97, "win_rate": 51.3, "trades": 78, "max_dd": 26.5, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},
    {"coin": "SUP-USD", "strategy": "momentum", "params": {"lookback": 25, "tp_pct": 15, "sl_pct": 3}, "net_pnl": 137.31, "win_rate": 40.5, "trades": 37, "max_dd": 19.3, "evidence": "claimed_and_optimized_positive", "tested_by": "qwen-trading-bots"},
    {"coin": "A8-USD", "strategy": "momentum", "params": {"lookback": 10, "tp_pct": 15, "sl_pct": 0}, "net_pnl": 117.80, "win_rate": 52.5, "trades": 59, "max_dd": 27.7, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},
    {"coin": "BAL-USD", "strategy": "momentum", "params": {"lookback": 50, "tp_pct": 8, "sl_pct": 3}, "net_pnl": 83.74, "win_rate": 59.3, "trades": 27, "max_dd": 14.9, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},
    {"coin": "IOTX-USD", "strategy": "momentum", "params": {"lookback": 25, "tp_pct": 5, "sl_pct": 2}, "net_pnl": 46.27, "win_rate": 56.3, "trades": 71, "max_dd": 21.3, "evidence": "claimed_param_confirmed", "tested_by": "qwen-main"},

    # RSI Mean Reversion
    {"coin": "MOG-USD", "strategy": "rsi_mr", "params": {"rsi_period": 4, "os_thresh": 45, "tp_pct": 7.5, "sl_pct": 0.5}, "net_pnl": 3288.81, "win_rate": 36.1, "trades": 244, "max_dd": 24.8, "evidence": "claimed_param_confirmed", "tested_by": "qwen-trading-bots"},

    # Range Breakout (from @codex-spot-scout's sweep)
    {"coin": "NOM-USD", "strategy": "range_breakout", "params": {"lookback": 10, "tp_pct": 10, "sl_pct": 1, "max_hold": 24}, "net_pnl": 2639.00, "win_rate": 37.7, "trades": 231, "max_dd": 27.2, "evidence": "claimed_param_confirmed", "tested_by": "codex-spot-scout"},
    {"coin": "SUP-USD", "strategy": "range_breakout", "params": {"lookback": 8, "tp_pct": 8, "sl_pct": 1, "max_hold": 24}, "net_pnl": 188.39, "win_rate": 44.9, "trades": 89, "max_dd": 12.3, "evidence": "claimed_param_confirmed", "tested_by": "codex-spot-scout"},
    {"coin": "PRL-USD", "strategy": "range_breakout", "params": {"lookback": 25, "tp_pct": 10, "sl_pct": 1, "max_hold": 36}, "net_pnl": 67.45, "win_rate": 33.7, "trades": 104, "max_dd": 23.4, "evidence": "claimed_param_confirmed", "tested_by": "codex-spot-scout"},
    {"coin": "BAL-USD", "strategy": "range_breakout", "params": {"lookback": 50, "tp_pct": 10, "sl_pct": 3, "max_hold": 36}, "net_pnl": 47.16, "win_rate": 56.7, "trades": 30, "max_dd": 10.7, "evidence": "claimed_param_confirmed", "tested_by": "codex-spot-scout"},

    # Opening Range Breakout (from @codex-strategy-500's focused 30d sweep)
    {"coin": "NOM-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 6, "breakout_buffer_pct": 0.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 18}, "net_pnl": 1881.68, "win_rate": 57.6, "trades": 276, "max_dd": 21.0, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "GHST-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 6, "breakout_buffer_pct": 0.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 18}, "net_pnl": 1067.94, "win_rate": 60.0, "trades": 210, "max_dd": 20.5, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "RAVE-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 12, "breakout_buffer_pct": 0.3, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 30}, "net_pnl": 586.34, "win_rate": 65.5, "trades": 110, "max_dd": 24.2, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "TRU-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 12, "breakout_buffer_pct": 0.3, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 30}, "net_pnl": 309.36, "win_rate": 56.0, "trades": 84, "max_dd": 29.0, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "A8-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 6, "breakout_buffer_pct": 0.2, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24}, "net_pnl": 134.06, "win_rate": 56.2, "trades": 96, "max_dd": 17.0, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "PRL-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 6, "breakout_buffer_pct": 0.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 18}, "net_pnl": 65.53, "win_rate": 49.0, "trades": 194, "max_dd": 28.7, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "SUP-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 6, "breakout_buffer_pct": 0.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 18}, "net_pnl": 61.85, "win_rate": 53.6, "trades": 97, "max_dd": 25.2, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "BAL-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 12, "breakout_buffer_pct": 0.3, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 30}, "net_pnl": 39.08, "win_rate": 45.8, "trades": 48, "max_dd": 21.1, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "CFG-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 6, "breakout_buffer_pct": 0.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 18}, "net_pnl": 16.90, "win_rate": 48.3, "trades": 176, "max_dd": 42.8, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "IOTX-USD", "strategy": "opening_range_breakout", "params": {"opening_bars": 12, "breakout_buffer_pct": 0.3, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 30}, "net_pnl": 13.41, "win_rate": 42.4, "trades": 59, "max_dd": 32.0, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},

    # Regime-Gated Momentum (from @codex-strategy-500's focused 30d sweep)
    {"coin": "NOM-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 12, "ema_period": 40, "atr_period": 14, "trend_lookback": 10, "min_atr_pct": 1.0, "min_trend_pct": 1.0, "min_ema_slope_pct": 0.03, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24}, "net_pnl": 806.39, "win_rate": 62.6, "trades": 115, "max_dd": 17.7, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "GHST-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 10, "ema_period": 30, "atr_period": 10, "trend_lookback": 8, "min_atr_pct": 0.8, "min_trend_pct": 0.8, "min_ema_slope_pct": 0.02, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 18}, "net_pnl": 427.78, "win_rate": 62.5, "trades": 96, "max_dd": 24.7, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "RAVE-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 10, "ema_period": 30, "atr_period": 10, "trend_lookback": 8, "min_atr_pct": 0.8, "min_trend_pct": 0.8, "min_ema_slope_pct": 0.02, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 18}, "net_pnl": 187.39, "win_rate": 60.5, "trades": 76, "max_dd": 12.2, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "TRU-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 20, "ema_period": 50, "atr_period": 14, "trend_lookback": 12, "min_atr_pct": 1.2, "min_trend_pct": 1.2, "min_ema_slope_pct": 0.04, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 24}, "net_pnl": 166.11, "win_rate": 63.6, "trades": 44, "max_dd": 21.1, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "SUP-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 10, "ema_period": 30, "atr_period": 10, "trend_lookback": 8, "min_atr_pct": 0.8, "min_trend_pct": 0.8, "min_ema_slope_pct": 0.02, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 18}, "net_pnl": 39.30, "win_rate": 54.1, "trades": 37, "max_dd": 17.7, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "CFG-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 12, "ema_period": 40, "atr_period": 14, "trend_lookback": 10, "min_atr_pct": 1.0, "min_trend_pct": 1.0, "min_ema_slope_pct": 0.03, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24}, "net_pnl": 22.08, "win_rate": 58.1, "trades": 31, "max_dd": 17.8, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "A8-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 10, "ema_period": 30, "atr_period": 10, "trend_lookback": 8, "min_atr_pct": 0.8, "min_trend_pct": 0.8, "min_ema_slope_pct": 0.02, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 18}, "net_pnl": 18.13, "win_rate": 48.8, "trades": 82, "max_dd": 31.5, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "PRL-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 12, "ema_period": 40, "atr_period": 14, "trend_lookback": 10, "min_atr_pct": 1.0, "min_trend_pct": 1.0, "min_ema_slope_pct": 0.03, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24}, "net_pnl": 18.07, "win_rate": 48.9, "trades": 47, "max_dd": 22.0, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "IOTX-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 20, "ema_period": 50, "atr_period": 14, "trend_lookback": 12, "min_atr_pct": 1.2, "min_trend_pct": 1.2, "min_ema_slope_pct": 0.04, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 24}, "net_pnl": 12.31, "win_rate": 42.9, "trades": 28, "max_dd": 29.1, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},
    {"coin": "BAL-USD", "strategy": "regime_gated_momentum", "params": {"lookback": 20, "ema_period": 50, "atr_period": 14, "trend_lookback": 12, "min_atr_pct": 1.2, "min_trend_pct": 1.2, "min_ema_slope_pct": 0.04, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 24}, "net_pnl": 9.99, "win_rate": 50.0, "trades": 14, "max_dd": 16.3, "evidence": "optimize_only", "tested_by": "codex-strategy-500"},

    # Novel Param Explorations
    {"coin": "NOM-USD", "strategy": "momentum", "params": {"lookback": 30, "tp_pct": 8, "sl_pct": 1, "max_hold": 60}, "net_pnl": 3471.50, "win_rate": 41.1, "trades": 146, "max_dd": 22.4, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},
    {"coin": "RAVE-USD", "strategy": "momentum", "params": {"lookback": 20, "tp_pct": 25, "sl_pct": 0, "max_hold": 96}, "net_pnl": 1925.86, "win_rate": 72.5, "trades": 40, "max_dd": 16.5, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},
    {"coin": "GHST-USD", "strategy": "momentum", "params": {"lookback": 20, "tp_pct": 10, "sl_pct": 1, "max_hold": 48}, "net_pnl": 1059.27, "win_rate": 42.6, "trades": 94, "max_dd": 15.3, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},
    {"coin": "TRU-USD", "strategy": "momentum", "params": {"lookback": 20, "tp_pct": 10, "sl_pct": 1, "max_hold": 48}, "net_pnl": 505.00, "win_rate": 41.4, "trades": 70, "max_dd": 12.1, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},
    {"coin": "MOG-USD", "strategy": "momentum", "params": {"lookback": 5, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}, "net_pnl": 79.02, "win_rate": 79.2, "trades": 24, "max_dd": 7.4, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},
    {"coin": "SUP-USD", "strategy": "momentum", "params": {"lookback": 20, "tp_pct": 10, "sl_pct": 1, "max_hold": 48}, "net_pnl": 164.93, "win_rate": 40.4, "trades": 47, "max_dd": 10.3, "evidence": "optimize_only", "tested_by": "qwen-trading-bots"},

    # Volatility (from @codex-spot-scout's verification)
    {"coin": "MULTI", "strategy": "atr_expansion", "params": {"default": True}, "net_pnl": 61.95, "win_rate": 0, "trades": 0, "max_dd": 0, "evidence": "frontier_positive", "tested_by": "codex-spot-scout"},
    {"coin": "MULTI", "strategy": "keltner_breakout", "params": {"default": True}, "net_pnl": -50, "win_rate": 0, "trades": 0, "max_dd": 0, "evidence": "frontier_negative", "tested_by": "codex-spot-scout"},
    {"coin": "MULTI", "strategy": "hist_vol_squeeze", "params": {"default": True}, "net_pnl": -10, "win_rate": 0, "trades": 0, "max_dd": 0, "evidence": "frontier_negative", "tested_by": "codex-spot-scout"},

    # BB Reversion (DISPROVEN)
    {"coin": "IOTX-USD", "strategy": "bb_reversion", "params": {"bb_period": 20, "rsi_period": 3, "rsi_thresh": 30, "proximity_pct": 3.0, "sl_pct": 5, "max_hold": 24}, "net_pnl": -75.97, "win_rate": 27.0, "trades": 163, "max_dd": 76.0, "evidence": "disproven", "tested_by": "qwen-trading-bots"},
    {"coin": "RAVE-USD", "strategy": "bb_reversion", "params": {"bb_period": 20, "rsi_period": 3, "rsi_thresh": 30, "proximity_pct": 3.0, "sl_pct": 5, "max_hold": 24}, "net_pnl": -90.00, "win_rate": 11.2, "trades": 170, "max_dd": 90.0, "evidence": "disproven", "tested_by": "qwen-trading-bots"},
]

UNTESTED_STRATEGIES = [
    "volume_spike_reversion",
    "vwap_reversion",
    "multi_tf_rsi",
    "overnight_gap",
    "ema_pullback",
]

EVIDENCE_CLASSES = {
    "claimed_param_confirmed": "Tested at claimed params, passed on 30d data",
    "optimize_only": "Only profitable after param optimization",
    "claimed_and_optimized_positive": "Works at both claimed and optimized params",
    "frontier_positive": "Positive on aggregate across multiple coins",
    "frontier_negative": "Negative on aggregate across multiple coins",
    "disproven": "Tested and failed on 30d data through strategy library",
}


def build_registry():
    registry = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_experiments": len(VERIFIED_RESULTS),
        "unique_strategies": list(set(r["strategy"] for r in VERIFIED_RESULTS)),
        "unique_coins": list(set(r["coin"] for r in VERIFIED_RESULTS if r["coin"] != "MULTI")),
        "untested_strategies": UNTESTED_STRATEGIES,
        "evidence_classes": EVIDENCE_CLASSES,
        "results": VERIFIED_RESULTS,
        "frontier": {},  # Best strategy per coin
    }

    # Build frontier
    coin_best = {}
    for r in VERIFIED_RESULTS:
        if r["coin"] == "MULTI" or r["net_pnl"] <= 0:
            continue
        coin = r["coin"]
        if coin not in coin_best or r["net_pnl"] > coin_best[coin]["net_pnl"]:
            coin_best[coin] = r

    registry["frontier"] = {
        coin: {
            "strategy": r["strategy"],
            "params": r["params"],
            "net_pnl": r["net_pnl"],
            "win_rate": r["win_rate"],
            "trades": r["trades"],
            "max_dd": r["max_dd"],
            "evidence": r["evidence"],
            "tested_by": r["tested_by"],
        }
        for coin, r in sorted(coin_best.items(), key=lambda x: x[1]["net_pnl"], reverse=True)
    }

    return registry


def print_summary(registry):
    print(f"\n{'='*80}")
    print(f"EXPERIMENT REGISTRY SUMMARY")
    print(f"{'='*80}")
    print(f"Total experiments: {registry['total_experiments']}")
    print(f"Unique strategies: {len(registry['unique_strategies'])}")
    print(f"Unique coins: {len(registry['unique_coins'])}")
    print(f"Untested strategies: {len(registry['untested_strategies'])}")
    print(f"Strategies: {', '.join(registry['unique_strategies'])}")
    print(f"Untested: {', '.join(registry['untested_strategies'])}")

    print(f"\n{'='*80}")
    print(f"STRATEGY FRONTIER (best per coin)")
    print(f"{'='*80}")
    print(f"{'#':<3} {'Coin':<15} {'Strategy':<20} {'Net/mo':<10} {'WR%':<6} {'Trades':<7} {'DD%':<6} {'Evidence':<25}")
    print("-" * 95)
    for i, (coin, data) in enumerate(registry["frontier"].items()):
        print(f"{i+1:<3} {coin:<15} {data['strategy']:<20} ${data['net_pnl']:<9.2f} {data['win_rate']:<6.1f} {data['trades']:<7} {data['max_dd']:<6.1f} {data['evidence']:<25}")

    total = sum(d["net_pnl"] for d in registry["frontier"].values())
    print(f"\nFrontier total: ${total:+.2f}/month on {len(registry['frontier'])} coins")


def main():
    registry = build_registry()

    # Save
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2, default=str)

    print_summary(registry)
    print(f"\nRegistry saved: {REGISTRY_PATH}")


if __name__ == "__main__":
    main()
