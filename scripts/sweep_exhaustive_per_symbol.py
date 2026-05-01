#!/usr/bin/env python3
"""Exhaustive per-symbol parameter sweep to find the true apex."""
from __future__ import annotations

import csv
from pathlib import Path
import time

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


def sim_lattice_alpha(symbol, bars, symbol_info, step_pips, max_open, close_gap, alpha):
    """Fast lattice simulation with alpha close extension."""
    if not bars:
        return {}
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell = anchor + base_step_px
    next_buy = anchor - base_step_px
    tickets: list[Ticket] = []
    realized: list[float] = []

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tickets if t.direction == "SELL")
        ob = sum(1 for t in tickets if t.direction == "BUY")

        ss = dynamic_step(base_step_px, os_, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bs = dynamic_step(base_step_px, ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())

        while bar["high"] >= next_sell and os_ < max_open:
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx))
            os_ += 1
            ss = dynamic_step(base_step_px, os_, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            next_sell += ss
        while bar["low"] <= next_buy and ob < max_open:
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx))
            ob += 1
            bs = dynamic_step(base_step_px, ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            next_buy -= bs

        sells = sorted([t for t in tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(sells) > close_gap and bar["low"] <= sells[close_gap].entry_price:
            outer = sells[0]
            ref = sells[close_gap].entry_price
            close_px = ref + (bar["low"] - ref) * alpha
            realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px))
            tickets.remove(outer)
            sells = sorted([t for t in tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)

        buys = sorted([t for t in tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(buys) > close_gap and bar["high"] >= buys[close_gap].entry_price:
            outer = buys[0]
            ref = buys[close_gap].entry_price
            close_px = ref + (bar["high"] - ref) * alpha
            realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px))
            tickets.remove(outer)
            buys = sorted([t for t in tickets if t.direction=="BUY"], key=lambda t:t.entry_price)

        if not tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell = anchor + base_step_px
            next_buy = anchor - base_step_px

    floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tickets]
    return {"combined": sum(realized)+sum(floating), "realized": sum(realized), "floating": sum(floating), "closes": len(realized)}


def main():
    mt5.initialize()
    cfg_map = default_raw_configs()

    steps = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    gaps = [1, 2, 3, 4, 5]
    alphas = [0.0, 0.25, 0.50]
    max_opens = [10, 15, 20, 25, 30]

    print(f"\n{'='*80}")
    print(f"  Exhaustive Per-Symbol Sweep — {len(steps)*len(gaps)*len(alphas)*len(max_opens)} configs per symbol")
    print(f"{'='*80}")

    overall_best = None

    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        pip_size = pip_size_for(info)
        t0 = time.time()

        rows = []
        for step in steps:
            for gap in gaps:
                for alpha in alphas:
                    for mop in max_opens:
                        r = sim_lattice_alpha(sym, bars, info, step, mop, gap, alpha)
                        rows.append({"step": step, "gap": gap, "alpha": alpha, "max_open": mop,
                                     "combined": r["combined"], "realized": r["realized"],
                                     "floating": r["floating"], "closes": r["closes"],
                                     "symbol": sym})

        elapsed = time.time() - t0
        best = max(rows, key=lambda r: r["combined"])
        worst = min(rows, key=lambda r: r["combined"])
        
        if overall_best is None or best["combined"] > overall_best["combined"]:
            overall_best = best

        # Show top 5
        top5 = sorted(rows, key=lambda r: r["combined"], reverse=True)[:5]
        print(f"\n  {sym} ({elapsed:.1f}s) — {len(rows)} configs tested:")
        print(f"  Current default: step={cfg_map[sym].step_pips} gap=2 α=0 → baseline")
        print(f"  Best α=0.50: {max((r for r in rows if r['alpha']==0.50), key=lambda r: r['combined'])}")
        print(f"\n  Top 5:")
        for i, r in enumerate(top5, 1):
            print(f"    {i}. step={r['step']} gap={r['gap']} α={r['alpha']} mop={r['max_open']}: ${r['combined']:,.2f} ({r['closes']}c)")
        print(f"  Worst: step={worst['step']} gap={worst['gap']} α={worst['alpha']} mop={worst['max_open']}: ${worst['combined']:,.2f}")

        # Save per-symbol
        out = ROOT / "reports" / f"exhaustive_sweep_{sym.lower()}.csv"
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

    print(f"\n{'='*80}")
    print(f"  OVERALL BEST: {overall_best['symbol']} step={overall_best['step']} gap={overall_best['gap']} α={overall_best['alpha']} mop={overall_best['max_open']}: ${overall_best['combined']:,.2f}")
    print(f"{'='*80}")

    mt5.shutdown()


def default_raw_configs():
    from benchmark_inside_geometry_churn import default_raw_configs as _drc
    return _drc()


if __name__ == "__main__":
    raise SystemExit(main())
