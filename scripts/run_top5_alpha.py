#!/usr/bin/env python3
import MetaTrader5 as mt5
from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import load_bars
from sweep_stateful_rearm_v2 import simulate_stateful_rearm_v2, Variant

mt5.initialize()
cfg_map = default_raw_configs()
symbols = ["GBPUSD", "EURUSD", "NZDUSD"]

variants = [
    Variant(name="momentum_gate", momentum_gate=True),
    Variant(name="momentum_alpha50", momentum_gate=True, close_alpha=0.50),
    Variant(name="cool12", cooldown_bars=12),
    Variant(name="cool12_alpha50", cooldown_bars=12, close_alpha=0.50),
    Variant(name="momentum_cool6_alpha50", momentum_gate=True, cooldown_bars=6, close_alpha=0.50),
]

print(f"{'Variant':<30} {'GBPUSD':>12} {'EURUSD':>12} {'NZDUSD':>12} {'Total':>12}")
print("-" * 85)
for v in variants:
    total = 0
    parts = []
    for sym in symbols:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        cfg = RawConfig(
            step_pips=cfg_map[sym].step_pips,
            max_open_per_side=cfg_map[sym].max_open_per_side,
            close_mode="two_level",
        )
        r = simulate_stateful_rearm_v2(sym, bars, info, cfg, v)
        val = float(r.get("combined_net_usd", 0))
        parts.append(val)
        total += val
    print(f"{v.name:<30} {parts[0]:>12,.2f} {parts[1]:>12,.2f} {parts[2]:>12,.2f} {total:>12,.2f}")

mt5.shutdown()
