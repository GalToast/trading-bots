#!/usr/bin/env python3
"""Isolate the close_gap_1 effect vs trailing_partial to find the true NZDUSD winner."""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

SYMBOL = "NZDUSD"
DAYS = 60


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
            outer = sells[0]
            ref = sells[gap].entry_price
            realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
            positions.remove(outer)
            sells = sorted([t for t in positions if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)

        buys = sorted([t for t in positions if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            ref = buys[gap].entry_price
            realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
            positions.remove(outer)
            buys = sorted([t for t in positions if t.direction=="BUY"], key=lambda t:t.entry_price)

        max_open = max(max_open, len(positions))
        if not positions and abs(bar["close"]-anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell = anchor + base_step_px
            next_buy = anchor - base_step_px

    floating = [unit_pnl_usd(symbol,t.direction,t.entry_price,bars[-1]["close"],spread_px) for t in positions]
    return {
        "realized": sum(realized),
        "floating": sum(floating),
        "combined": sum(realized)+sum(floating),
        "closes": len(realized),
        "max_open": max_open,
    }


mt5.initialize()
info = mt5.symbol_info(SYMBOL)
bars = load_bars(SYMBOL, DAYS)
cfg = RawConfig(step_pips=1.5, max_open_per_side=12, close_mode="two_level")

true_bl = simulate_raw_close2(SYMBOL, bars, info, cfg)
print(f"\n{'='*50}")
print(f"  NZDUSD Gap Sweep")
print(f"{'='*50}")

results = []
for gap in [1, 2, 3]:
    r = simulate_with_gap(SYMBOL, bars, info, cfg, gap_override=gap)
    delta = r["combined"] - float(true_bl["combined_net_usd"])
    results.append({"gap": gap, **r, "delta": delta})
    marker = "✅" if delta > 0 else "❌"
    print(f"  {marker} gap={gap}: ${r['combined']:>10.2f}  delta=${delta:>+8.2f}  closes={r['closes']}  avg=${r['realized']/r['closes']:.4f}  max_open={r['max_open']}")

# Also test: gap=1 but only for one side
for side_gap in ["sell1_buy2", "sell2_buy1"]:
    # Custom: different gap per side
    pip_size = pip_size_for(info)
    spread_px = spread_price(info)
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

        sell_gap = 1 if "sell1" in side_gap else 2
        buy_gap = 1 if "buy1" in side_gap else 2

        sells = sorted([t for t in positions if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(sells) > sell_gap and bar["low"] <= sells[sell_gap].entry_price:
            outer = sells[0]; ref = sells[sell_gap].entry_price
            realized.append(unit_pnl_usd(SYMBOL,"SELL",outer.entry_price,ref,spread_px))
            positions.remove(outer)
            sells = sorted([t for t in positions if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        buys = sorted([t for t in positions if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(buys) > buy_gap and bar["high"] >= buys[buy_gap].entry_price:
            outer = buys[0]; ref = buys[buy_gap].entry_price
            realized.append(unit_pnl_usd(SYMBOL,"BUY",outer.entry_price,ref,spread_px))
            positions.remove(outer)
            buys = sorted([t for t in positions if t.direction=="BUY"], key=lambda t:t.entry_price)
        max_open = max(max_open, len(positions))
        if not positions and abs(bar["close"]-anchor) >= base_step_px:
            anchor = bar["close"]; next_sell = anchor+base_step_px; next_buy = anchor-base_step_px

    floating = [unit_pnl_usd(SYMBOL,t.direction,t.entry_price,bars[-1]["close"],spread_px) for t in positions]
    combined = sum(realized)+sum(floating)
    delta = combined - float(true_bl["combined_net_usd"])
    print(f"  {'✅' if delta>0 else '❌'} {side_gap:15s}: ${combined:>10.2f}  delta=${delta:>+8.2f}  closes={len(realized)}  avg=${sum(realized)/len(realized):.4f}  max_open={max_open}")
    results.append({"gap": side_gap, "combined": combined, "closes": len(realized), "delta": delta})

# Sweep gap=1 with different step_pips
print(f"\n  gap=1 step sweep:")
for step in [1.0, 1.5, 2.0, 2.5, 3.0]:
    cfg2 = RawConfig(step_pips=step, max_open_per_side=12, close_mode="two_level")
    r = simulate_with_gap(SYMBOL, bars, info, cfg2, gap_override=1)
    delta = r["combined"] - float(true_bl["combined_net_usd"])
    print(f"    {'✅' if delta>0 else '❌'} step={step}pips: ${r['combined']:>10.2f}  delta=${delta:>+8.2f}  closes={r['closes']}  avg=${r['realized']/r['closes']:.4f}")

mt5.shutdown()
