#!/usr/bin/env python3
"""
FX Range Formula — Deeper Analysis
===================================
Look at the FX deep optimization data to find the optimal coefficient.
"""
import csv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Typical FX M15 ranges (estimated)
FX_RANGE = {
    "GBPUSD": 0.0012,
    "EURUSD": 0.0010,
    "NZDUSD": 0.0009,
}

# Load deep optimization
fx_deep = REPO / "reports" / "fx_m15_deep_opt.csv"
if fx_deep.exists():
    print("FX M15 DEEP OPTIMIZATION ANALYSIS")
    print("=" * 80)
    
    rows = []
    with open(fx_deep) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    
    # Group by symbol
    by_symbol = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(row)
    
    for symbol, sym_rows in by_symbol.items():
        print(f"\n{symbol} (typical M15 range = {FX_RANGE.get(symbol, 'N/A')}):")
        print(f"  {'Step':>8} {'Closes':>7} {'Net $':>10} {'$/close':>9} {'Range-x':>7} {'$/hr':>8}")
        print(f"  {'-'*8} {'-'*7} {'-'*10} {'-'*9} {'-'*7} {'-'*8}")
        
        for row in sym_rows:
            step = float(row["step"])
            closes = int(row.get("closes", 0))
            net = float(row.get("realized_usd", 0))
            dollar_per_close = net / closes if closes > 0 else 0
            
            # Assume 6714 bars = 6714 * 15 min = ~70 days of data
            # 70 days * 24 hrs = 1680 hours
            # Actually let's just use $/close as the metric
            range_coeff = step / FX_RANGE.get(symbol, 0.001) if FX_RANGE.get(symbol) else 0
            
            gate = row.get("momentum_gate", "?")
            print(f"  ${step:>7.4f} {closes:>7} ${net:>9.2f} ${dollar_per_close:>8.2f} {range_coeff:>6.2f}x")
    
    print()
    print("=" * 80)
    print("OPTIMAL STEP BY $/CLOSE:")
    print("-" * 80)
    
    for symbol, sym_rows in by_symbol.items():
        best = max(sym_rows, key=lambda r: float(r.get("realized_usd", 0)) / max(int(r.get("closes", 0)), 1))
        best_step = float(best["step"])
        best_closes = int(best.get("closes", 0))
        best_net = float(best.get("realized_usd", 0))
        best_dpc = best_net / best_closes if best_closes > 0 else 0
        range_coeff = best_step / FX_RANGE.get(symbol, 0.001) if FX_RANGE.get(symbol) else 0
        print(f"  {symbol}: step={best_step:.4f} ({range_coeff:.2f}x Range), ${best_dpc:.2f}/close, {best_closes} closes")
    
    print()
    print("OPTIMAL STEP BY TOTAL $:")
    print("-" * 80)
    
    for symbol, sym_rows in by_symbol.items():
        best = max(sym_rows, key=lambda r: float(r.get("realized_usd", 0)))
        best_step = float(best["step"])
        best_closes = int(best.get("closes", 0))
        best_net = float(best.get("realized_usd", 0))
        best_dpc = best_net / best_closes if best_closes > 0 else 0
        range_coeff = best_step / FX_RANGE.get(symbol, 0.001) if FX_RANGE.get(symbol) else 0
        print(f"  {symbol}: step={best_step:.4f} ({range_coeff:.2f}x Range), ${best_dpc:.2f}/close, {best_closes} closes, ${best_net:.0f} total")
