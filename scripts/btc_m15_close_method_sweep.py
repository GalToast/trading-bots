#!/usr/bin/env python3
"""Close method sweep — test penetration alpha, early-green, cluster, and step-by-step.

Methods tested:
1. Penetration close (alpha=1.0, 0.8, 0.5) — outer closes when price reaches inner level
2. Step close (alpha=0.0) — each position closes at its own entry ± step
3. Early green close — close first position that shows green

Uses BTC M15 as the test symbol with $15 step (highest frequency in bar sweep).
"""
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5
import json

mt5.initialize()

bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 90)
if bars15 is None or len(bars15) == 0:
    print("NO M15 bars")
    mt5.shutdown()
    exit()

bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]

print(f"Loaded {len(bars)} M15 bars ({len(bars)*15/60:.0f} hours)")
print()

# Close method configurations
configs = [
    # Penetration close variants
    {"name": "Penetration alpha=1.0 (standard)", "close_alpha": 1.0, "close_gap": 1},
    {"name": "Penetration alpha=0.8 (early 20%)", "close_alpha": 0.8, "close_gap": 1},
    {"name": "Penetration alpha=0.5 (early 50%)", "close_alpha": 0.5, "close_gap": 1},
    {"name": "Penetration alpha=0.3 (very early)", "close_alpha": 0.3, "close_gap": 1},
    # Multi-close (close gap 2, 3)
    {"name": "Penetration gap=2 (wait 2 levels)", "close_alpha": 1.0, "close_gap": 2},
    {"name": "Penetration gap=3 (wait 3 levels)", "close_alpha": 1.0, "close_gap": 3},
    # Aggressive (no gap - close any when outer level hit)
    {"name": "No gap (close first on penetration)", "close_alpha": 1.0, "close_gap": 0},
]

total_hours = len(bars) * 15 / 60

print(f"{'Method':<40} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'$/hr':>8} | {'MaxOpen':>7} | {'Resets':>6}")
print("-" * 115)

results = []
for cfg_data in configs:
    cfg = {
        "step": 15.0,
        "max_open_per_side": 60,
        "close_alpha": cfg_data["close_alpha"],
        "close_gap": cfg_data["close_gap"],
        "momentum_gate": False,
        "rearm_variant": "rearm_lvl2_exc1",
        "rearm_cooldown_bars": 0,
        "timeframe": "M15",
    }
    state = init_symbol_state("BTCUSD", cfg, bars)
    state = process_symbol("BTCUSD", cfg, bars, state)

    resets = getattr(state, "anchor_resets", 0)
    per_hour = state.realized_net_usd / total_hours
    avg_close = state.realized_net_usd / state.realized_closes if state.realized_closes > 0 else 0

    results.append({
        "method": cfg_data["name"],
        "close_alpha": cfg_data["close_alpha"],
        "close_gap": cfg_data["close_gap"],
        "closes": state.realized_closes,
        "net": state.realized_net_usd,
        "avg_per_close": avg_close,
        "per_hour": per_hour,
        "max_open": state.max_open_total,
        "resets": resets,
    })
    print(f"{cfg_data['name']:<40} | {state.realized_closes:>6} | ${avg_close:>7.2f} | ${state.realized_net_usd:>9.2f} | ${per_hour:>7.2f} | {state.max_open_total:>7} | {resets:>6}")

print("=" * 115)

# Find best
if results:
    best_hr = max(results, key=lambda r: r["per_hour"])
    best_net = max(results, key=lambda r: r["net"])
    best_close = max(results, key=lambda r: r["avg_per_close"])
    print(f"\nBest $/hr: {best_hr['method']} → ${best_hr['per_hour']:.2f}/hr, {best_hr['closes']}c, ${best_hr['avg_per_close']:.2f}/close")
    print(f"Best net:  {best_net['method']} → ${best_net['net']:.2f} net, {best_net['closes']}c")
    print(f"Best $/c:  {best_close['method']} → ${best_close['avg_per_close']:.2f}/close")

    # Also test rearm impact
    print(f"\nRearm impact on penetration alpha=1.0:")
    for rearm_variant in ["rearm_lvl2_exc1", "none"]:
        cfg = {
            "step": 15.0,
            "max_open_per_side": 60,
            "close_alpha": 1.0,
            "close_gap": 1,
            "momentum_gate": False,
            "rearm_variant": rearm_variant,
            "rearm_cooldown_bars": 0 if rearm_variant != "none" else 999,
            "timeframe": "M15",
        }
        state = init_symbol_state("BTCUSD", cfg, bars)
        state = process_symbol("BTCUSD", cfg, bars, state)
        resets = getattr(state, "anchor_resets", 0)
        per_hour = state.realized_net_usd / total_hours
        avg_close = state.realized_net_usd / state.realized_closes if state.realized_closes > 0 else 0
        print(f"  rearm={rearm_variant}: {state.realized_closes}c, ${state.realized_net_usd:.2f} net, ${avg_close:.2f}/c, ${per_hour:.2f}/hr, {resets} resets")

# Save
out = {"results": results, "best_by_hr": best_hr["method"] if results else None}
from pathlib import Path
out_path = Path("reports/btc_m15_close_method_sweep.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(out, indent=2))
print(f"\nSaved to {out_path}")

mt5.shutdown()
