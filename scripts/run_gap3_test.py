#!/usr/bin/env python3
from sweep_black_market_v2 import simulate_black_v2, BlackV2, SYMBOLS
import MetaTrader5 as mt5
from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import load_bars

mt5.initialize()
cfg_map = default_raw_configs()

variants = [
    BlackV2(name='sell_gap3_buy_gap1_a50', momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, sell_gap=3, buy_gap=1),
    BlackV2(name='sell_gap3_buy_gap1_a100', momentum_gate=True, close_alpha_sell=1.00, close_alpha_buy=1.00, sell_gap=3, buy_gap=1),
    BlackV2(name='sell_gap3_buy_gap2_a50', momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, sell_gap=3, buy_gap=2),
    BlackV2(name='sell_gap3_buy_gap2_a100', momentum_gate=True, close_alpha_sell=1.00, close_alpha_buy=1.00, sell_gap=3, buy_gap=2),
    BlackV2(name='sell_gap3_buy_gap3_a50', momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, sell_gap=3, buy_gap=3),
    BlackV2(name='sell_gap3_buy_gap3_a100', momentum_gate=True, close_alpha_sell=1.00, close_alpha_buy=1.00, sell_gap=3, buy_gap=3),
]

print(f"{'Variant':<35} {'GBPUSD':>12} {'EURUSD':>12} {'NZDUSD':>12} {'Total':>12} {'x':>5}")
print("-" * 95)
baseline_total = 6189.98
for v in variants:
    total = 0
    parts = []
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        cfg = RawConfig(step_pips=cfg_map[sym].step_pips, max_open_per_side=cfg_map[sym].max_open_per_side, close_mode="two_level")
        r = simulate_black_v2(sym, bars, info, cfg, v)
        val = float(r["combined_net_usd"])
        parts.append(val)
        total += val
    mult = total / baseline_total
    print(f"{v.name:<35} {parts[0]:>12,.0f} {parts[1]:>12,.0f} {parts[2]:>12,.0f} {total:>12,.0f} {mult:>5.1f}x")

mt5.shutdown()
