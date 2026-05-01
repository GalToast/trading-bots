#!/usr/bin/env python3
"""Verify that NZDUSD gap optimization works in the combined basket."""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


def simulate_with_gap(symbol, bars, symbol_info, cfg, gap_override=None):
    if not bars:
        return {}
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    anchor = bars[0]["close"]
    next_sell = anchor + base_step_px
    next_buy = anchor - base_step_px
    positions: list[Ticket] = []
    realized: list[float] = []
    max_open = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ob = sum(1 for t in positions if t.direction == "BUY")
        os_ = sum(1 for t in positions if t.direction == "SELL")

        css = dynamic_step(base_step_px, os_, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        cbs = dynamic_step(base_step_px, ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())

        while bar["high"] >= next_sell and os_ < cfg.max_open_per_side:
            positions.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx))
            os_ += 1
            css = dynamic_step(base_step_px, os_, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            next_sell += css
        while bar["low"] <= next_buy and ob < cfg.max_open_per_side:
            positions.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx))
            ob += 1
            cbs = dynamic_step(base_step_px, ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            next_buy -= cbs

        gap = gap_override if gap_override is not None else (1 if cfg.close_mode == "one_level" else 2)

        sells = sorted([t for t in positions if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]; ref = sells[gap].entry_price
            realized.append(unit_pnl_usd(symbol,"SELL",outer.entry_price,ref,spread_px))
            positions.remove(outer)
            sells = sorted([t for t in positions if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        buys = sorted([t for t in positions if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]; ref = buys[gap].entry_price
            realized.append(unit_pnl_usd(symbol,"BUY",outer.entry_price,ref,spread_px))
            positions.remove(outer)
            buys = sorted([t for t in positions if t.direction=="BUY"], key=lambda t:t.entry_price)

        max_open = max(max_open, len(positions))
        if not positions and abs(bar["close"]-anchor) >= base_step_px:
            anchor = bar["close"]; next_sell = anchor+base_step_px; next_buy = anchor-base_step_px

    floating = [unit_pnl_usd(symbol,t.direction,t.entry_price,bars[-1]["close"],spread_px) for t in positions]
    return {
        "realized": sum(realized), "floating": sum(floating), "combined": sum(realized)+sum(floating),
        "closes": len(realized), "max_open": max_open,
    }


mt5.initialize()
cfg_map = default_raw_configs()

# Test: all symbols gap=2 (current baseline) vs NZDUSD gap=3
print(f"\n{'='*60}")
print(f"  Combined Basket Test — GBPUSD + EURUSD + NZDUSD (60d)")
print(f"{'='*60}")

configs = [
    ("all_gap2", {"GBPUSD": 2, "EURUSD": 2, "NZDUSD": 2}),
    ("nzd_gap3", {"GBPUSD": 2, "EURUSD": 2, "NZDUSD": 3}),
    ("all_gap3", {"GBPUSD": 3, "EURUSD": 3, "NZDUSD": 3}),
    ("nzd_gap3_eur_gap3", {"GBPUSD": 2, "EURUSD": 3, "NZDUSD": 3}),
]

for name, gaps in configs:
    total = 0.0
    total_closes = 0
    total_floating = 0.0
    details = []
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        cfg = RawConfig(step_pips=cfg_map[sym].step_pips, max_open_per_side=cfg_map[sym].max_open_per_side, close_mode="two_level")
        r = simulate_with_gap(sym, bars, info, cfg, gap_override=gaps[sym])
        total += r["combined"]
        total_closes += r["closes"]
        total_floating += r["floating"]
        details.append(f"{sym}: ${r['combined']:.2f} ({r['closes']} closes, gap={gaps[sym]})")

    marker = "🏆" if "nzd_gap3" in name and "all" not in name else "  "
    print(f"\n  {marker} {name}: ${total:.2f} ({total_closes} closes, floating ${total_floating:.2f})")
    for d in details:
        print(f"       {d}")

# Also test: true baseline from original code
print(f"\n  True baseline (original simulate_raw_close2):")
baseline_total = 0
for sym in SYMBOLS:
    info = mt5.symbol_info(sym)
    bars = load_bars(sym, 60)
    cfg = RawConfig(step_pips=cfg_map[sym].step_pips, max_open_per_side=cfg_map[sym].max_open_per_side, close_mode="two_level")
    r = simulate_raw_close2(sym, bars, info, cfg)
    baseline_total += float(r["combined_net_usd"])
    print(f"       {sym}: ${r['combined_net_usd']:.2f} ({r['realized_closes']} closes)")
print(f"       TOTAL: ${baseline_total:.2f}")

mt5.shutdown()
