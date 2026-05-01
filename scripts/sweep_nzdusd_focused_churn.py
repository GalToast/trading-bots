#!/usr/bin/env python3
"""NZDUSD churn focused sweep — only the patterns that work."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


@dataclass
class Variant:
    name: str
    churn_entry_mode: str
    churn_close_gap: int
    churn_close_mode: str
    churn_fixed_steps: int
    churn_max_per_side: int


VARIANTS = [
    # Closed-level entries with different churn close gaps
    Variant(name="closed_gap1", churn_entry_mode="closed_levels", churn_close_gap=1, churn_close_mode="gap", churn_fixed_steps=0, churn_max_per_side=30),
    Variant(name="closed_gap2", churn_entry_mode="closed_levels", churn_close_gap=2, churn_close_mode="gap", churn_fixed_steps=0, churn_max_per_side=30),
    Variant(name="closed_gap3", churn_entry_mode="closed_levels", churn_close_gap=3, churn_close_mode="gap", churn_fixed_steps=0, churn_max_per_side=30),

    # Closed-level entries with fixed profit targets
    Variant(name="closed_fix1", churn_entry_mode="closed_levels", churn_close_gap=0, churn_close_mode="fixed_steps", churn_fixed_steps=1, churn_max_per_side=30),
    Variant(name="closed_fix2", churn_entry_mode="closed_levels", churn_close_gap=0, churn_close_mode="fixed_steps", churn_fixed_steps=2, churn_max_per_side=30),
    Variant(name="closed_fix3", churn_entry_mode="closed_levels", churn_close_gap=0, churn_close_mode="fixed_steps", churn_fixed_steps=3, churn_max_per_side=30),
    Variant(name="closed_fix5", churn_entry_mode="closed_levels", churn_close_gap=0, churn_close_mode="fixed_steps", churn_fixed_steps=5, churn_max_per_side=30),

    # All-interior entries (every full step) with gap closes
    Variant(name="interior_gap1", churn_entry_mode="all_interior", churn_close_gap=1, churn_close_mode="gap", churn_fixed_steps=0, churn_max_per_side=30),
    Variant(name="interior_gap2", churn_entry_mode="all_interior", churn_close_gap=2, churn_close_mode="gap", churn_fixed_steps=0, churn_max_per_side=30),

    # All-interior with fixed targets
    Variant(name="interior_fix2", churn_entry_mode="all_interior", churn_close_gap=0, churn_close_mode="fixed_steps", churn_fixed_steps=2, churn_max_per_side=30),
    Variant(name="interior_fix3", churn_entry_mode="all_interior", churn_close_gap=0, churn_close_mode="fixed_steps", churn_fixed_steps=3, churn_max_per_side=30),

    # Baseline
    Variant(name="baseline_only", churn_entry_mode="", churn_close_gap=0, churn_close_mode="", churn_fixed_steps=0, churn_max_per_side=0),
]


def simulate(symbol, bars, symbol_info, cfg, variant):
    if not bars:
        return {}
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    bl_anchor = bars[0]["close"]
    bl_next_sell = bl_anchor + base_step_px
    bl_next_buy = bl_anchor - base_step_px
    bl_tickets: list[Ticket] = []
    bl_realized: list[float] = []

    churn_tickets: list[ChurnTicket] = []
    churn_realized: list[float] = []
    churn_rearm_opens = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Baseline entries
        bl_os = sum(1 for t in bl_tickets if t.direction == "SELL")
        bl_ob = sum(1 for t in bl_tickets if t.direction == "BUY")
        bl_ss = dynamic_step(base_step_px, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bl_bs = dynamic_step(base_step_px, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        while bar["high"] >= bl_next_sell and bl_os < cfg.max_open_per_side:
            bl_tickets.append(Ticket(direction="SELL", entry_price=bl_next_sell, opened_idx=idx))
            bl_os += 1
            bl_ss = dynamic_step(base_step_px, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_sell += bl_ss
        while bar["low"] <= bl_next_buy and bl_ob < cfg.max_open_per_side:
            bl_tickets.append(Ticket(direction="BUY", entry_price=bl_next_buy, opened_idx=idx))
            bl_ob += 1
            bl_bs = dynamic_step(base_step_px, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_buy -= bl_bs

        # Baseline closes
        gap = 2
        closed_this_bar: list[tuple[str, float, int]] = []
        bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(bl_sells) > gap and bar["low"] <= bl_sells[gap].entry_price:
            outer = bl_sells[0]; ref = bl_sells[gap].entry_price
            bl_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
            level_idx = int(round((outer.entry_price - bl_anchor) / base_step_px))
            closed_this_bar.append(("SELL", outer.entry_price, level_idx))
            bl_tickets.remove(outer)
            bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl_buys) > gap and bar["high"] >= bl_buys[gap].entry_price:
            outer = bl_buys[0]; ref = bl_buys[gap].entry_price
            bl_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
            level_idx = int(round((bl_anchor - outer.entry_price) / base_step_px))
            closed_this_bar.append(("BUY", outer.entry_price, level_idx))
            bl_tickets.remove(outer)
            bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)

        if not bl_tickets and abs(bar["close"] - bl_anchor) >= base_step_px:
            bl_anchor = bar["close"]
            bl_next_sell = bl_anchor + base_step_px
            bl_next_buy = bl_anchor - base_step_px

        # Churn entries
        churn_os = sum(1 for t in churn_tickets if t.direction == "SELL")
        churn_ob = sum(1 for t in churn_tickets if t.direction == "BUY")

        if variant.churn_entry_mode == "closed_levels":
            for direction, closed_price, level_idx in closed_this_bar:
                count = churn_os if direction == "SELL" else churn_ob
                if count < variant.churn_max_per_side:
                    churn_tickets.append(ChurnTicket(direction=direction, entry_price=closed_price, opened_idx=idx))
                    churn_rearm_opens += 1
                    if direction == "SELL": churn_os += 1
                    else: churn_ob += 1
        elif variant.churn_entry_mode == "all_interior":
            bl_sells_all = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
            if bl_sells_all:
                outermost = bl_sells_all[0].entry_price
                for li in range(1, int(round((outermost - bl_anchor) / base_step_px)) + 1):
                    level = bl_anchor + li * base_step_px
                    exists = any(abs(t.entry_price - level) < 0.000001 and t.direction == "SELL" for t in churn_tickets)
                    if not exists and churn_os < variant.churn_max_per_side and bar["high"] >= level:
                        churn_tickets.append(ChurnTicket(direction="SELL", entry_price=level, opened_idx=idx))
                        churn_rearm_opens += 1; churn_os += 1
            bl_buys_all = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
            if bl_buys_all:
                outermost = bl_buys_all[0].entry_price
                for li in range(1, int(round((bl_anchor - outermost) / base_step_px)) + 1):
                    level = bl_anchor - li * base_step_px
                    exists = any(abs(t.entry_price - level) < 0.000001 and t.direction == "BUY" for t in churn_tickets)
                    if not exists and churn_ob < variant.churn_max_per_side and bar["low"] <= level:
                        churn_tickets.append(ChurnTicket(direction="BUY", entry_price=level, opened_idx=idx))
                        churn_rearm_opens += 1; churn_ob += 1

        # Churn closes
        if variant.churn_close_mode == "gap":
            cg = variant.churn_close_gap
            cs = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
            while len(cs) > cg and bar["low"] <= cs[cg].entry_price:
                outer = cs[0]; ref = cs[cg].entry_price
                churn_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
                churn_tickets.remove(outer)
                cs = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
            cb = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
            while len(cb) > cg and bar["high"] >= cb[cg].entry_price:
                outer = cb[0]; ref = cb[cg].entry_price
                churn_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
                churn_tickets.remove(outer)
                cb = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        elif variant.churn_close_mode == "fixed_steps":
            steps = variant.churn_fixed_steps
            for t in list(churn_tickets):
                if t.direction == "SELL":
                    tp = t.entry_price - steps * base_step_px
                    if bar["low"] <= tp:
                        churn_realized.append(unit_pnl_usd(symbol, "SELL", t.entry_price, tp, spread_px))
                        churn_tickets.remove(t)
                else:
                    tp = t.entry_price + steps * base_step_px
                    if bar["high"] >= tp:
                        churn_realized.append(unit_pnl_usd(symbol, "BUY", t.entry_price, tp, spread_px))
                        churn_tickets.remove(t)

    bl_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in bl_tickets]
    churn_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn_tickets]

    return {
        "bl_combined": sum(bl_realized)+sum(bl_float), "bl_closes": len(bl_realized),
        "churn_realized": sum(churn_realized), "churn_floating": sum(churn_float),
        "churn_combined": sum(churn_realized)+sum(churn_float), "churn_closes": len(churn_realized),
        "churn_rearm_opens": churn_rearm_opens,
        "total_combined": sum(bl_realized)+sum(bl_float)+sum(churn_realized)+sum(churn_float),
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1
    try:
        cfg_map = default_raw_configs()
        info = mt5.symbol_info("NZDUSD")
        bars = load_bars("NZDUSD", 60)
        raw_cfg = RawConfig(step_pips=1.5, max_open_per_side=12, close_mode="two_level")
        true_bl = simulate_raw_close2("NZDUSD", bars, info, raw_cfg)
        baseline_usd = float(true_bl["combined_net_usd"])

        print(f"\n{'='*75}")
        print(f"  NZDUSD Focused Churn Sweep — 60d baseline ${baseline_usd:.2f}")
        print(f"{'='*75}")

        rows = []
        for v in VARIANTS:
            if v.name == "baseline_only":
                r = {"bl_combined": baseline_usd, "bl_closes": true_bl["realized_closes"],
                     "churn_combined": 0, "churn_realized": 0, "churn_floating": 0,
                     "churn_closes": 0, "churn_rearm_opens": 0, "total_combined": baseline_usd}
            else:
                r = simulate("NZDUSD", bars, info, raw_cfg, v)

            delta = r["total_combined"] - baseline_usd
            rows.append({"variant": v.name, "baseline": round(baseline_usd,3),
                "bl": round(r["bl_combined"],3), "churn": round(r["churn_combined"],3),
                "churn_realized": round(r["churn_realized"],3), "churn_floating": round(r["churn_floating"],3),
                "churn_closes": r["churn_closes"], "rearm_opens": r["churn_rearm_opens"],
                "total": round(r["total_combined"],3), "delta": round(delta,3), "beats": delta>0})
            m = "✅" if delta > 0 else "❌"
            print(f"  {m} {v.name:25s} total=${r['total_combined']:>10.2f}  delta=${delta:>+8.2f}  "
                  f"bl=${r['bl_combined']:.2f}  churn=${r['churn_combined']:+.2f}({r['churn_closes']}c)")

        out = ROOT / "reports" / "nzdusd_focused_churn.csv"
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader(); w.writerows(rows)

        winners = [r for r in rows if r["beats"]]
        if winners:
            best = max(winners, key=lambda r: r["delta"])
            print(f"\n🏆 Best: {best['variant']} → ${best['total']:.2f} (+${best['delta']:.2f})")
        else:
            print(f"\n⚠️  None beat baseline.")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
