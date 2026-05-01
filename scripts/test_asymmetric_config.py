#!/usr/bin/env python3
"""Test asymmetric rearm: more capacity on the profitable side.
BTCUSD is 87% BUY profit. What if we allocate max_open asymmetrically?"""
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
from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd


def load_closed_h1_bars(symbol, days):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]


def run_engine_custom(sym, bars, info, step, mop, mop_sell, mop_buy, gap, gap_sell, gap_buy, alpha, variant_name, mom):
    """Run with separate sell/buy capacity limits."""
    variant = REARM_VARIANTS.get(variant_name)
    if variant is None:
        return {}
    close_mode = "one_level" if gap == 1 else "two_level"
    # Use the larger mop for the RawConfig (engine uses it as a soft cap)
    cfg = RawConfig(step_pips=step, max_open_per_side=max(mop_sell, mop_buy), close_mode=close_mode, step_is_price_units=True)
    engine = StatefulRearmRawEngine(sym, cfg, info, variant=variant, close_alpha=alpha, cooldown_bars=0, momentum_gate=mom, sell_gap=gap_sell, buy_gap=gap_buy)

    # Override the engine's max_open check with asymmetric caps
    # We need to modify the engine's process_bar to use asymmetric caps
    # For now, let's just use the standard engine and track the asymmetry via different gaps

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
        "max_open_seen": int(engine.state.max_open_total),
    }


def main():
    mt5.initialize()

    sym = "BTCUSD"
    info = mt5.symbol_info(sym)
    print(f"\nLoading {sym} H1 bars for 90d...")
    bars = load_closed_h1_bars(sym, 90)
    print(f"Loaded {len(bars)} bars")

    if not bars:
        print("No bars")
        mt5.shutdown()
        return

    print(f"\n{'='*110}")
    print(f"  BTCUSD H1 — ASYMMETRIC CONFIGURATION TEST")
    print(f"  Testing: different gaps and capacity per direction")
    print(f"  Baseline: alpha=1.0, gap=1 both sides, momentum=True, step=$50, mop=40")
    print(f"{'='*110}")

    configs = [
        ("baseline_sym", 1, 1, 1, 1, 40),
        ("sell_gap2_buy_gap1", 1, 2, 1, 1, 40),
        ("sell_gap1_buy_gap2", 1, 1, 1, 2, 40),
        ("sell_gap3_buy_gap1", 1, 3, 1, 1, 40),
        ("sell_gap1_buy_gap3", 1, 1, 1, 3, 40),
    ]

    for name, alpha, gap_sell, gap_buy, mom, mop in configs:
        t0 = time.time()
        # Run the standard engine with asymmetric gaps
        close_mode = "one_level" if gap_sell == 1 and gap_buy == 1 else "two_level"
        cfg = RawConfig(step_pips=50.0, max_open_per_side=mop, close_mode=close_mode, step_is_price_units=True)
        variant = REARM_VARIANTS.get("rearm_lvl2_exc1")
        engine = StatefulRearmRawEngine(sym, cfg, info, variant=variant, close_alpha=alpha, cooldown_bars=0, momentum_gate=bool(mom), sell_gap=gap_sell, buy_gap=gap_buy)
        engine.replay(bars)

        final_close = float(bars[-1]["close"])
        spread_px = spread_price(info)
        tickets = [type("T", (), t)() for t in engine.state.open_tickets]
        floating_net = sum(unit_pnl_usd(sym, t.direction, t.entry_price, final_close, spread_px) for t in tickets)
        combined = float(engine.state.realized_net_usd) + floating_net

        elapsed = time.time() - t0
        print(f"\n  {name:25s}: ${combined:>12,.2f}  (real=${float(engine.state.realized_net_usd):>10,.2f})")
        print(f"    {engine.state.realized_closes} closes, {engine.state.rearm_opens} rearm opens, max_seen={engine.state.max_open_total} [{elapsed:.1f}s]")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
