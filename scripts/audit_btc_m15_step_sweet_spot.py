#!/usr/bin/env python3
"""
BTC M15 Step Sweet-Spot Audit
Simulates the penetration lattice at different step sizes on historical M15 bars.
Finds the step that maximizes total profit while keeping resets survivable.
"""
import argparse
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


@dataclass
class LatticeResult:
    step: float
    closes: int = 0
    net: float = 0.0
    avg_per_close: float = 0.0
    resets: int = 0
    max_open: int = 0
    max_floating: float = 0.0
    win_rate: float = 0.0
    wins: int = 0
    losses: int = 0


def simulate_lattice_on_bars(
    bars: list[dict],
    step: float,
    max_open: int = 60,
    max_floating_loss: float = 3500.0,
    rearm_variant: str = "rearm_lvl2_exc1",
    anchor_reset_threshold: float = 4.0,
) -> LatticeResult:
    """Replay bars through a simplified penetration lattice model."""
    result = LatticeResult(step=step)

    if len(bars) < 50:
        return result

    anchor = bars[0]["close"]
    next_sell = anchor + step
    next_buy = anchor - step

    open_positions: list[dict] = []
    realized = 0.0
    wins = 0
    losses = 0
    resets = 0
    current_anchor = anchor
    anchor_level_buy = current_anchor - step
    anchor_level_sell = current_anchor + step

    for bar in bars:
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]

        # Check for anchor reset (price moved far beyond anchor)
        if bar_high > current_anchor + anchor_reset_threshold * step:
            # Anchor reset - close all positions at anchor
            for pos in open_positions:
                realized += (current_anchor - pos["entry"]) * pos["direction_mult"]
            open_positions.clear()
            resets += 1
            current_anchor = bar_close
            next_sell = current_anchor + step
            next_buy = current_anchor - step

        # BUY entries: price dips to buy level
        while bar_low <= next_buy and len([p for p in open_positions if p["direction"] == "BUY"]) < max_open:
            open_positions.append({"direction": "BUY", "entry": next_buy, "direction_mult": 1.0})
            next_buy -= step
            if len(open_positions) > result.max_open:
                result.max_open = len(open_positions)

        # SELL entries: price rises to sell level
        while bar_high >= next_sell and len([p for p in open_positions if p["direction"] == "SELL"]) < max_open:
            open_positions.append({"direction": "SELL", "entry": next_sell, "direction_mult": -1.0})
            next_sell += step
            if len(open_positions) > result.max_open:
                result.max_open = len(open_positions)

        # BUY exits: price rises back to entry + step (penetration close)
        new_positions = []
        for pos in open_positions:
            if pos["direction"] == "BUY" and bar_high >= pos["entry"] + step:
                pnl = (pos["entry"] + step - pos["entry"]) * pos["direction_mult"]
                realized += pnl
                result.closes += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
            elif pos["direction"] == "SELL" and bar_low <= pos["entry"] - step:
                pnl = (pos["entry"] - step - pos["entry"]) * pos["direction_mult"]
                realized += pnl
                result.closes += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
            else:
                new_positions.append(pos)
        open_positions = new_positions

        # Track floating PnL
        floating = 0.0
        for pos in open_positions:
            if pos["direction"] == "BUY":
                floating += (bar_close - pos["entry"]) * pos["direction_mult"]
            else:
                floating += (bar_close - pos["entry"]) * pos["direction_mult"]
        result.max_floating = min(result.max_floating, floating)

    result.net = realized
    result.wins = wins
    result.losses = losses
    result.resets = resets
    if result.closes > 0:
        result.avg_per_close = realized / result.closes
        result.win_rate = wins / result.closes * 100
    return result


def fetch_btc_m15_bars(count: int = 10000) -> list[dict]:
    """Fetch BTCUSD M15 bars from MT5."""
    if not mt5.initialize():
        print("MT5 initialize failed, trying to load from cached bars file")
        cached = REPORTS / "btc_m15_bars_cache.json"
        if cached.exists():
            return json.loads(cached.read_text())
        return []

    rates = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, count)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print(f"No bars fetched from MT5")
        return []

    bars = []
    for r in rates:
        bars.append({
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        })

    # Cache for future runs
    cached = REPORTS / "btc_m15_bars_cache.json"
    cached.write_text(json.dumps(bars, indent=2))
    print(f"Fetched {len(bars)} M15 bars, cached to {cached}")
    return bars


