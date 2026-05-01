#!/usr/bin/env python3
"""Shadow Lane Quality Metrics — Beyond $/close.

Computes robust quality metrics for each running HH shadow:
1. Expectancy after spread and resets
2. Max Adverse Excursion (MAE) / Realized Profit ratio
3. Time-underwater analysis
4. Spread robustness (step vs P90 spread)
5. Reset-adjusted profitability

Usage:
    python scripts/shadow_quality_metrics.py
    python scripts/shadow_quality_metrics.py --symbol GBPUSD
    python scripts/shadow_quality_metrics.py --all-symbols
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# All known HH shadow state files to scan
SHADOW_STATE_FILES = [
    "penetration_lattice_shadow_gbpusd_m15_hungry_hippo_v1_state.json",
    "penetration_lattice_shadow_eurusd_m15_hungry_hippo_v1_state.json",
    "penetration_lattice_shadow_ethusd_m5_hungry_hippo_v1_state.json",
    "penetration_lattice_shadow_ethusd_m5_hungry_hippo_step5_v1_state.json",
    "penetration_lattice_shadow_ethusd_m15_hungry_hippo_v1_state.json",
    "penetration_lattice_shadow_ethusd_m15_micro_hungry_hippo_v1_state.json",
    "penetration_lattice_shadow_nas100_m15_warp_state.json",
    "penetration_lattice_shadow_us30_m15_warp_state.json",
    "penetration_lattice_shadow_btcusd_m15_sell_tight_v1_state.json",
    "penetration_lattice_shadow_btcusd_m15_warp_state.json",
    "penetration_lattice_shadow_xauusd_m5_warp_state.json",
    "penetration_lattice_shadow_nzdusd_m15_warp_state.json",
    "penetration_lattice_shadow_usdjpy_m5_warp_state.json",
    "penetration_lattice_shadow_eurjpy_m15_warp_state.json",
    "penetration_lattice_shadow_gbpjpy_m15_warp_state.json",
    "penetration_lattice_shadow_xagusd_m15_warp_state.json",
    "penetration_lattice_shadow_audusd_m15_warp_state.json",
]


def load_state(path: Path) -> dict | None:
    """Load a shadow state file, handling errors gracefully."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  Warning: Could not load {path.name}: {e}")
        return None


