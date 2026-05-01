#!/usr/bin/env python3
"""
Realistic fill modeling — cap extreme fills to reasonable levels.

Test fill models:
1. Level fill (conservative): fill at penetration level — guaranteed
2. 50% fill: fill halfway between level and extreme
3. 25% fill: fill at 25% of the sweep (more realistic for market orders)
4. Capped extreme: fill at extreme but cap depth at 5 pips (realistic reversal)
5. Capped extreme: cap at 10 pips
"""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    ROOT,
    load_bars,
    pip_size_for,
    spread_price,
)


VOLUME = 0.01


def pnl_usd(symbol, direction, entry, exit_px, spread_px, vol=VOLUME):
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(ot, symbol, vol, entry, exit_px)
    if gross is None:
        return 0.0
    if direction == "BUY":
        sc = mt5.order_calc_profit(ot, symbol, vol, entry + spread_px, entry)
    else:
        sc = mt5.order_calc_profit(ot, symbol, vol, entry, entry + spread_px)
    return float(gross) - abs(float(sc or 0.0))


class Pos:
    def __init__(self, direction, entry, opened_idx=0):
        self.direction = direction
        self.entry = entry
        self.opened_idx = opened_idx


def run_with_fill_models(symbol, bars, info, step_pips, cap,
                          fill_models):
    """Run lattice with multiple fill models simultaneously."""
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step = step_pips * pip

    anchor = bars[0]["close"]
    sell_level = anchor + base_step
    buy_level = anchor - base_step

    positions: list[Pos] = []
    positions.append(Pos("SELL", sell_level, 0))
    positions.append(Pos("BUY", buy_level, 0))

    # Initialize results per model
    results = {name: {"realized": 0.0, "closes": 0} for name in fill_models}

    for idx in range(1, len(bars)):
        bar = bars[idx]

        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(Pos("SELL", sell_level, idx))
            oss += 1
            sell_level += base_step

        while bar["low"] <= buy_level and obs < cap:
            positions.append(Pos("BUY", buy_level, idx))
            obs += 1
            buy_level -= base_step

        # Close sells
        sells = sorted([p for p in positions if p.direction == "SELL"],
                       key=lambda p: p.entry, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry:
            level_fill = sells[1].entry
            extreme_fill = bar["low"]
            raw_depth = sells[1].entry - extreme_fill

            for name, model in fill_models.items():
                if model["type"] == "level":
                    fill_px = level_fill
                elif model["type"] == "extreme":
                    fill_px = extreme_fill
                elif model["type"] == "pct":
                    fill_px = level_fill - model["pct"] * raw_depth
                elif model["type"] == "capped":
                    capped_depth = min(raw_depth, model["cap_pips"] * pip)
                    fill_px = level_fill - capped_depth
                else:
                    fill_px = level_fill

                pnl = pnl_usd(symbol, "SELL", sells[0].entry, fill_px, spread)
                results[name]["realized"] += pnl
                results[name]["closes"] += 1

            positions.remove(sells[0])
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)

        # Close buys
        buys = sorted([p for p in positions if p.direction == "BUY"],
                      key=lambda p: p.entry)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry:
            level_fill = buys[1].entry
            extreme_fill = bar["high"]
            raw_depth = extreme_fill - buys[1].entry

            for name, model in fill_models.items():
                if model["type"] == "level":
                    fill_px = level_fill
                elif model["type"] == "extreme":
                    fill_px = extreme_fill
                elif model["type"] == "pct":
                    fill_px = level_fill + model["pct"] * raw_depth
                elif model["type"] == "capped":
                    capped_depth = min(raw_depth, model["cap_pips"] * pip)
                    fill_px = level_fill + capped_depth
                else:
                    fill_px = level_fill

                pnl = pnl_usd(symbol, "BUY", buys[0].entry, fill_px, spread)
                results[name]["realized"] += pnl
                results[name]["closes"] += 1

            positions.remove(buys[0])
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)

    # Final floating
    last_close = bars[-1]["close"]
    floating = [pnl_usd(symbol, p.direction, p.entry, last_close, spread) for p in positions]
    floating_net = sum(floating)
    worst_float = min(floating) if floating else 0.0

    out = {}
    for name, r in results.items():
        out[name] = {
            "combined": round(r["realized"] + floating_net, 3),
            "realized": round(r["realized"], 3),
            "closes": r["closes"],
        }
    out["floating_net"] = round(floating_net, 3)
    out["worst_float"] = round(worst_float, 3)
    out["max_open"] = max(len(positions) for _ in [1])  # approximate
    return out


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 90)
    print("REALISTIC FILL MODELING — what's actually achievable?")
    print("=" * 90)

    fill_models = {
        "level": {"type": "level"},
        "extreme": {"type": "extreme"},
        "25pct": {"type": "pct", "pct": 0.25},
        "50pct": {"type": "pct", "pct": 0.50},
        "cap_5p": {"type": "capped", "cap_pips": 5.0},
        "cap_10p": {"type": "capped", "cap_pips": 10.0},
        "cap_20p": {"type": "capped", "cap_pips": 20.0},
    }

    days = 60
    symbols = ["GBPUSD", "EURUSD", "NZDUSD"]
    rows = []

    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        step = {"GBPUSD": 2.0, "EURUSD": 2.5, "NZDUSD": 1.5}[symbol]
        print(f"\n=== {symbol} — step={step} pips ===")

        results = run_with_fill_models(symbol, bars, info, step, 20, fill_models)

        for name, r in results.items():
            if not isinstance(r, dict):
                continue
            daily = r["combined"] / days
            rows.append({"symbol": symbol, "step": step, "cap": 20,
                         "fill_model": name, "combined": r["combined"],
                         "realized": r["realized"], "closes": r["closes"], "daily": daily})
            print(f"  {name:<12} combined=${r['combined']:+9.2f} daily=${daily:+7.2f} "
                  f"closes={r['closes']:>5}")

        if "level" in results and isinstance(results["level"], dict) and "extreme" in results and isinstance(results["extreme"], dict):
            level_daily = results["level"]["combined"] / days
            extreme_daily = results["extreme"]["combined"] / days
            print(f"  Ratio: extreme/level = {extreme_daily/level_daily:.1f}x")

        # Show best realistic model
        if "cap_10p" in results and isinstance(results["cap_10p"], dict):
            realistic = results["cap_10p"]["combined"] / days
            conservative = results["level"]["combined"] / days
            print(f"  Realistic (10p cap) vs conservative: ${realistic:+.2f} vs ${conservative:+.2f}/day")

    # Save
    output = ROOT / "reports" / "realistic_fill_models.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY — realistic fill across all symbols")
    print(f"{'='*80}")

    for model_name in ["level", "cap_5p", "cap_10p", "cap_20p", "50pct", "extreme"]:
        total = sum(r["combined"] for r in rows if r["fill_model"] == model_name)
        daily = total / days
        print(f"  {model_name:<12} total=${total:+9.2f}/60d  ${daily:+7.2f}/day  ${daily*365:,.0f}/year")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
