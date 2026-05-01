#!/usr/bin/env python3
"""
Sharpened Apex — realistic fill (10-pip cap) across ALL remaining dimensions.

Dimensions to sweep:
1. Fine step granularity (0.25 pip resolution around known apices)
2. Asymmetric buy/sell steps
3. Cap optimization (5-50)
4. Close mode: two_level vs all_profitable
5. All with the realistic 10-pip capped fill model
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
MAX_CAP_PENETRATION = 10.0 * 0.0001  # 10 pip cap in price units


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
    __slots__ = ['direction', 'entry', 'opened_idx']
    def __init__(self, direction, entry, opened_idx=0):
        self.direction = direction
        self.entry = entry
        self.opened_idx = opened_idx


def run_apex(symbol, bars, info, step_sell, step_buy, cap,
              close_mode="two_level", fill_cap_pips=10.0):
    """Apex sim with realistic capped fill and asymmetric steps."""
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

    positions: list[Pos] = []
    positions.append(Pos("SELL", sell_level, 0))
    positions.append(Pos("BUY", buy_level, 0))

    realized = 0.0
    closes = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(Pos("SELL", sell_level, idx))
            oss += 1
            if oss >= 20:
                sell_level += base_step_sell * 2.0
            elif oss >= 10:
                sell_level += base_step_sell * 1.5
            else:
                sell_level += base_step_sell

        while bar["low"] <= buy_level and obs < cap:
            positions.append(Pos("BUY", buy_level, idx))
            obs += 1
            if obs >= 20:
                buy_level -= base_step_buy * 2.0
            elif obs >= 10:
                buy_level -= base_step_buy * 1.5
            else:
                buy_level -= base_step_buy

        if close_mode == "two_level":
            # Sells
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

            # Buys
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

        elif close_mode == "all_profitable":
            # Sells
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)
            if len(sells) >= 2 and bar["low"] <= sells[1].entry:
                raw_depth = sells[1].entry - bar["low"]
                capped_depth = min(raw_depth, fill_cap_px)
                fill_px = sells[1].entry - capped_depth
                profitable = [p for p in sells
                              if pnl_usd(symbol, "SELL", p.entry, fill_px, spread) > 0]
                for p in profitable:
                    pnl = pnl_usd(symbol, "SELL", p.entry, fill_px, spread)
                    realized += pnl
                    closes += 1
                    positions.remove(p)

            # Buys
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)
            if len(buys) >= 2 and bar["high"] >= buys[1].entry:
                raw_depth = bar["high"] - buys[1].entry
                capped_depth = min(raw_depth, fill_cap_px)
                fill_px = buys[1].entry + capped_depth
                profitable = [p for p in buys
                              if pnl_usd(symbol, "BUY", p.entry, fill_px, spread) > 0]
                for p in profitable:
                    pnl = pnl_usd(symbol, "BUY", p.entry, fill_px, spread)
                    realized += pnl
                    closes += 1
                    positions.remove(p)

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
    print("SHARPENED APEX — realistic 10-pip fill cap across all dimensions")
    print("=" * 100)

    days = 60
    symbols = ["GBPUSD", "EURUSD", "NZDUSD"]
    rows = []
    global_best = {}

    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        print(f"\n{'='*90}")
        print(f"=== {symbol} ===")
        print(f"{'='*90}")

        best = None
        best_combined = -999999

        # 1. Fine step sweep (symmetric) with realistic fill cap
        print(f"\n--- Fine step sweep (symmetric, cap=20, fill_cap=10p) ---")
        fine_steps = {
            "GBPUSD": [1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00],
            "EURUSD": [1.75, 2.00, 2.25, 2.50, 2.75, 3.00, 3.50, 4.00],
            "NZDUSD": [0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00],
        }

        for step in fine_steps[symbol]:
            for close_mode in ["two_level", "all_profitable"]:
                r = run_apex(symbol, bars, info, step, step, 20, close_mode=close_mode, fill_cap_pips=10.0)
                daily = r["combined"] / days
                tag = "🔥" if best is None or r["combined"] > best["combined"] else ""
                print(f"  step={step:<5.2f} {close_mode:<15} combined=${r['combined']:+8.2f} daily=${daily:+6.2f} closes={r['closes']:>5} {tag}")
                rows.append({"symbol": symbol, "test": "fine_step", "step": step, "step_sell": step,
                             "step_buy": step, "cap": 20, "close_mode": close_mode,
                             "fill_cap_pips": 10.0, "combined": r["combined"],
                             "realized": r["realized"], "floating": r["floating"],
                             "worst": r["worst"], "closes": r["closes"], "daily": daily})
                if r["combined"] > best_combined:
                    best_combined = r["combined"]
                    best = dict(r, step=step, step_sell=step, step_buy=step,
                                cap=20, close_mode=close_mode, fill_cap_pips=10.0)

        # 2. Asymmetric steps (fine grid around best symmetric step)
        print(f"\n--- Asymmetric step sweep ---")
        best_step = best["step"] if best else 2.0
        for sell_step in [best_step - 0.5, best_step - 0.25, best_step, best_step + 0.25, best_step + 0.5, best_step + 1.0]:
            if sell_step < 0.25:
                continue
            for buy_step in [best_step - 0.5, best_step - 0.25, best_step, best_step + 0.25, best_step + 0.5]:
                if buy_step < 0.25:
                    continue
                r = run_apex(symbol, bars, info, sell_step, buy_step, 20, fill_cap_pips=10.0)
                daily = r["combined"] / days
                if r["combined"] > best_combined:
                    best_combined = r["combined"]
                    best = dict(r, step_sell=sell_step, step_buy=buy_step,
                                cap=20, close_mode="two_level", fill_cap_pips=10.0)
                    print(f"  *** NEW BEST: sell={sell_step:.2f} buy={buy_step:.2f} combined=${r['combined']:+8.2f} daily=${daily:+6.2f}")

                rows.append({"symbol": symbol, "test": "asymmetric",
                             "step_sell": sell_step, "step_buy": buy_step,
                             "cap": 20, "close_mode": "two_level", "fill_cap_pips": 10.0,
                             "combined": r["combined"], "realized": r["realized"],
                             "floating": r["floating"], "worst": r["worst"],
                             "closes": r["closes"], "daily": daily})

        # 3. Cap sweep around best config
        print(f"\n--- Cap sweep ---")
        best_sell = best["step_sell"]
        best_buy = best["step_buy"]
        for cap in [5, 8, 10, 12, 15, 18, 20, 25, 30, 40, 50]:
            r = run_apex(symbol, bars, info, best_sell, best_buy, cap, fill_cap_pips=10.0)
            daily = r["combined"] / days
            tag = "🔥" if r["combined"] > best_combined else ""
            print(f"  cap={cap:>3} combined=${r['combined']:+8.2f} daily=${daily:+6.2f} closes={r['closes']:>5} {tag}")
            rows.append({"symbol": symbol, "test": "cap_sweep",
                         "step_sell": best_sell, "step_buy": best_buy,
                         "cap": cap, "close_mode": "two_level", "fill_cap_pips": 10.0,
                         "combined": r["combined"], "realized": r["realized"],
                         "floating": r["floating"], "worst": r["worst"],
                         "closes": r["closes"], "daily": daily})
            if r["combined"] > best_combined:
                best_combined = r["combined"]
                best = dict(r, step_sell=best_sell, step_buy=best_buy, cap=cap,
                            close_mode="two_level", fill_cap_pips=10.0)

        global_best[symbol] = best
        print(f"\n  *** {symbol} APEX: sell={best['step_sell']:.2f} buy={best['step_buy']:.2f} "
              f"cap={best['cap']} combined=${best['combined']:+.2f} daily=${best['combined']/days:+.2f} ***")

    # Final basket
    print(f"\n{'='*90}")
    print("FINAL SHARPENED APEX BASKET — realistic 10-pip fill cap")
    print(f"{'='*90}")

    total = sum(b["combined"] for b in global_best.values())
    daily = total / days

    for symbol, b in global_best.items():
        print(f"  {symbol}: sell={b['step_sell']:.2f} buy={b['step_buy']:.2f} cap={b['cap']} "
              f"${b['combined']:+.2f}/60d  ${b['combined']/days:+.2f}/day  "
              f"worst=${b['worst']:+.2f} closes={b['closes']}")

    print(f"\n  TOTAL: ${total:+.2f}/60d  ${daily:+.2f}/day  ${daily*365:,.0f}/year")
    print(f"  At 0.05 lot: ${daily*5:+.2f}/day  ${daily*5*365:,.0f}/year")
    print(f"  At 0.10 lot: ${daily*10:+.2f}/day  ${daily*10*365:,.0f}/year")

    # Save
    output = ROOT / "reports" / "sharpened_apex.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
