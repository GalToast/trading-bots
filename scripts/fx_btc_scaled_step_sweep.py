#!/usr/bin/env python3
"""FX bar-level sweep using BTC-scaled equivalent steps.
Tests whether BTC-tight-step geometry ($15/$20 → FX pip equivalents) works on FX.
"""
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5

mt5.initialize()

btc_info = mt5.symbol_info("BTCUSD")
btc_price = btc_info.bid if btc_info else 75000.0

# BTC steps to scale
btc_steps = [15.0, 20.0]
btc_pct = [s / btc_price for s in btc_steps]
print(f"BTC @ ${btc_price:.2f}: $15={btc_pct[0]*100:.4f}%, $20={btc_pct[1]*100:.4f}%")
print()

# FX symbols
fx_symbols = ["EURUSD", "GBPUSD", "USDJPY"]
total_bars = 24 * 90 * 4  # 90 days of M15

print(f"{'Symbol':<10} {'Step':>6} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'$/hr':>8} | {'MaxOpen':>7}")
print("-" * 95)

results = []
for sym in fx_symbols:
    info = mt5.symbol_info(sym)
    if info is None:
        print(f"{sym}: NOT AVAILABLE")
        continue
    price = info.bid
    digits = int(info.digits or 5)
    point = float(info.point or 0.00001)
    pip = point * (10.0 if digits in (3, 5) else 1.0)

    for btc_step, pct in zip(btc_steps, btc_pct):
        fx_step = price * pct
        fx_step_pips = fx_step / pip

        cfg = {
            'step': round(fx_step, digits),
            'max_open_per_side': 60,
            'close_alpha': 1.0,
            'close_gap': 1,
            'momentum_gate': False,
            'rearm_variant': 'rearm_lvl2_exc1',
            'rearm_cooldown_bars': 0,
            'timeframe': 'M15',
        }

        bars15 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, total_bars)
        if bars15 is None or len(bars15) == 0:
            print(f"{sym}: NO BARS")
            continue

        bars = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]),
                 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars15]

        state = init_symbol_state(sym, cfg, bars)
        state = process_symbol(sym, cfg, bars, state)

        total_hours = len(bars) * 15 / 60
        per_hour = state.realized_net_usd / total_hours if total_hours > 0 else 0
        avg_close = state.realized_net_usd / state.realized_closes if state.realized_closes > 0 else 0

        results.append({
            'symbol': sym,
            'btc_step': btc_step,
            'fx_step': fx_step,
            'fx_step_pips': fx_step_pips,
            'closes': state.realized_closes,
            'net': state.realized_net_usd,
            'avg_per_close': avg_close,
            'per_hour': per_hour,
            'max_open': state.max_open_total,
        })

        print(f"{sym:<10} ${btc_step:>5.0f}({fx_step_pips:>4.1f}p) | {state.realized_closes:>6} | ${avg_close:>7.2f} | ${state.realized_net_usd:>9.2f} | ${per_hour:>7.2f} | {state.max_open_total:>7}")

print("=" * 95)

# Find best combo
if results:
    best = max(results, key=lambda r: r['net'])
    print(f"\n*** Best: {best['symbol']} ${best['btc_step']:.0f} → ${best['fx_step']:.6f} ({best['fx_step_pips']:.1f}pips): ${best['per_hour']:.2f}/hr ***")

    # Per-symbol best
    for sym in fx_symbols:
        sym_results = [r for r in results if r['symbol'] == sym]
        if sym_results:
            best_sym = max(sym_results, key=lambda r: r['per_hour'])
            print(f"  {sym} best: ${best_sym['btc_step']:.0f} → {best_sym['fx_step_pips']:.1f}pips, ${best_sym['per_hour']:.2f}/hr, {best_sym['closes']}c")

# Save
import json
out = {
    'btc_price': btc_price,
    'results': results,
}
from pathlib import Path
out_path = Path("reports/fx_btc_scaled_step_sweep.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(out, indent=2))
print(f"\nSaved to {out_path}")

mt5.shutdown()