def main():
    parser = argparse.ArgumentParser(description="BTC M15 Step Sweet-Spot Audit")
    parser.add_argument("--bars", type=int, default=10000, help="Number of M15 bars to fetch")
    parser.add_argument("--steps", type=str, default="15,20,30,50,75,100,150,200", help="Comma-separated step sizes")
    parser.add_argument("--max-open", type=int, default=60)
    args = parser.parse_args()

    steps = [float(x) for x in args.steps.split(",")]

    print("Fetching BTCUSD M15 bars...")
    bars = fetch_btc_m15_bars(args.bars)
    if not bars:
        print("ERROR: No bars available. Cannot run audit.")
        return 1

    # Also reverse bars to simulate from oldest to newest
    bars = bars[::-1]  # Oldest first
    print(f"Simulating {len(bars)} bars ({len(bars) * 15 / 60:.0f} hours of data)")
    print()

    results: list[LatticeResult] = []
    for step in steps:
        r = simulate_lattice_on_bars(bars, step, max_open=args.max_open)
        results.append(r)

    # Print results
    print(f"{'='*90}")
    print(f"{'Step':>6} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'Resets':>6} | {'WR%':>6} | {'MaxOpen':>7} | {'MaxFloat':>10}")
    print(f"{'-'*90}")
    for r in results:
        print(f"${r.step:>5.0f} | {r.closes:>6} | ${r.avg_per_close:>7.2f} | ${r.net:>9.2f} | {r.resets:>6} | {r.win_rate:>5.1f}% | {r.max_open:>7} | ${r.max_floating:>9.2f}")
    print(f"{'='*90}")

    # Compute $/hour for each step
    total_hours = len(bars) * 15 / 60
    print(f"\nProfit per hour:")
    for r in results:
        per_hour = r.net / total_hours if total_hours > 0 else 0
        print(f"  ${r.step:.0f} step: ${per_hour:.2f}/hour ({r.closes} closes in {total_hours:.0f}h)")

    # Find the sweet spot: highest $/hour with resets < 20% of closes
    print(f"\nSweet-spot analysis (resets < 20% of closes):")
    for r in results:
        reset_ratio = r.resets / r.closes * 100 if r.closes > 0 else 0
        per_hour = r.net / total_hours if total_hours > 0 else 0
        survivable = "OK" if reset_ratio < 20 else "HIGH"
        print(f"  ${r.step:.0f}: ${per_hour:.2f}/h, reset_ratio={reset_ratio:.1f}% [{survivable}]")

    # Winner
    viable = [r for r in results if r.closes > 0 and (r.resets / r.closes) < 0.20]
    if viable:
        best = max(viable, key=lambda r: r.net)
        per_hour = best.net / total_hours
        print(f"\n*** Best viable step: ${best.step:.0f} -> ${per_hour:.2f}/hour, ${best.avg_per_close:.2f}/close, {best.closes} closes ***")
    else:
        print("\n*** No step passes the 20% reset ratio gate. Consider relaxing the gate or adding reset suppression. ***")
        best = max(results, key=lambda r: r.net)
        per_hour = best.net / total_hours
        print(f"*** Highest net regardless: ${best.step:.0f} -> ${per_hour:.2f}/hour ***")

    # Save results
    output = {
        "steps_tested": steps,
        "bars_count": len(bars),
        "total_hours": total_hours,
        "results": [
            {
                "step": r.step,
                "closes": r.closes,
                "net": round(r.net, 2),
                "avg_per_close": round(r.avg_per_close, 2),
                "resets": r.resets,
                "win_rate": round(r.win_rate, 1),
                "max_open": r.max_open,
                "max_floating": round(r.max_floating, 2),
                "per_hour": round(r.net / total_hours, 2) if total_hours > 0 else 0,
                "reset_ratio_pct": round(r.resets / r.closes * 100, 1) if r.closes > 0 else 0,
            }
            for r in results
        ],
    }
    out_path = REPORTS / "btc_m15_step_sweet_spot_audit.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")
    return 0


if __name__ == "__main__":
    exit(main())
