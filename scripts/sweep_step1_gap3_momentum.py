#!/usr/bin/env python3
"""Test: step=1.0/gap=3 + momentum + alpha combo."""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


class ChurnTicket:
    __slots__ = ("direction", "entry_price", "opened_idx")
    def __init__(self, direction, entry_price, opened_idx):
        self.direction = direction
        self.entry_price = entry_price
        self.opened_idx = opened_idx


def simulate_combo(symbol, bars, symbol_info, step_pips, close_gap, alpha, momentum_gate):
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = step_pips * pip_size
    
    bl_anchor = bars[0]["close"]
    bl_next_sell = bl_anchor + base_step_px
    bl_next_buy = bl_anchor - base_step_px
    bl_tickets: list[Ticket] = []
    bl_realized: list[float] = []
    
    churn_tickets: list[ChurnTicket] = []
    churn_realized: list[float] = []
    churn_skipped_mom = 0
    
    for idx in range(1, len(bars)):
        bar = bars[idx]
        
        bl_os = sum(1 for t in bl_tickets if t.direction == "SELL")
        bl_ob = sum(1 for t in bl_tickets if t.direction == "BUY")
        bl_ss = dynamic_step(base_step_px, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bl_bs = dynamic_step(base_step_px, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        while bar["high"] >= bl_next_sell and bl_os < 30:
            bl_tickets.append(Ticket(direction="SELL", entry_price=bl_next_sell, opened_idx=idx))
            bl_os += 1
            bl_ss = dynamic_step(base_step_px, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_sell += bl_ss
        while bar["low"] <= bl_next_buy and bl_ob < 30:
            bl_tickets.append(Ticket(direction="BUY", entry_price=bl_next_buy, opened_idx=idx))
            bl_ob += 1
            bl_bs = dynamic_step(base_step_px, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_buy -= bl_bs
        
        gap = close_gap
        closed_this_bar = []
        bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(bl_sells) > gap and bar["low"] <= bl_sells[gap].entry_price:
            outer = bl_sells[0]
            ref = bl_sells[gap].entry_price
            close_px = ref + (bar["low"] - ref) * alpha
            bl_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px))
            closed_this_bar.append(("SELL", outer.entry_price))
            bl_tickets.remove(outer)
            bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl_buys) > gap and bar["high"] >= bl_buys[gap].entry_price:
            outer = bl_buys[0]
            ref = bl_buys[gap].entry_price
            close_px = ref + (bar["high"] - ref) * alpha
            bl_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px))
            closed_this_bar.append(("BUY", outer.entry_price))
            bl_tickets.remove(outer)
            bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        
        if not bl_tickets and abs(bar["close"] - bl_anchor) >= base_step_px:
            bl_anchor = bar["close"]
            bl_next_sell = bl_anchor + base_step_px
            bl_next_buy = bl_anchor - base_step_px
        
        # Churn entries
        churn_os = sum(1 for t in churn_tickets if t.direction == "SELL")
        churn_ob = sum(1 for t in churn_tickets if t.direction == "BUY")
        
        for direction, closed_price in closed_this_bar:
            count = churn_os if direction == "SELL" else churn_ob
            if count >= 30:
                continue
            if momentum_gate:
                if direction == "SELL" and bar["close"] >= closed_price:
                    churn_skipped_mom += 1
                    continue
                if direction == "BUY" and bar["close"] <= closed_price:
                    churn_skipped_mom += 1
                    continue
            churn_tickets.append(ChurnTicket(direction=direction, entry_price=closed_price, opened_idx=idx))
            if direction == "SELL": churn_os += 1
            else: churn_ob += 1
        
        # Churn closes
        cs = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            outer = cs[0]; ref = cs[gap].entry_price
            close_px = ref + (bar["low"] - ref) * alpha
            churn_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px))
            churn_tickets.remove(outer)
            cs = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        cb = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            outer = cb[0]; ref = cb[gap].entry_price
            close_px = ref + (bar["high"] - ref) * alpha
            churn_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px))
            churn_tickets.remove(outer)
            cb = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
    
    bl_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in bl_tickets]
    churn_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn_tickets]
    
    return {
        "combined": sum(bl_realized)+sum(bl_float)+sum(churn_realized)+sum(churn_float),
        "bl_combined": sum(bl_realized)+sum(bl_float),
        "churn_combined": sum(churn_realized)+sum(churn_float),
        "bl_closes": len(bl_realized),
        "churn_closes": len(churn_realized),
        "churn_skipped_mom": churn_skipped_mom,
    }


def main():
    mt5.initialize()
    cfg_map = default_raw_configs()
    
    print(f"\n{'='*80}")
    print(f"  Step=1.0/gap=3 + Momentum + Alpha Combo Sweep")
    print(f"{'='*80}")
    
    configs = [
        ("baseline_2_2_a0", 2.0, 2, 0.0, False),
        ("optimal_1_3_a0", 1.0, 3, 0.0, False),
        ("optimal_1_3_a50", 1.0, 3, 0.5, False),
        ("optimal_1_3_a50_mom", 1.0, 3, 0.5, True),
    ]
    
    all_rows = []
    for name, step, gap, alpha, mom in configs:
        total = 0.0
        details = []
        total_skip = 0
        for sym in SYMBOLS:
            info = mt5.symbol_info(sym)
            bars = load_bars(sym, 60)
            r = simulate_combo(sym, bars, info, step, gap, alpha, mom)
            total += r["combined"]
            total_skip += r["churn_skipped_mom"]
            details.append(f"{sym}: ${r['combined']:.2f} (bl=${r['bl_combined']:.2f}, churn=${r['churn_combined']:+.2f})")
        
        bl_total = 0
        for sym in SYMBOLS:
            info = mt5.symbol_info(sym)
            bars = load_bars(sym, 60)
            cfg = RawConfig(step_pips=cfg_map[sym].step_pips, max_open_per_side=cfg_map[sym].max_open_per_side, close_mode="two_level")
            bl = simulate_raw_close2(sym, bars, info, cfg)
            bl_total += float(bl["combined_net_usd"])
        
        delta = total - bl_total
        mult = total / bl_total if bl_total > 0 else 0
        all_rows.append({"name": name, "step": step, "gap": gap, "alpha": alpha, "mom": mom,
                         "total": total, "delta": delta, "mult": mult, "skip": total_skip})
        skip_str = f" [skipped {total_skip} momentum]" if mom else ""
        print(f"\n  {name}: ${total:,.2f} ({mult:.2f}x baseline) Δ=${delta:,.2f}{skip_str}")
        for d in details:
            print(f"    {d}")
    
    print(f"\n{'='*80}")
    print(f"  Summary")
    print(f"{'='*80}")
    for r in sorted(all_rows, key=lambda x: x["total"], reverse=True):
        print(f"  {r['name']:25s} ${r['total']:>12,.2f}  {r['mult']:.2f}x  Δ=${r['delta']:>+11,.2f}")
    
    best = max(all_rows, key=lambda r: r["total"])
    print(f"\n🏆 Best: {best['name']} → ${best['total']:,.2f} ({best['mult']:.2f}x baseline)")
    
    out = ROOT / "reports" / "step1_gap3_momentum_combo.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        w.writeheader(); w.writerows(all_rows)
    
    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