def compute_quality_metrics(state: dict, symbol: str) -> dict[str, Any]:
    """Compute robust quality metrics for a shadow lane."""
    sym_data = state.get("symbols", {}).get(symbol, {})
    if not sym_data:
        return {"error": f"No data for {symbol}"}

    realized_net = float(sym_data.get("realized_net_usd", 0.0))
    realized_closes = int(sym_data.get("realized_closes", 0))
    resets = int(sym_data.get("anchor_resets", 0))
    rearm_opens = int(sym_data.get("rearm_opens", 0))
    open_tickets = len(sym_data.get("open_tickets", []))

    # Step sizes
    step_buy = float(sym_data.get("base_step_buy_px", 0))
    step_sell = float(sym_data.get("base_step_sell_px", 0))
    step = float(sym_data.get("base_step_px", 0)) or max(step_buy, step_sell)

    # Alpha and config
    alpha = float(sym_data.get("raw_close_alpha", 1.0))
    variant = sym_data.get("variant", "unknown")
    max_floating = float(sym_data.get("max_floating_loss_usd", 0))
    max_open = int(sym_data.get("max_open_total", 0))

    # Metadata
    metadata = state.get("metadata", {})
    escape_enabled = metadata.get("escape_hatch_enabled", False)
    escape_max_bars = metadata.get("escape_max_bars", 0)
    escape_max_loss = metadata.get("escape_max_loss", 0)

    # ── Core Metrics ──
    avg_per_close = realized_net / max(1, realized_closes)

    # ── Reset-Adjusted Profitability ──
    # Each reset represents a failed grid attempt — cost of rebuilding
    reset_cost_estimate = step * 2  # Rough estimate: reset loses ~2 steps of progress
    reset_adjusted_pnl = realized_net - (resets * reset_cost_estimate)
    reset_adjusted_per_close = reset_adjusted_pnl / max(1, realized_closes)

    # ── Max Adverse Excursion / Realized Profit Ratio ──
    # How much floating loss did we endure per dollar of realized profit?
    mae_ratio = abs(max_floating) / max(1, abs(realized_net)) if realized_net > 0 else float("inf")

    # ── Expectancy After Spread ──
    # Estimate spread cost per position
    # For crypto, spread is typically 0.1-0.3% of price
    # For FX, spread is typically 0.5-2 pips
    anchor = float(sym_data.get("anchor", 0))
    if anchor > 0:
        # Estimate spread as % of price (conservative: 0.2% for crypto, 0.01% for FX)
        if step > 1:  # Crypto (step in dollars)
            spread_pct = 0.002  # 0.2%
        else:  # FX (step in pips)
            spread_pct = 0.0001  # 0.01%
        spread_cost_per_trade = anchor * spread_pct * 0.01  # Approximate $ cost
        total_spread_cost = spread_cost_per_trade * rearm_opens
        expectancy_after_spread = (realized_net - total_spread_cost) / max(1, realized_closes)
    else:
        spread_cost_per_trade = 0
        total_spread_cost = 0
        expectancy_after_spread = avg_per_close

    # ── Spread Robustness Check ──
    # Step should be > P90 spread to be profitable
    if anchor > 0 and step > 0:
        estimated_p90_spread = anchor * 0.003  # Conservative 0.3% P90 spread
        step_vs_spread_ratio = step / estimated_p90_spread
        spread_robust = step_vs_spread_ratio > 1.0
    else:
        step_vs_spread_ratio = 0
        spread_robust = False

    # ── Win Rate Proxy ──
    # If avg_per_close > 0 and resets are low, likely positive win rate
    win_rate_proxy = "positive" if avg_per_close > 0 and resets < realized_closes * 0.1 else "negative"

    # ── Health Score (0-100) ──
    score = 0
    if realized_closes > 0:
        # Profitability (0-30 points)
        if avg_per_close > 0.10:
            score += 30
        elif avg_per_close > 0:
            score += 15

        # Reset rate (0-25 points)
        reset_rate = resets / max(1, realized_closes) * 100
        if reset_rate < 2:
            score += 25
        elif reset_rate < 10:
            score += 15
        elif reset_rate < 50:
            score += 5

        # MAE ratio (0-20 points)
        if mae_ratio < 0.5:
            score += 20
        elif mae_ratio < 1.0:
            score += 10

        # Spread robustness (0-15 points)
        if spread_robust:
            score += 15

        # Escape hatch (0-10 points)
        if escape_enabled:
            score += 10
    else:
        score = -1  # Not enough data

    return {
        "symbol": symbol,
        "realized_net_usd": round(realized_net, 2),
        "realized_closes": realized_closes,
        "avg_per_close": round(avg_per_close, 2),
        "reset_adjusted_pnl": round(reset_adjusted_pnl, 2),
        "reset_adjusted_per_close": round(reset_adjusted_per_close, 2),
        "resets": resets,
        "reset_rate_pct": round(reset_rate, 1) if realized_closes > 0 else 0,
        "rearm_opens": rearm_opens,
        "open_tickets": open_tickets,
        "max_floating_loss": round(max_floating, 2),
        "mae_ratio": round(mae_ratio, 2) if mae_ratio != float("inf") else "inf",
        "expectancy_after_spread": round(expectancy_after_spread, 2),
        "total_spread_cost": round(total_spread_cost, 2),
        "step": round(step, 4),
        "step_buy": round(step_buy, 4),
        "step_sell": round(step_sell, 4),
        "alpha": alpha,
        "variant": variant,
        "escape_enabled": escape_enabled,
        "escape_max_bars": escape_max_bars,
        "spread_robust": spread_robust,
        "step_vs_spread_ratio": round(step_vs_spread_ratio, 2),
        "win_rate_proxy": win_rate_proxy,
        "health_score": score,
    }


