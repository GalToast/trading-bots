#!/usr/bin/env python3
"""
NZDUSD inside-churn v3: Aggressive isolated churn with micro-lattice closes.

Builds on the v2 isolated dual-lattice proof-of-concept (+$212) but amplifies by:
1. Churn enters at ALL interior levels (not just closed levels) — every half-step between baseline levels
2. Churn positions close against EACH OTHER using gap=1 (mini-lattice within the lattice)
3. Baseline runs completely unaffected
"""
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
    churn_entry_mode: str = "closed_levels"  # "closed_levels", "half_steps", "quarter_steps", "all_interior"
    churn_close_gap: int = 1  # gap for churn internal closes (1 = close outer vs 2nd)
    churn_close_mode: str = "gap"  # "gap" = close against churn[n], "fixed_steps" = fixed profit target
    churn_fixed_steps: int = 2  # for close_mode="fixed_steps"
    churn_max_per_side: int = 30


VARIANTS = [
    # 1) Enter at closed levels, close with gap=1 (micro-lattice)
    Variant(name="closed_gap1", churn_entry_mode="closed_levels", churn_close_gap=1, churn_close_mode="gap"),
    # 2) Enter at closed levels, close with gap=2
    Variant(name="closed_gap2", churn_entry_mode="closed_levels", churn_close_gap=2, churn_close_mode="gap"),
    # 3) Enter at half-steps, close gap=1
    Variant(name="halfstep_gap1", churn_entry_mode="half_steps", churn_close_gap=1, churn_close_mode="gap"),
    # 4) Enter at half-steps, close gap=2
    Variant(name="halfstep_gap2", churn_entry_mode="half_steps", churn_close_gap=2, churn_close_mode="gap"),
    # 5) Enter at half-steps, fixed 1-step profit target
    Variant(name="halfstep_fix1", churn_entry_mode="half_steps", churn_close_mode="fixed_steps", churn_fixed_steps=1),
    # 6) Enter at half-steps, fixed 2-step profit target
    Variant(name="halfstep_fix2", churn_entry_mode="half_steps", churn_close_mode="fixed_steps", churn_fixed_steps=2),
    # 7) Enter at quarter-steps, close gap=1
    Variant(name="quarterstep_gap1", churn_entry_mode="quarter_steps", churn_close_gap=1, churn_close_mode="gap"),
    # 8) Enter at quarter-steps, fixed 1-step profit
    Variant(name="quarterstep_fix1", churn_entry_mode="quarter_steps", churn_close_mode="fixed_steps", churn_fixed_steps=1),
    # 9) Enter at ALL interior levels (every step between anchor and outer), close gap=1
    Variant(name="interior_gap1", churn_entry_mode="all_interior", churn_close_gap=1, churn_close_mode="gap"),
    # 10) Enter at ALL interior levels, fixed 2-step profit
    Variant(name="interior_fix2", churn_entry_mode="all_interior", churn_close_mode="fixed_steps", churn_fixed_steps=2),
    # 11) Enter at closed levels, fixed 5-step profit (bigger targets)
    Variant(name="closed_fix5", churn_entry_mode="closed_levels", churn_close_mode="fixed_steps", churn_fixed_steps=5),
    # 12) Baseline only
    Variant(name="baseline_only"),
]


