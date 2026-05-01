#!/usr/bin/env python3
"""Quick FX max_open test — just GBPUSD M1 to see the scaling pattern."""
from __future__ import annotations

import time
from pathlib import Path

import MetaTrader5 as mt5

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_penetration_lattice_shadow import (
    StatefulRearmRawEngine,
    REARM_VARIANTS,
)
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import pip_size_for, spread_price, unit_pnl_usd


def load_m1_bars_fast(symbol, days):
    """Load only the bars we need — chunked for MT5 limits."""
    max_bars = 100000
    total = 1440 * days
    all_rates = []
    offset = 0
    while offset < total:
        count = min(max_bars, total - offset)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, offset, count)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        offset += count
    if not all_rates:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in all_rates]


def run_engine(sym, bars, info, step_pips, mop, gap, alpha, variant_name, mom):
    variant = REARM_VARIANTS.get(variant_name)
    if variant is None:
        return {}
    close_mode = "one_level" if gap == 1 else "two_level"
    cfg = RawConfig(step_pips=step_pips, max_open_per_side=mop, close_mode=close_mode, step_is_price_units=False)
    engine = StatefulRearmRawEngine(sym, cfg, info, variant=variant, close_alpha=alpha, cooldown_bars=0, momentum_gate=mom, sell_gap=gap, buy_gap=gap)
    engine.replay(bars)

    final_close = float(bars[-1]["close"])
    spread_px = spread_price(info)
    tickets = [type("T", (), t)() for t in engine.state.open_tickets]
    floating_net = sum(unit_pnl_usd(sym, t.direction, t.entry_price, final_close, spread_px) for t in tickets)

    combined = float(engine.state.realized_net_usd) + floating_net
    return {
        "combined": combined,
        "realized": float(engine.state.realized_net_usd),
        "floating": floating_net,
        "closes": int(engine.state.realized_closes),
        "rearm_opens": int(engine.state.rearm_opens),
        "max_open": int(engine.state.max_open_total),
    }


def main():
    mt5.initialize()

    sym = "GBPUSD"
    info = mt5.symbol_info(sym)
    print(f"\nLoading {sym} M1 bars for 60d...")
    bars = load_m1_bars_fast(sym, 60)
    print(f"Loaded {len(bars)} bars")

    if not bars:
        print("No bars loaded")
        mt5.shutdown()
        return

    print(f"\n{'='*80}")
    print(f"  GBPUSD M1 max_open sweep — alpha=0.50, gap=3, momentum=True")
    print(f"{'='*80}")

    for mop in [20, 30, 40]:
        t0 = time.time()
        r = run_engine(sym, bars, info, 1.0, mop, 3, 0.5, "rearm_lvl2_exc1", True)
        elapsed = time.time() - t0
        print(f"\n  max_open={mop}: ${r['combined']:>10,.2f}  (real=${r['realized']:>8,.2f}, flt=${r['floating']:>8,.2f})")
        print(f"    {r['closes']} closes, {r['rearm_opens']} rearm opens, max_open_seen={r['max_open']} [{elapsed:.1f}s]")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
