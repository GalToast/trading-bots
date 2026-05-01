#!/usr/bin/env python3
"""Combined basket test: isolated churn on all 3 symbols."""
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
    __slots__ = ("direction","entry_price","opened_idx")
    def __init__(self, direction, entry_price, opened_idx):
        self.direction = direction
        self.entry_price = entry_price
        self.opened_idx = opened_idx


def simulate_with_churn(symbol, bars, symbol_info, cfg, churn_gap):
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

    for idx in range(1, len(bars)):
        bar = bars[idx]
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

        gap = 2
        closed_this_bar = []
        bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(bl_sells) > gap and bar["low"] <= bl_sells[gap].entry_price:
            outer = bl_sells[0]; ref = bl_sells[gap].entry_price
            bl_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
            closed_this_bar.append(("SELL", outer.entry_price))
            bl_tickets.remove(outer)
            bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl_buys) > gap and bar["high"] >= bl_buys[gap].entry_price:
            outer = bl_buys[0]; ref = bl_buys[gap].entry_price
            bl_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
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
            if count < 30:
                churn_tickets.append(ChurnTicket(direction=direction, entry_price=closed_price, opened_idx=idx))
                if direction == "SELL": churn_os += 1
                else: churn_ob += 1

        # Churn closes
        cs = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(cs) > churn_gap and bar["low"] <= cs[churn_gap].entry_price:
            outer = cs[0]; ref = cs[churn_gap].entry_price
            churn_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
            churn_tickets.remove(outer)
            cs = sorted([t for t in churn_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        cb = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(cb) > churn_gap and bar["high"] >= cb[churn_gap].entry_price:
            outer = cb[0]; ref = cb[churn_gap].entry_price
            churn_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
            churn_tickets.remove(outer)
            cb = sorted([t for t in churn_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)

    bl_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in bl_tickets]
    churn_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn_tickets]

    return {
        "bl_combined": sum(bl_realized)+sum(bl_float), "bl_closes": len(bl_realized),
        "churn_combined": sum(churn_realized)+sum(churn_float),
        "churn_realized": sum(churn_realized), "churn_floating": sum(churn_float),
        "churn_closes": len(churn_realized),
        "total": sum(bl_realized)+sum(bl_float)+sum(churn_realized)+sum(churn_float),
    }


mt5.initialize()
cfg_map = default_raw_configs()

print(f"\n{'='*80}")
print(f"  Combined Basket: GBPUSD + EURUSD + NZDUSD (60d)")
print(f"{'='*80}")

configs = [
    ("all_baseline", {s: 0 for s in SYMBOLS}),
    ("all_churn6", {s: 6 for s in SYMBOLS}),
    ("all_churn5", {s: 5 for s in SYMBOLS}),
    ("nzd6_gbp5_eur5", {"GBPUSD": 5, "EURUSD": 5, "NZDUSD": 6}),
    ("nzd6_gbp6_eur6", {"GBPUSD": 6, "EURUSD": 6, "NZDUSD": 6}),
    ("nzd6_gbp4_eur4", {"GBPUSD": 4, "EURUSD": 4, "NZDUSD": 6}),
    ("nzd5_gbp6_eur5", {"GBPUSD": 6, "EURUSD": 5, "NZDUSD": 5}),
]

rows = []
for name, churn_gaps in configs:
    total_bl = 0.0
    total_churn = 0.0
    total_combined = 0.0
    total_closes = 0
    details = []
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        cfg = RawConfig(step_pips=cfg_map[sym].step_pips, max_open_per_side=cfg_map[sym].max_open_per_side, close_mode="two_level")
        cg = churn_gaps[sym]

        if cg == 0:
            bl = simulate_raw_close2(sym, bars, info, cfg)
            r = {"bl_combined": float(bl["combined_net_usd"]), "bl_closes": bl["realized_closes"],
                 "churn_combined": 0, "churn_realized": 0, "churn_floating": 0, "churn_closes": 0,
                 "total": float(bl["combined_net_usd"])}
        else:
            r = simulate_with_churn(sym, bars, info, cfg, churn_gap=cg)

        total_bl += r["bl_combined"]
        total_churn += r["churn_combined"]
        total_combined += r["total"]
        total_closes += r["bl_closes"] + r["churn_closes"]
        churn_str = f"churn=${r['churn_combined']:+.2f}({r['churn_closes']}c)" if cg > 0 else "baseline-only"
        details.append(f"{sym}: ${r['total']:.2f} (bl=${r['bl_combined']:.2f}, {churn_str})")

    delta = total_combined - sum(
        float(simulate_raw_close2(s, load_bars(s, 60), mt5.symbol_info(s),
            RawConfig(step_pips=cfg_map[s].step_pips, max_open_per_side=cfg_map[s].max_open_per_side, close_mode="two_level"))["combined_net_usd"])
        for s in SYMBOLS
    )

    rows.append({"config": name, "total": round(total_combined,3), "bl": round(total_bl,3),
                 "churn": round(total_churn,3), "closes": total_closes, "delta": round(delta,3)})

    print(f"\n  {name}: ${total_combined:.2f} (delta ${delta:+.2f}, {total_closes} closes)")
    for d in details:
        print(f"    {d}")

out = ROOT / "reports" / "combined_basket_churn.csv"
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader(); w.writerows(rows)

best = max(rows, key=lambda r: r["delta"])
print(f"\n🏆 Best config: {best['config']} → ${best['total']:.2f} (+${best['delta']:.2f})")

mt5.shutdown()