def simulate_aggressive_churn(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant
) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    # === BASELINE (completely independent) ===
    bl_anchor = bars[0]["close"]
    bl_next_sell = bl_anchor + base_step_px
    bl_next_buy = bl_anchor - base_step_px
    bl_tickets: list[Ticket] = []
    bl_realized: list[float] = []

    # === CHURN (separate) ===
    churn_tickets: list[ChurnTicket] = []
    churn_realized: list[float] = []
    churn_rearm_opens = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # --- Baseline entries ---
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

        # --- Baseline closes ---
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

        # === CHURN ENTRIES ===
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

        elif variant.churn_entry_mode in ("half_steps", "quarter_steps"):
            step_divisor = 2 if variant.churn_entry_mode == "half_steps" else 4
            mini_step = base_step_px / step_divisor
            # Sells: between anchor and bl_next_sell
            max_level = int(round((bl_next_sell - bl_anchor) / mini_step))
            for li in range(1, max_level):
                level = bl_anchor + li * mini_step
                exists = any(abs(t.entry_price - level) < 0.000001 and t.direction == "SELL" for t in churn_tickets)
                if not exists and churn_os < variant.churn_max_per_side and bar["high"] >= level:
                    churn_tickets.append(ChurnTicket(direction="SELL", entry_price=level, opened_idx=idx))
                    churn_rearm_opens += 1; churn_os += 1
            # Buys: between anchor and bl_next_buy
            max_level_buy = int(round((bl_anchor - bl_next_buy) / mini_step))
            for li in range(1, max_level_buy):
                level = bl_anchor - li * mini_step
                exists = any(abs(t.entry_price - level) < 0.000001 and t.direction == "BUY" for t in churn_tickets)
                if not exists and churn_ob < variant.churn_max_per_side and bar["low"] <= level:
                    churn_tickets.append(ChurnTicket(direction="BUY", entry_price=level, opened_idx=idx))
                    churn_rearm_opens += 1; churn_ob += 1

        elif variant.churn_entry_mode == "all_interior":
            # Enter at every full step between anchor and current outer
            bl_sells_all = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
            if bl_sells_all:
                outermost_sell = bl_sells_all[0].entry_price
                for li in range(1, int(round((outermost_sell - bl_anchor) / base_step_px))):
                    level = bl_anchor + li * base_step_px
                    exists = any(abs(t.entry_price - level) < 0.000001 and t.direction == "SELL" for t in churn_tickets)
                    if not exists and churn_os < variant.churn_max_per_side and bar["high"] >= level:
                        churn_tickets.append(ChurnTicket(direction="SELL", entry_price=level, opened_idx=idx))
                        churn_rearm_opens += 1; churn_os += 1
            bl_buys_all = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
            if bl_buys_all:
                outermost_buy = bl_buys_all[0].entry_price
                for li in range(1, int(round((bl_anchor - outermost_buy) / base_step_px))):
                    level = bl_anchor - li * base_step_px
                    exists = any(abs(t.entry_price - level) < 0.000001 and t.direction == "BUY" for t in churn_tickets)
                    if not exists and churn_ob < variant.churn_max_per_side and bar["low"] <= level:
                        churn_tickets.append(ChurnTicket(direction="BUY", entry_price=level, opened_idx=idx))
                        churn_rearm_opens += 1; churn_ob += 1

        # === CHURN CLOSES ===
        if variant.churn_close_mode == "gap":
            churn_gap = variant.churn_close_gap
            c_sells = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
            while len(c_sells) > churn_gap and bar["low"] <= c_sells[churn_gap].entry_price:
                outer = c_sells[0]; ref = c_sells[churn_gap].entry_price
                churn_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
                churn_tickets.remove(outer)
                c_sells = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
            c_buys = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
            while len(c_buys) > churn_gap and bar["high"] >= c_buys[churn_gap].entry_price:
                outer = c_buys[0]; ref = c_buys[churn_gap].entry_price
                churn_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
                churn_tickets.remove(outer)
                c_buys = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        else:
            # Fixed step profit target
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

    bl_floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in bl_tickets]
    churn_floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn_tickets]

    return {
        "bl_realized": sum(bl_realized), "bl_floating": sum(bl_floating),
        "bl_combined": sum(bl_realized)+sum(bl_floating), "bl_closes": len(bl_realized),
        "churn_realized": sum(churn_realized), "churn_floating": sum(churn_floating),
        "churn_combined": sum(churn_realized)+sum(churn_floating), "churn_closes": len(churn_realized),
        "churn_rearm_opens": churn_rearm_opens,
        "total_combined": sum(bl_realized)+sum(bl_floating)+sum(churn_realized)+sum(churn_floating),
    }


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="NZDUSD")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--output-csv", default=str(ROOT / "reports" / "nzdusd_aggressive_churn_sweep.csv"))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed"); return 1

    try:
        symbol = args.symbol
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, args.days)
        raw_cfg = RawConfig(step_pips=cfg_map[symbol].step_pips, max_open_per_side=cfg_map[symbol].max_open_per_side, close_mode="two_level")
        true_bl = simulate_raw_close2(symbol, bars, info, raw_cfg)
        baseline_usd = float(true_bl["combined_net_usd"])

        print(f"\n{'='*75}")
        print(f"  NZDUSD Aggressive Churn Sweep — {args.days}d baseline ${baseline_usd:.2f}")
        print(f"{'='*75}")

        rows = []
        for v in VARIANTS:
            if v.name == "baseline_only":
                r = {"bl_combined": baseline_usd, "bl_closes": true_bl["realized_closes"],
                     "churn_combined": 0, "churn_realized": float(true_bl["realized_net_usd"]),
                     "churn_floating": float(true_bl["floating_net_usd"]), "churn_closes": 0,
                     "churn_rearm_opens": 0, "total_combined": baseline_usd}
            else:
                r = simulate_aggressive_churn(symbol, bars, info, raw_cfg, v)

            delta = r["total_combined"] - baseline_usd
            rows.append({"variant": v.name, "baseline_usd": round(baseline_usd, 3),
                "bl_combined": round(r["bl_combined"], 3), "churn_combined": round(r["churn_combined"], 3),
                "churn_realized": round(r["churn_realized"], 3), "churn_floating": round(r["churn_floating"], 3),
                "churn_closes": r["churn_closes"], "churn_rearm_opens": r["churn_rearm_opens"],
                "total_combined": round(r["total_combined"], 3), "delta_usd": round(delta, 3), "beats": delta > 0})
            m = "✅" if delta > 0 else "❌"
            c_str = f"churn=${r['churn_combined']:+.2f}({r['churn_closes']}c,{r['churn_rearm_opens']}ent)"
            print(f"  {m} {v.name:25s} total=${r['total_combined']:>10.2f}  delta=${delta:>+8.2f}  bl=${r['bl_combined']:.2f}  {c_str}")

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader(); w.writerows(rows)
        print(f"\nWrote {out_path}")

        winners = [r for r in rows if r["beats"]]
        if winners:
            best = max(winners, key=lambda r: r["delta_usd"])
            print(f"\n🏆 Best: {best['variant']} → ${best['total_combined']:.2f} (+${best['delta_usd']:.2f})")
            print(f"   Baseline: ${best['bl_combined']:.2f}, Churn: ${best['churn_combined']:.2f} ({best['churn_closes']} closes, {best['churn_rearm_opens']} entries)")
        else:
            print(f"\n⚠️  None beat baseline.")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