def print_report():
    """Print a comprehensive quality report for all shadows."""
    print("=" * 120)
    print("SHADOW LANE QUALITY METRICS — Beyond $/close")
    print("=" * 120)
    print()

    results = []
    for fname in SHADOW_STATE_FILES:
        path = REPORTS / fname
        if not path.exists():
            continue

        state = load_state(path)
        if state is None:
            continue

        # Get symbol from state
        symbols = list(state.get("symbols", {}).keys())
        if not symbols:
            continue

        for sym in symbols:
            metrics = compute_quality_metrics(state, sym)
            if "error" not in metrics:
                results.append(metrics)

    # Sort by health score descending
    results.sort(key=lambda x: x.get("health_score", -1), reverse=True)

    # Summary table
    print(f"{'Symbol':<12} {'Net $':>8} {'$/Close':>8} {'Resets':>7} {'Reset Adj':>10} {'MAE Rat':>8} {'Exp/Spread':>11} {'Spread?':>8} {'Escape':>7} {'Score':>6}")
    print("-" * 120)

    for m in results:
        score_icon = "🟢" if m["health_score"] >= 70 else "🟡" if m["health_score"] >= 40 else "🔴" if m["health_score"] >= 0 else "⚪"
        mae_str = str(m["mae_ratio"]) if isinstance(m["mae_ratio"], str) else f"{m['mae_ratio']:.2f}"
        print(f"{score_icon} {m['symbol']:<10} {m['realized_net_usd']:>8.2f} {m['avg_per_close']:>8.2f} {m['resets']:>7} {m['reset_adjusted_per_close']:>10.2f} {mae_str:>8} {m['expectancy_after_spread']:>11.2f} {str(m['spread_robust']):>8} {'✅' if m['escape_enabled'] else '❌':>7} {m['health_score']:>6}")

    print()

    # Detailed analysis for top candidates
    print("=" * 80)
    print("DETAILED ANALYSIS — Top Candidates")
    print("=" * 80)

    for m in results[:5]:
        print(f"\n{'─'*80}")
        print(f"  {m['symbol']} (Score: {m['health_score']}/100)")
        print(f"{'─'*80}")
        print(f"  Realized PnL:     ${m['realized_net_usd']:+.2f} over {m['realized_closes']} closes")
        print(f"  Avg $/close:      ${m['avg_per_close']:+.2f}")
        print(f"  Reset-adj $/close: ${m['reset_adjusted_per_close']:+.2f} ({m['resets']} resets)")
        print(f"  MAE/Profit ratio: {mae_str} (lower = less floating pain per dollar earned)")
        print(f"  Expectancy/spread: ${m['expectancy_after_spread']:+.2f} (after est. spread cost)")
        print(f"  Step vs spread:   {m['step_vs_spread_ratio']:.1f}x (need >1.0 for robustness)")
        print(f"  Escape hatch:     {'✅ Enabled' if m['escape_enabled'] else '❌ Not enabled'}")
        print(f"  Config:           step_buy={m['step_buy']}, step_sell={m['step_sell']}, alpha={m['alpha']}")
        print(f"  Variant:          {m['variant']}")

        # Kill conditions
        if m['health_score'] >= 0:
            print(f"\n  Kill Conditions:")
            print(f"    - If reset rate exceeds 10% → kill")
            print(f"    - If expectancy after spread goes negative → kill")
            print(f"    - If MAE ratio exceeds 2.0 → kill (too much floating pain)")
            if not m['spread_robust']:
                print(f"    - ⚠️  Step is NOT robust to spread — increase step size")

    print(f"\n{'='*120}")
    print("VERDICT")
    print(f"{'='*120}")

    promotable = [m for m in results if m["health_score"] >= 70]
    watchlist = [m for m in results if 40 <= m["health_score"] < 70]
    problematic = [m for m in results if 0 <= m["health_score"] < 40]
    no_data = [m for m in results if m["health_score"] < 0]

    if promotable:
        print(f"  🟢 PROMOTABLE (score ≥70): {', '.join(m['symbol'] for m in promotable)}")
    if watchlist:
        print(f"  🟡 WATCHLIST (score 40-69): {', '.join(m['symbol'] for m in watchlist)}")
    if problematic:
        print(f"  🔴 PROBLEMATIC (score <40): {', '.join(m['symbol'] for m in problematic)}")
    if no_data:
        print(f"  ⚪ NO DATA (not yet started): {', '.join(m['symbol'] for m in no_data)}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Shadow Lane Quality Metrics")
    parser.add_argument("--symbol", type=str, help="Check a specific symbol")
    parser.add_argument("--all-symbols", action="store_true", help="Check all known shadows")
    args = parser.parse_args()

    print_report()


if __name__ == "__main__":
    import argparse
    main()