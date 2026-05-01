#!/usr/bin/env python3
"""Sweep close gaps at $30/$50/$75 steps."""
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5

mt5.initialize()
bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 90)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]
total_hrs = len(bars) * 15 / 60

print(f"{'Step':>5} {'Gap':>4} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'$/hr':>8} | {'MaxOpen':>7}")
print("-" * 80)

for step, gap in [(75,1), (75,2), (75,3), (50,1), (50,2), (50,3), (30,1), (30,2), (30,3), (20,1), (20,2), (20,3), (15,1), (15,2), (15,3)]:
    cfg = {
        "step": float(step), "max_open_per_side": 60, "close_alpha": 1.0, "close_gap": gap,
        "momentum_gate": False, "rearm_variant": "rearm_lvl2_exc1", "rearm_cooldown_bars": 0,
        "timeframe": "M15",
    }
    state = init_symbol_state("BTCUSD", cfg, bars)
    state = process_symbol("BTCUSD", cfg, bars, state)
    closes = state.realized_closes
    net = state.realized_net_usd
    avg = net / closes if closes > 0 else 0
    per_hr = net / total_hrs
    max_open = state.max_open_total
    print(f"${step:>4}   {gap:>2} | {closes:>6} | ${avg:>7.2f} | ${net:>9.2f} | ${per_hr:>7.2f} | {max_open:>7}")

print("=" * 80)

# Find best overall
best = max([
    {"step": s, "gap": g}
    for s, g in [(75,1), (75,2), (75,3), (50,1), (50,2), (50,3), (30,1), (30,2), (30,3), (20,1), (20,2), (20,3), (15,1), (15,2), (15,3)]
], key=lambda x: 0)  # placeholder, compute below

# Recompute best
results = []
for step, gap in [(75,1), (75,2), (75,3), (50,1), (50,2), (50,3), (30,1), (30,2), (30,3), (20,1), (20,2), (20,3), (15,1), (15,2), (15,3)]:
    cfg = {
        "step": float(step), "max_open_per_side": 60, "close_alpha": 1.0, "close_gap": gap,
        "momentum_gate": False, "rearm_variant": "rearm_lvl2_exc1", "rearm_cooldown_bars": 0,
        "timeframe": "M15",
    }
    state = init_symbol_state("BTCUSD", cfg, bars)
    state = process_symbol("BTCUSD", cfg, bars, state)
    results.append({"step": step, "gap": gap, "per_hr": state.realized_net_usd / total_hrs, "net": state.realized_net_usd, "closes": state.realized_closes})

best = max(results, key=lambda r: r["per_hr"])
print(f"\n*** Best: step=${best['step']}, gap={best['gap']} → ${best['per_hr']:.2f}/hr, {best['closes']}c ***")

mt5.shutdown()
