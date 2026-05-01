#!/usr/bin/env python3
"""
Spread Robustness Test — Are Steps Wide Enough to Survive Spread?

For each symbol:
1. Collect spread data from recent tick history
2. Compute p50, p90, p99 spread
3. Compare to current config step sizes
4. Flag configs where step < 2x p90 spread (spread-risk)

This is the @codex-profit-theory standard: "step sizing should beat p90 spread,
not just current spread. Otherwise we are optimizing to a screenshot."

Output: reports/spread_robustness.json
"""
import MetaTrader5 as mt5
import json
import os
import numpy as np
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Config Registry (current step sizes) ──────────────────────────────

CONFIG_STEPS = {
    "GBPUSD": {"step_buy": 0.00058, "step_sell": 0.00029, "atr": 0.000404, "config": "hungry_hippo_gbpusd_live"},
    "EURUSD": {"step_buy": 0.00043, "step_sell": 0.00029, "atr": 0.000286, "config": "hungry_hippo_eurusd_live"},
    "USDJPY": {"step_buy": 0.0783, "step_sell": 0.0783, "atr": 0.0391, "config": "hungry_hippo_usdjpy_live"},
    "NZDUSD": {"step_buy": 0.00052, "step_sell": 0.00052, "atr": 0.000258, "config": "hungry_hippo_nzdusd_live"},
    "NAS100": {"step_buy": 13.37, "step_sell": 28.58, "atr": 19.10, "config": "hungry_hippo_nas100_breakout_shadow"},
    "US30": {"step_buy": 54.62, "step_sell": 25.49, "atr": 36.67, "config": "hungry_hippo_us30_breakdown_shadow"},
    "BTCUSD": {"step_buy": 389.14, "step_sell": 129.71, "atr": 259.43, "config": "hungry_hippo_btcusd_m15_sell_tight_shadow"},
    "ETHUSD": {"step": 5.0, "atr": 8.65, "config": "hungry_hippo_ethusd_m5_step5_shadow"},
    "XAUUSD": {"step": 3.51, "atr": 11.70, "config": "hungry_hippo_xauusd_consolidation_shadow"},
}


def collect_spread_data(symbol: str, sample_count: int = 50, interval_ms: int = 100) -> list:
    """
    Collect spread data from MT5 live ticks.

    Since MT5 doesn't provide tick history, we poll symbol_info_tick
    repeatedly to build a spread sample over ~5 seconds per symbol.
    """
    mt5.initialize()

    spreads = []
    import time
    for i in range(sample_count):
        tick = mt5.symbol_info_tick(symbol)
        if tick is not None:
            spread = float(tick.ask) - float(tick.bid)
            spreads.append(spread)
        if i < sample_count - 1:
            time.sleep(interval_ms / 1000.0)

    mt5.shutdown()
    return spreads


def compute_spread_stats(spreads: list) -> dict:
    """Compute spread percentiles."""
    if len(spreads) == 0:
        return {"error": "no spread data"}

    arr = np.array(spreads)
    return {
        "current": round(float(arr[-1]), 6),
        "min": round(float(arr.min()), 6),
        "max": round(float(arr.max()), 6),
        "p50": round(float(np.percentile(arr, 50)), 6),
        "p90": round(float(np.percentile(arr, 90)), 6),
        "p99": round(float(np.percentile(arr, 99)), 6),
        "mean": round(float(arr.mean()), 6),
        "samples": len(arr),
    }


def check_spread_robustness(symbol: str, spread_stats: dict, config: dict) -> dict:
    """
    Check if config step sizes are robust against spread.

    Min viable step = 2× p90 spread (covers open + close spread cost)
    """
    if "error" in spread_stats:
        return {"status": "error", "message": spread_stats["error"]}

    p90 = spread_stats["p90"]
    min_viable_step = p90 * 2.0

    # Get effective step (use min of buy/sell for asymmetric configs)
    if "step" in config:
        effective_step = config["step"]
        step_label = f"step={config['step']}"
    elif "step_buy" in config and "step_sell" in config:
        effective_step = min(config["step_buy"], config["step_sell"])
        step_label = f"buy={config['step_buy']}, sell={config['step_sell']}"
    else:
        return {"status": "error", "message": "no step size found"}

    # Ratio of step to min viable
    if min_viable_step > 0:
        ratio = effective_step / min_viable_step
    else:
        ratio = float("inf")

    # Classification
    if ratio >= 3.0:
        status = "ROBUST"
        verdict = f"Step {effective_step:.6f} is {ratio:.1f}× min viable ({min_viable_step:.6f}) — safe"
    elif ratio >= 2.0:
        status = "ACCEPTABLE"
        verdict = f"Step {effective_step:.6f} is {ratio:.1f}× min viable ({min_viable_step:.6f}) — borderline safe"
    elif ratio >= 1.0:
        status = "SPREAD-RISK"
        verdict = f"Step {effective_step:.6f} is {ratio:.1f}× min viable ({min_viable_step:.6f}) — spread may eat profit"
    else:
        status = "SPREAD-LOSS"
        verdict = f"Step {effective_step:.6f} < min viable ({min_viable_step:.6f}) — LOSING on spread"

    return {
        "status": status,
        "verdict": verdict,
        "effective_step": effective_step,
        "min_viable_step": round(min_viable_step, 6),
        "ratio": round(ratio, 2),
        "spread_p50": spread_stats["p50"],
        "spread_p90": spread_stats["p90"],
        "spread_p99": spread_stats["p99"],
    }


def main():
    symbols = list(CONFIG_STEPS.keys())
    results = {}

    print(f"{'Symbol':<10} {'Status':<14} {'Step':<30} {'p50':>10} {'p90':>10} {'p99':>10} {'Ratio':>7} {'Verdict'}")
    print("-" * 140)

    for sym in symbols:
        config = CONFIG_STEPS[sym]
        spreads = collect_spread_data(sym, sample_count=50, interval_ms=100)
        stats = compute_spread_stats(spreads)
        check = check_spread_robustness(sym, stats, config)

        if "error" in check:
            print(f"{sym:<10} {'ERROR':<14} {config.get('config', 'N/A'):<30} {'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>7} {check['message']}")
            results[sym] = {"status": "error", "message": check["message"]}
            continue

        ratio_str = f"{check['ratio']:.1f}×"
        step_str = config.get("config", "unknown")
        verdict = check["verdict"][:50]

        print(f"{sym:<10} {check['status']:<14} {step_str:<30} {check['spread_p50']:>10.6f} {check['spread_p90']:>10.6f} {check['spread_p99']:>10.6f} {ratio_str:>7} {verdict}")

        results[sym] = {
            "status": check["status"],
            "verdict": check["verdict"],
            "effective_step": check["effective_step"],
            "min_viable_step": check["min_viable_step"],
            "ratio": check["ratio"],
            "spread_p50": check["spread_p50"],
            "spread_p90": check["spread_p90"],
            "spread_p99": check["spread_p99"],
            "samples": stats["samples"],
        }

    # Save report
    report_dir = Path(__file__).parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "spread_robustness.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {report_path}")

    # Summary
    statuses = {}
    for sym, data in results.items():
        status = data.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1

    print(f"\nSpread robustness summary:")
    for status, count in sorted(statuses.items(), key=lambda x: -x[1]):
        emoji = {"ROBUST": "✅", "ACCEPTABLE": "⚠️", "SPREAD-RISK": "🟡", "SPREAD-LOSS": "🔴", "error": "❌"}
        print(f"  {emoji.get(status, '?')} {status}: {count} symbols")


if __name__ == "__main__":
    main()
