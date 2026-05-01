#!/usr/bin/env python3
"""
Apex Doubler — testing more symbols and pushing steps tighter.

Lever 1: ALL available FX symbols, not just 3
Lever 2: Ultra-tight steps (0.10-0.50 pips) where NZDUSD won at 0.25
Lever 3: V3 bounded on all symbols to unlock trending pairs
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
from penetration_lattice_lab_v3_bounded import (
    Config as V3Config,
    simulate_symbol as simulate_v3,
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
    __slots__ = ['direction', 'entry']
    def __init__(self, direction, entry):
        self.direction = direction
        self.entry = entry


def run_lattice(symbol, bars, info, step_sell, step_buy, cap, fill_cap_pips=10.0):
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step_sell = step_sell * pip
    base_step_buy = step_buy * pip
    fill_cap_px = fill_cap_pips * pip

    anchor = bars[0]["close"]
    sell_level = anchor + base_step_sell
    buy_level = anchor - base_step_buy

    positions = [Pos("SELL", sell_level), Pos("BUY", buy_level)]
    realized = 0.0
    closes = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(Pos("SELL", sell_level))
            oss += 1
            if oss >= 20:
                sell_level += base_step_sell * 2.0
            elif oss >= 10:
                sell_level += base_step_sell * 1.5
            else:
                sell_level += base_step_sell

        while bar["low"] <= buy_level and obs < cap:
            positions.append(Pos("BUY", buy_level))
            obs += 1
            if obs >= 20:
                buy_level -= base_step_buy * 2.0
            elif obs >= 10:
                buy_level -= base_step_buy * 1.5
            else:
                buy_level -= base_step_buy

        # Two-level penetration close with capped fill
        sells = sorted([p for p in positions if p.direction == "SELL"],
                       key=lambda p: p.entry, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry:
            raw_depth = sells[1].entry - bar["low"]
            capped_depth = min(raw_depth, fill_cap_px)
            fill_px = sells[1].entry - capped_depth
            pnl = pnl_usd(symbol, "SELL", sells[0].entry, fill_px, spread)
            if pnl <= 0:
                break
            realized += pnl
            closes += 1
            positions.remove(sells[0])
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)

        buys = sorted([p for p in positions if p.direction == "BUY"],
                      key=lambda p: p.entry)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry:
            raw_depth = bar["high"] - buys[1].entry
            capped_depth = min(raw_depth, fill_cap_px)
            fill_px = buys[1].entry + capped_depth
            pnl = pnl_usd(symbol, "BUY", buys[0].entry, fill_px, spread)
            if pnl <= 0:
                break
            realized += pnl
            closes += 1
            positions.remove(buys[0])
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)

    last_close = bars[-1]["close"]
    floating = [pnl_usd(symbol, p.direction, p.entry, last_close, spread) for p in positions]
    floating_net = sum(floating)
    worst = min(floating) if floating else 0.0

    return {
        "combined": round(realized + floating_net, 3),
        "realized": round(realized, 3),
        "floating": round(floating_net, 3),
        "worst": round(worst, 3),
        "closes": closes,
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 100)
    print("APEX DOUBLER — more symbols + ultra-tight steps")
    print("=" * 100)

    days = 60
    all_symbols = ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF",
                   "AUDUSD", "USDCAD", "GBPJPY", "EURJPY", "AUDCAD",
                   "AUDNZD", "CADCHF", "EURAUD", "EURCAD", "EURGBP",
                   "EURCHF", "GBPAUD", "GBPCAD", "NZDJPY"]

    rows = []
    basket_total = 0.0
    basket_symbols = []

    # === LEVER 1: All symbols with sharpened apex configs ===
    print("\n" + "=" * 80)
    print("LEVER 1: All available FX symbols with apex configs")
    print("=" * 80)

    # Use the sharpened configs for the known 3
    apex_configs = {
        "GBPUSD": ("raw", 0.75, 1.00, 25),
        "EURUSD": ("raw", 1.25, 2.00, 20),
        "NZDUSD": ("raw", 0.25, 0.50, 50),
        "USDJPY": ("v3", 0.50, 0.50, 20),  # V3 bounded
        "USDCHF": ("v3", 0.50, 0.50, 20),  # V3 bounded
    }

    for symbol in all_symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        if symbol in apex_configs:
            mode, ss, bs, cap = apex_configs[symbol]
            if mode == "v3":
                vc = type("V3", (), {
                    "step_pips": ss, "max_open_per_side": cap,
                    "max_floating_loss_usd": -10.0, "vwap_lookback": 20,
                    "regime_lookback_bars": 60, "max_range_pips": 24.0,
                    "breakout_buffer_pips": 5.0, "max_lattice_window_bars": 240,
                    "cooldown_bars": 60,
                    "adaptive_step_threshold_1": 10, "adaptive_step_threshold_2": 20,
                    "adaptive_step_multiplier_1": 1.5, "adaptive_step_multiplier_2": 2.0,
                })()
                r_v3 = simulate_v3(symbol, bars, info, vc)
                combined = r_v3["combined_net_usd"]
                worst = r_v3.get("worst_floating_usd", 0)
                closes = r_v3.get("total_closes", 0)
                daily = combined / days
                print(f"  {symbol:<8} V3    combined=${combined:+8.2f} daily=${daily:+6.2f} worst=${worst:+7.2f} closes={closes:>5}")
                rows.append({"symbol": symbol, "mode": "v3", "step_sell": ss, "step_buy": bs,
                             "cap": cap, "combined": combined, "daily": daily, "worst": worst, "closes": closes})
                r = {"worst": worst}
            else:
                r = run_lattice(symbol, bars, info, ss, bs, cap)
                daily = r["combined"] / days
                combined = r["combined"]
                print(f"  {symbol:<8} raw   combined=${r['combined']:+8.2f} daily=${daily:+6.2f} worst=${r['worst']:+7.2f} closes={r['closes']:>5}")
                rows.append({"symbol": symbol, "mode": "raw", "step_sell": ss, "step_buy": bs,
                             "cap": cap, "combined": r["combined"], "daily": daily,
                             "worst": r["worst"], "closes": r["closes"]})

            if r.get("worst", 0) > -50:  # Acceptable floating risk
                basket_total += combined
                basket_symbols.append(symbol)

        else:
            # Quick scan: test 3 configs to find if symbol is viable
            best_combined = -999999
            best_config = None
            for ss, bs, cap in [(0.50, 0.50, 20), (0.75, 1.00, 25), (1.00, 1.00, 20)]:
                r = run_lattice(symbol, bars, info, ss, bs, cap)
                if r["combined"] > best_combined:
                    best_combined = r["combined"]
                    best_config = (ss, bs, cap, r)

            ss, bs, cap, r = best_config
            daily = r["combined"] / days
            tag = "✅" if r["combined"] > 200 else "❌" if r["combined"] < 0 else "➡️"
            print(f"  {symbol:<8} raw   combined=${r['combined']:+8.2f} daily=${daily:+6.2f} worst=${r['worst']:+7.2f} {tag}")
            rows.append({"symbol": symbol, "mode": "raw", "step_sell": ss, "step_buy": bs,
                         "cap": cap, "combined": r["combined"], "daily": daily,
                         "worst": r["worst"], "closes": r["closes"]})

    # === LEVER 2: Ultra-tight steps on the known winners ===
    print("\n" + "=" * 80)
    print("LEVER 2: Ultra-tight steps (0.10-0.50 pips)")
    print("=" * 80)

    ultra_tight = {
        "GBPUSD": [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.75],
        "EURUSD": [0.20, 0.30, 0.40, 0.50, 0.60, 0.75, 1.00],
        "NZDUSD": [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40],
    }

    for symbol, steps in ultra_tight.items():
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        best = None
        for step in steps:
            for cap in [25, 50]:
                r = run_lattice(symbol, bars, info, step, step, cap)
                daily = r["combined"] / days
                if best is None or r["combined"] > best["combined"]:
                    best = dict(r, step=step, cap=cap)

        if best:
            print(f"  {symbol:<8} ultra-tight apex: step={best['step']:.2f} cap={best['cap']} "
                  f"combined=${best['combined']:+8.2f} daily=${best['combined']/days:+6.2f}")
            rows.append({"symbol": symbol, "mode": "ultra_tight", "step_sell": best["step"],
                         "step_buy": best["step"], "cap": best["cap"],
                         "combined": best["combined"], "daily": best["combined"]/days,
                         "worst": best["worst"], "closes": best["closes"]})

    # === FINAL BASKET ===
    print(f"\n{'='*80}")
    print("FINAL APEX BASKET — all symbols that pass the floating risk filter")
    print(f"{'='*80}")

    # Best per symbol
    for symbol in sorted(set(r["symbol"] for r in rows)):
        sym = [r for r in rows if r["symbol"] == symbol]
        best = max(sym, key=lambda x: x["combined"])
        print(f"  {symbol:<8} ${best['combined']:+8.2f}/60d  ${best['daily']:+6.2f}/day  "
              f"worst=${best['worst']:+7.2f}  mode={best['mode']}")

    # Total of viable symbols (worst > -$50)
    viable = [max([r for r in rows if r["symbol"] == s], key=lambda x: x["combined"])
              for s in set(r["symbol"] for r in rows)
              if max([r for r in rows if r["symbol"] == s], key=lambda x: x["combined"]).get("worst", 0) > -50]
    total = sum(r["combined"] for r in viable)
    daily = total / days

    print(f"\n  VIABLE BASKET ({len(viable)} symbols): ${total:+.2f}/60d  ${daily:+.2f}/day  ${daily*365:,.0f}/year")
    print(f"  At 0.05 lot: ${daily*5:+.2f}/day  ${daily*5*365:,.0f}/year")
    print(f"  At 0.10 lot: ${daily*10:+.2f}/day  ${daily*10*365:,.0f}/year")

    # Save
    output = ROOT / "reports" / "apex_doubler.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
