#!/usr/bin/env python3
"""Per-symbol asymmetric gap sweep — find the optimal gap combo per symbol."""
from sweep_black_market_v2 import simulate_black_v2, BlackV2, SYMBOLS
import MetaTrader5 as mt5
from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import load_bars
import itertools

mt5.initialize()
cfg_map = default_raw_configs()

sell_gaps = [1, 2, 3, 4]
buy_gaps = [1, 2, 3]
alphas = [0.50, 0.75, 1.00]

print(f"\n{'='*110}")
print(f"  PER-SYMBOL ASYMMETRIC GAP SWEEP — Testing all sell_gap × buy_gap × alpha combos")
print(f"{'='*110}")

results = []
for sym in SYMBOLS:
    info = mt5.symbol_info(sym)
    bars = load_bars(sym, 60)
    cfg = RawConfig(
        step_pips=cfg_map[sym].step_pips,
        max_open_per_side=cfg_map[sym].max_open_per_side,
        close_mode="two_level",
    )
    print(f"\n--- {sym} ---")
    print(f"{'sell_g':>6} {'buy_g':>6} {'alpha':>5} {'Total':>12} {'x Baseline':>10}  Config")
    print("-" * 75)

    baseline = float(mt5.symbol_info(sym).point)  # placeholder
    from penetration_lattice_hybrid_apex import simulate_raw_close2
    bl = simulate_raw_close2(sym, bars, info, cfg)
    baseline = float(bl["combined_net_usd"])

    best = None
    best_val = 0
    for sg, bg, alpha in itertools.product(sell_gaps, buy_gaps, alphas):
        v = BlackV2(name=f"test", momentum_gate=True, close_alpha_sell=alpha, close_alpha_buy=alpha, sell_gap=sg, buy_gap=bg)
        r = simulate_black_v2(sym, bars, info, cfg, v)
        val = float(r["combined_net_usd"])
        mult = val / baseline if baseline > 0 else 0
        if val > best_val:
            best_val = val
            best = (sg, bg, alpha)
        print(f"  {sg:>6} {bg:>6} {alpha:>5.2f} ${val:>11,.0f} {mult:>10.1f}x  sg={sg} bg={bg} a={alpha}")
    print(f"  🏆 BEST: sell_gap={best[0]}, buy_gap={best[1]}, alpha={best[2]} → ${best_val:,.0f} ({best_val/baseline:.1f}x)")
    results.append((sym, best, best_val, baseline))

print(f"\n{'='*110}")
print(f"  OPTIMAL PER-SYMBOL CONFIGS")
print(f"{'='*110}")
total_best = 0
total_baseline = 0
for sym, best, val, bl in results:
    total_best += val
    total_baseline += bl
    print(f"  {sym}: sell_gap={best[0]}, buy_gap={best[1]}, alpha={best[2]} → ${val:,.0f} ({val/bl:.1f}x)")

mult = total_best / total_baseline if total_baseline > 0 else 0
print(f"\n  COMBINED: ${total_best:,.0f} ({mult:.1f}x baseline of ${total_baseline:,.0f})")

mt5.shutdown()
