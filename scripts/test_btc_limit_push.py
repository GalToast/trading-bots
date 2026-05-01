#!/usr/bin/env python3
"""Push BTCUSD H1 to the limit: max_open=60, 80, 100."""
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


def run_engine(sym, bars, info, step, mop, gap, alpha, variant_name, mom):
    variant = REARM_VARIANTS.get(variant_name)
    if variant is None:
        return {}
    close_mode = "one_level" if gap == 1 else "two_level"
    cfg = RawConfig(step_pips=step, max_open_per_side=mop, close_mode=close_mode, step_is_price_units=True)
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
    print(f"  BTCUSD H1 — PUSHING THE LIMIT: max_open from 30 to 100")
    print(f"  Config: alpha=1.0, gap=1, rearm_lvl2_exc1, momentum=True, step=$50")
    print(f"{'='*110}")

    # First verify our baseline matches the known result
    t0 = time.time()
    r30 = run_engine(sym, bars, info, 50.0, 30, 1, 1.0, "rearm_lvl2_exc1", True)
    print(f"\n  max_open=30: ${r30['combined']:>12,.2f}  (real=${r30['realized']:>10,.2f}, flt=${r30['floating']:>8,.2f})")
    print(f"    {r30['closes']} closes, {r30['rearm_opens']} rearm opens, max_seen={r30['max_open_seen']} [{time.time()-t0:.1f}s]")

    # Now push the limits
    for mop in [40, 50, 60, 80, 100]:
        t0 = time.time()
        r = run_engine(sym, bars, info, 50.0, mop, 1, 1.0, "rearm_lvl2_exc1", True)
        delta = r["combined"] - r30["combined"]
        pct = (r["combined"] / r30["combined"] - 1) * 100
        print(f"\n  max_open={mop:>3d}: ${r['combined']:>12,.2f}  (+${delta:>10,.2f}, {pct:+.1f}%)")
        print(f"    {r['closes']} closes, {r['rearm_opens']} rearm opens, max_seen={r['max_open_seen']} [{time.time()-t0:.1f}s]")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
