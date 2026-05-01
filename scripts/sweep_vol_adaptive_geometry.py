#!/usr/bin/env python3
"""
Volatility-adaptive step sizing + regime-adaptive close gap sweep.

Tests whether different ATR regimes need different lattice geometry.
The core insight: a single step_pips/gap config can't be optimal for both
quiet and volatile markets.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import math

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


def atr_for_bars(bars: list[dict], period: int = 14) -> list[float]:
    """Compute ATR for each bar (forward-filled)."""
    if len(bars) < period + 1:
        return [0.0] * len(bars)
    
    true_ranges = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i-1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    
    atrs = []
    # Initial ATR = SMA of first `period` true ranges
    atr = sum(true_ranges[:period]) / period
    atrs.append(atr)
    
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period
        atrs.append(atr)
    
    # Pad first `period` bars
    result = [atrs[0]] * period + atrs
    
    return result


def simulate_with_params(symbol, bars, symbol_info, step_pips, max_open_per_side, close_gap, alpha, momentum_gate, bars_atr=None, atr_threshold=None, use_high_atr_params=None, use_low_atr_params=None):
    """Simulate with optional volatility-adaptive geometry."""
    if not bars:
        return {}
    
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
    
    for idx in range(1, len(bars)):
        bar = bars[idx]
        
        # Determine current params based on ATR regime
        current_step_pips = step_pips
        current_close_gap = close_gap
        current_max_open = max_open_per_side
        
        if use_high_atr_params and bars_atr is not None and atr_threshold is not None:
            atr_pips = bars_atr[idx] / pip_size if idx < len(bars_atr) else 0
            if atr_pips >= atr_threshold:
                current_step_pips, current_close_gap = use_high_atr_params
            else:
                # Low vol: use default params (already set)
                pass
        
        current_base_step = current_step_pips * pip_size
        
        # For adaptive params, we need to recalculate levels
        # This is a simplified approach — in production you'd recalculate the whole lattice
        # For now, just test: does the right geometry in the right regime beat static?
        
        bl_os = sum(1 for t in bl_tickets if t.direction == "SELL")
        bl_ob = sum(1 for t in bl_tickets if t.direction == "BUY")
        bl_ss = dynamic_step(current_base_step, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bl_bs = dynamic_step(current_base_step, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        while bar["high"] >= bl_next_sell and bl_os < current_max_open:
            bl_tickets.append(Ticket(direction="SELL", entry_price=bl_next_sell, opened_idx=idx))
            bl_os += 1
            bl_ss = dynamic_step(current_base_step, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_sell += bl_ss
        while bar["low"] <= bl_next_buy and bl_ob < current_max_open:
            bl_tickets.append(Ticket(direction="BUY", entry_price=bl_next_buy, opened_idx=idx))
            bl_ob += 1
            bl_bs = dynamic_step(current_base_step, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_buy -= bl_bs
        
        # Close
        bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(bl_sells) > current_close_gap and bar["low"] <= bl_sells[current_close_gap].entry_price:
            outer = bl_sells[0]
            ref = bl_sells[current_close_gap].entry_price
            close_px = ref + (bar["low"] - ref) * alpha
            bl_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px))
            closed_this_bar = True
            bl_tickets.remove(outer)
            bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        
        bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl_buys) > current_close_gap and bar["high"] >= bl_buys[current_close_gap].entry_price:
            outer = bl_buys[0]
            ref = bl_buys[current_close_gap].entry_price
            close_px = ref + (bar["high"] - ref) * alpha
            bl_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px))
            bl_tickets.remove(outer)
            bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        
        # Churn entries at closed levels (simplified)
        # ... (would need to track closed levels for full implementation)
    
    bl_float = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in bl_tickets]
    return {
        "combined": sum(bl_realized) + sum(bl_float),
        "realized": sum(bl_realized),
        "floating": sum(bl_float),
        "closes": len(bl_realized),
    }


def main():
    mt5.initialize()
    cfg_map = default_raw_configs()
    
    # Phase 1: Test static geometry combos to find the per-regime optimum
    print(f"\n{'='*75}")
    print(f"  Vol-Adaptive Geometry Sweep — Static Baseline Per-Symbol (60d, α=0.50)")
    print(f"{'='*75}")
    
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        atrs = atr_for_bars(bars, period=14)
        pip_size = pip_size_for(info)

        # Median ATR in pips
        atr_pips = [a / pip_size for a in atrs[14:]]
        median_atr = sorted(atr_pips)[len(atr_pips)//2]
        
        print(f"\n  {sym}: median ATR = {median_atr:.1f} pips, bar count = {len(bars)}")
        
        # Test static configs
        for step in [1.0, 1.5, 2.0, 2.5, 3.0]:
            for gap in [1, 2, 3]:
                r = simulate_with_params(sym, bars, info, step, 20, gap, alpha=0.5, momentum_gate=False)
                marker = "  "
                if gap == 2 and step == cfg_map[sym].step_pips:
                    marker = "→ "
                print(f"    {marker}step={step}pips gap={gap}: ${r['combined']:>10.2f} ({r['closes']} closes)")


if __name__ == "__main__":
    raise SystemExit(main())
