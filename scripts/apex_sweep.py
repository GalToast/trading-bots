#!/usr/bin/env python3
"""
Apex Sweep — testing every remaining dimension that could increase edge.

Untested or coarsely tested dimensions:
1. Cap beyond 20 (30, 40, 50, 100)
2. Close mode: two_level vs ALL profitable close
3. Close reference: penetration level vs bar extreme
4. Asymmetric steps: different buy vs sell spacing
5. Fine step granularity on known apices
6. V3 regime params deeper sweep on hostiles
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
    __slots__ = ['direction', 'entry', 'opened_idx']
    def __init__(self, direction, entry, opened_idx=0):
        self.direction = direction
        self.entry = entry
        self.opened_idx = opened_idx


def adaptive_step(base_step, count):
    if count >= 20:
        return base_step * 2.0
    elif count >= 10:
        return base_step * 1.5
    return base_step


def run_lattice(symbol, bars, info, step_pips, cap,
               close_mode="two_level",
               close_ref="level",
               sell_step_mult=1.0, buy_step_mult=1.0,
               max_open_override=None):
    """Flexible lattice simulator with all remaining dimensions exposed."""
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step = step_pips * pip

    anchor = bars[0]["close"]
    sell_level = anchor + base_step
    buy_level = anchor - base_step

    positions: list[Pos] = []
    realized: list[float] = []
    max_open = 0
    worst_floating_seen = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Open new orders
        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        cap_val = max_open_override if max_open_override else cap

        while bar["high"] >= sell_level and oss < cap_val:
            positions.append(Pos("SELL", sell_level, idx))
            oss += 1
            sell_level += adaptive_step(base_step, oss) * sell_step_mult

        while bar["low"] <= buy_level and obs < cap_val:
            positions.append(Pos("BUY", buy_level, idx))
            obs += 1
            buy_level -= adaptive_step(base_step, obs) * buy_step_mult

        # Close logic
        if close_mode == "two_level":
            # Two-level: close outermost when price penetrates below/above 2nd level
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)
            while len(sells) >= 2 and bar["low"] <= sells[1].entry:
                if close_ref == "level":
                    close_ref_px = sells[1].entry
                else:  # bar extreme
                    close_ref_px = bar["low"]
                pnl = pnl_usd(symbol, "SELL", sells[0].entry, close_ref_px, spread)
                if pnl <= 0:
                    break
                realized.append(pnl)
                positions.remove(sells[0])
                sells = sorted([p for p in positions if p.direction == "SELL"],
                               key=lambda p: p.entry, reverse=True)

            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)
            while len(buys) >= 2 and bar["high"] >= buys[1].entry:
                if close_ref == "level":
                    close_ref_px = buys[1].entry
                else:
                    close_ref_px = bar["high"]
                pnl = pnl_usd(symbol, "BUY", buys[0].entry, close_ref_px, spread)
                if pnl <= 0:
                    break
                realized.append(pnl)
                positions.remove(buys[0])
                buys = sorted([p for p in positions if p.direction == "BUY"],
                              key=lambda p: p.entry)

        elif close_mode == "all_profitable":
            # Close ALL profitable positions on penetration
            # Sells
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)
            if len(sells) >= 2 and bar["low"] <= sells[1].entry:
                close_ref_px = bar["low"] if close_ref == "extreme" else sells[1].entry
                profitable = [p for p in sells
                              if pnl_usd(symbol, "SELL", p.entry, close_ref_px, spread) > 0]
                for p in profitable:
                    realized.append(pnl_usd(symbol, "SELL", p.entry, close_ref_px, spread))
                    positions.remove(p)

            # Buys
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)
            if len(buys) >= 2 and bar["high"] >= buys[1].entry:
                close_ref_px = bar["high"] if close_ref == "extreme" else buys[1].entry
                profitable = [p for p in buys
                              if pnl_usd(symbol, "BUY", p.entry, close_ref_px, spread) > 0]
                for p in profitable:
                    realized.append(pnl_usd(symbol, "BUY", p.entry, close_ref_px, spread))
                    positions.remove(p)

        max_open = max(max_open, len(positions))

        # Track worst floating
        floating_now = [pnl_usd(symbol, p.direction, p.entry, bar["close"], spread)
                        for p in positions]
        if floating_now:
            worst_floating_seen = max(worst_floating_seen, abs(min(floating_now)))

    # Final
    last_close = bars[-1]["close"]
    floating = [pnl_usd(symbol, p.direction, p.entry, last_close, spread) for p in positions]
    realized_net = sum(realized)
    floating_net = sum(floating)

    return {
        "combined": round(realized_net + floating_net, 3),
        "realized": round(realized_net, 3),
        "floating": round(floating_net, 3),
        "worst_seen": round(worst_floating_seen, 3),
        "worst_final": round(min(floating), 3) if floating else 0.0,
        "max_open": max_open,
        "closes": len(realized),
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 100)
    print("APEX SWEEP — every remaining dimension that could increase edge")
    print("=" * 100)

    days = 60
    rows = []

    # === TEST 1: Cap beyond 20 (30, 40, 50, 100) ===
    print("\n" + "=" * 80)
    print("TEST 1: Cap beyond 20")
    print("=" * 80)

    for symbol in ["GBPUSD", "EURUSD"]:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        step = 2.0 if symbol == "GBPUSD" else 2.5
        for cap in [20, 30, 40, 50, 100]:
            r = run_lattice(symbol, bars, info, step, cap)
            daily = r["combined"] / days
            print(f"  {symbol} cap={cap:>3} combined=${r['combined']:+8.2f} closes={r['closes']:>5} "
                  f"worst=${r['worst_seen']:+7.2f} daily=${daily:+6.2f}")
            rows.append({"test": "cap_beyond_20", "symbol": symbol, "cap": cap, "step": step,
                         "combined": r["combined"], "realized": r["realized"],
                         "floating": r["floating"], "worst_seen": r["worst_seen"],
                         "closes": r["closes"], "daily": daily})

    # === TEST 2: All-profitable close vs two_level ===
    print("\n" + "=" * 80)
    print("TEST 2: Close mode — two_level vs all_profitable")
    print("=" * 80)

    for symbol in ["GBPUSD", "EURUSD", "NZDUSD"]:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        step = {"GBPUSD": 2.0, "EURUSD": 2.5, "NZDUSD": 1.5}[symbol]
        for close_mode in ["two_level", "all_profitable"]:
            for close_ref in ["level", "extreme"]:
                r = run_lattice(symbol, bars, info, step, cap=20,
                                close_mode=close_mode, close_ref=close_ref)
                daily = r["combined"] / days
                tag = "🔥" if close_mode == "all_profitable" and r["combined"] > 0 else ""
                print(f"  {symbol} {close_mode:<15} ref={close_ref:<8} "
                      f"combined=${r['combined']:+8.2f} closes={r['closes']:>5} "
                      f"worst=${r['worst_seen']:+7.2f} daily=${daily:+6.2f} {tag}")
                rows.append({"test": "close_mode", "symbol": symbol, "cap": 20, "step": step,
                             "close_mode": close_mode, "close_ref": close_ref,
                             "combined": r["combined"], "realized": r["realized"],
                             "floating": r["floating"], "worst_seen": r["worst_seen"],
                             "closes": r["closes"], "daily": daily})

    # === TEST 3: Asymmetric steps ===
    print("\n" + "=" * 80)
    print("TEST 3: Asymmetric buy/sell steps")
    print("=" * 80)

    for symbol in ["GBPUSD", "EURUSD"]:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        base = 2.0 if symbol == "GBPUSD" else 2.5
        for sell_mult in [0.5, 0.75, 1.0, 1.5, 2.0]:
            for buy_mult in [0.5, 0.75, 1.0, 1.5, 2.0]:
                r = run_lattice(symbol, bars, info, base, cap=20,
                                sell_step_mult=sell_mult, buy_step_mult=buy_mult)
                daily = r["combined"] / days
                rows.append({"test": "asymmetric", "symbol": symbol, "cap": 20, "step": base,
                             "sell_mult": sell_mult, "buy_mult": buy_mult,
                             "combined": r["combined"], "realized": r["realized"],
                             "floating": r["floating"], "worst_seen": r["worst_seen"],
                             "closes": r["closes"], "daily": daily})

        # Show best for this symbol
        sym = [r for r in rows if r["test"] == "asymmetric" and r["symbol"] == symbol]
        if sym:
            best = max(sym, key=lambda x: x["combined"])
            print(f"  {symbol} BEST: sell_mult={best['sell_mult']:<4} buy_mult={best['buy_mult']:<4} "
                  f"combined=${best['combined']:+8.2f} closes={best['closes']:>5}")

    # === TEST 4: Fine step granularity on known apices ===
    print("\n" + "=" * 80)
    print("TEST 4: Fine step granularity")
    print("=" * 80)

    fine_steps = {
        "GBPUSD": [1.50, 1.625, 1.75, 1.875, 2.0, 2.125, 2.25],
        "EURUSD": [2.0, 2.25, 2.50, 2.75, 3.0],
        "NZDUSD": [1.0, 1.25, 1.50, 1.75, 2.0],
    }

    for symbol, steps in fine_steps.items():
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        best = None
        for step in steps:
            r = run_lattice(symbol, bars, info, step, cap=20)
            daily = r["combined"] / days
            rows.append({"test": "fine_step", "symbol": symbol, "cap": 20, "step": step,
                         "combined": r["combined"], "realized": r["realized"],
                         "floating": r["floating"], "worst_seen": r["worst_seen"],
                         "closes": r["closes"], "daily": daily})
            if best is None or r["combined"] > best["combined"]:
                best = r
        print(f"  {symbol} apex: step={best.get('step', 'N/A'):<6} combined=${best['combined']:+8.2f}")

    # === TEST 5: Close at bar extreme (realistic fills) ===
    print("\n" + "=" * 80)
    print("TEST 5: Close reference — level vs bar extreme")
    print("=" * 80)

    for symbol in ["GBPUSD", "EURUSD", "NZDUSD"]:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        step = {"GBPUSD": 2.0, "EURUSD": 2.5, "NZDUSD": 1.5}[symbol]
        for close_ref in ["level", "extreme"]:
            r = run_lattice(symbol, bars, info, step, cap=20, close_ref=close_ref)
            daily = r["combined"] / days
            rows.append({"test": "close_ref", "symbol": symbol, "cap": 20, "step": step,
                         "close_ref": close_ref, "combined": r["combined"],
                         "realized": r["realized"], "floating": r["floating"],
                         "worst_seen": r["worst_seen"], "closes": r["closes"], "daily": daily})
            print(f"  {symbol} ref={close_ref:<8} combined=${r['combined']:+8.2f} "
                  f"closes={r['closes']:>5} worst=${r['worst_seen']:+7.2f}")

    # === FINAL APEX BASKET ===
    print("\n" + "=" * 80)
    print("FINAL APEX BASKET — best config per symbol")
    print("=" * 80)

    # Find best per symbol across all tests
    for symbol in ["GBPUSD", "EURUSD", "NZDUSD"]:
        sym = [r for r in rows if r.get("symbol") == symbol]
        if not sym:
            continue
        best = max(sym, key=lambda x: x["combined"])
        print(f"  {symbol}: ${best['combined']:+8.2f}/60d  ${best['daily']:+.2f}/day  "
              f"closes={best['closes']:>5}  test={best.get('test','?')}")

    total = sum(max([r for r in rows if r.get("symbol") == s], key=lambda x: x["combined"])["combined"]
                for s in ["GBPUSD", "EURUSD", "NZDUSD"] if any(r.get("symbol") == s for r in rows))
    daily = total / days
    print(f"  TOTAL: ${total:+.2f}/60d  ${daily:+.2f}/day  ${daily*365:.0f}/year")

    # Save
    output = ROOT / "reports" / "apex_sweep.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        all_keys = set()
        for r in rows:
            all_keys.update(r.keys())
        writer = csv.DictWriter(f, fieldnames=sorted(all_keys))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
