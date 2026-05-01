#!/usr/bin/env python3
"""
FUNDAMENTAL $/HOUR MULTIPLIER ANALYSIS — REARM AGGRESSION STUDY

CRITICAL FINDING from warp state analysis:
  - 288 closes, $1,457 net, $5.06/close
  - 462 anchor resets (457 flat + 5 risk)
  - **ONLY 5 REARM OPENS** in 462 cycles!
  - max_open_total: 24 (out of 40 allowed)

The rearm mechanism is barely firing. The lattice operates in waves:
  1. Build stack of ~24 positions during trend
  2. Wait for reversal (IDLE TIME — no trading)
  3. Cascade close ALL positions
  4. Anchor resets flat
  5. Build NEW stack from scratch

The bottleneck is step 2: waiting for reversal with ZERO positions.
If rearm tokens could IMMEDIATELY rebuild the stack after each close,
we'd get MULTIPLE cascade cycles per reversal = 2x+ $/hour.

This study tests: what happens if rearm fires aggressively?
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state

mt5.initialize()
bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 30)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]
total_hrs = len(bars) * 15 / 60

print(f"Loaded {len(bars)} M15 bars ({total_hrs:.0f} hours)")
print()
print("Testing rearm aggression: how do rearm_excursion_levels and cooldown affect $/hr?")
print()

configs = [
    # Base: warp config
    {"label": "WARP-BASE: $15 mo=40 a=0.6 gap=0", "step": 15, "mo": 40, "alpha": 0.6, "gap": 0, "mom": False, "excursion": 1, "cooldown": 0},
    # No excursion = immediate rearm
    {"label": "NO-EXCURSION: $15 mo=40 a=0.6 gap=0 exc=0", "step": 15, "mo": 40, "alpha": 0.6, "gap": 0, "mom": False, "excursion": 0, "cooldown": 0},
    # Even tighter: no excursion + wider stack
    {"label": "NO-EXC-MO60: $15 mo=60 a=0.6 gap=0 exc=0", "step": 15, "mo": 60, "alpha": 0.6, "gap": 0, "mom": False, "excursion": 0, "cooldown": 0},
    # Alpha=1.0 with no excursion
    {"label": "NO-EXC-A1: $15 mo=40 a=1.0 gap=0 exc=0", "step": 15, "mo": 40, "alpha": 1.0, "gap": 0, "mom": False, "excursion": 0, "cooldown": 0},
    # Gap=1 baseline
    {"label": "GAP-1-BASE: $15 mo=40 a=1.0 gap=1", "step": 15, "mo": 40, "alpha": 1.0, "gap": 1, "mom": False, "excursion": 1, "cooldown": 0},
    # Step $25 for comparison
    {"label": "WIDE-STEP: $25 mo=40 a=1.0 gap=1", "step": 25, "mo": 40, "alpha": 1.0, "gap": 1, "mom": False, "excursion": 1, "cooldown": 0},
]

results = []
for cfg in configs:
    c = {"step": float(cfg["step"]), "max_open_per_side": cfg["mo"],
         "close_alpha": cfg["alpha"], "close_gap": cfg["gap"],
         "momentum_gate": cfg["mom"], "rearm_variant": "rearm_lvl2_exc1",
         "rearm_cooldown_bars": cfg["cooldown"], "timeframe": "M15",
         "rearm_excursion_levels": cfg["excursion"]}
    state = init_symbol_state("BTCUSD", c, bars)
    state = process_symbol("BTCUSD", c, bars, state)
    closes = state.realized_closes
    net = state.realized_net_usd
    avg = net / closes if closes > 0 else 0
    per_hr = net / total_hrs
    results.append((cfg["label"], {
        "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
        "resets": getattr(state, 'anchor_resets', 0),
        "rearm_opens": state.rearm_opens,
        "max_open": state.max_open_total
    }))
    print(f"  {cfg['label']}")
    print(f"    {closes}c, ${net:.2f} net, ${avg:.2f}/close, ${per_hr:.2f}/hr")
    print(f"    resets={getattr(state, 'anchor_resets', 0)}, rearm={state.rearm_opens}, max_open={state.max_open_total}")
    print()

print("=" * 100)
print(f"{'Config':<50} {'$/hr':>8} {'Closes':>7} {'$/close':>8} {'Rearm':>6} {'MaxOpen':>8}")
print("-" * 100)
for label, r in results:
    print(f"{label:<50} ${r['per_hr']:>7.2f} {r['closes']:>7} ${r['avg']:>7.2f} {r['rearm_opens']:>6} {r['max_open']:>8}")
print("=" * 100)

# Warp baseline
warp_live_per_hr = 1457.44 / 4.0  # ~$364/hr (4 hours of live trading)
warp_live_per_close = 5.06
print(f"\nLive warp reference: ~${warp_live_per_hr:.2f}/hr, ${warp_live_per_close:.2f}/close")
print(f"(Note: shadow engine uses bar-level close price approximation; live uses trigger-level closes)")
print()
print("KEY QUESTION: Does reducing rearm_excursion_levels increase $/hr?")
print("If rearm fires immediately after each close (excursion=0),")
print("the lattice rebuilds faster and captures more closes per reversal.")

mt5.shutdown()
