#!/usr/bin/env python3
"""Diagnostic: test step_is_price_units True vs False on M5 and M15."""
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


TIMEFRAME_MAP = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15}


def load_bars(symbol, tf_name, days):
    tf = TIMEFRAME_MAP[tf_name]
    bpd = {"M5": 288, "M15": 96}[tf_name]
    total = bpd * days
    all_rates = []
    offset = 1
    while offset < total:
        rates = mt5.copy_rates_from_pos(symbol, tf, offset, min(100000, total - offset))
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        offset += 100000
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in all_rates]


def run(sym, bars, info, step, mop, gap, alpha, mom, sipu):
    variant = REARM_VARIANTS.get("rearm_lvl2_exc1")
    close_mode = "one_level" if gap == 1 else "two_level"
    cfg = RawConfig(step_pips=step, max_open_per_side=mop, close_mode=close_mode, step_is_price_units=sipu)
    engine = StatefulRearmRawEngine(sym, cfg, info, variant=variant, close_alpha=alpha, cooldown_bars=0, momentum_gate=mom, sell_gap=gap, buy_gap=gap)
    engine.replay(bars)
    final = float(bars[-1]["close"])
    sp = spread_price(info)
    tix = [type("T", (), t)() for t in engine.state.open_tickets]
    flt = sum(unit_pnl_usd(sym, t.direction, t.entry_price, final, sp) for t in tix)
    return {"combined": float(engine.state.realized_net_usd) + flt, "realized": float(engine.state.realized_net_usd),
            "floating": flt, "closes": int(engine.state.realized_closes), "rearms": int(engine.state.rearm_opens), "max_seen": int(engine.state.max_open_total)}


def main():
    mt5.initialize()
    sym = "BTCUSD"
    info = mt5.symbol_info(sym)
    m5 = load_bars(sym, "M5", 90)
    m15 = load_bars(sym, "M15", 90)
    print(f"\nM5 bars: {len(m5)}, M15 bars: {len(m15)}")

    print(f"\n{'='*110}")
    print(f"  DIAGNOSTIC: step_is_price_units True vs False")
    print(f"{'='*110}")

    # M5 configs (we know these should match ~$663K)
    print(f"\n  --- M5 $100, MO=60, gap=1, α=1.00, mom=OFF ---")
    for sipu in [True, False]:
        r = run(sym, m5, info, 100.0, 60, 1, 1.0, False, sipu)
        print(f"    sipu={sipu}: ${r['combined']:,.2f}  (real=${r['realized']:,.2f}, flt=${r['floating']:,.2f})  {r['closes']}c, max={r['max_seen']}")

    # M15 configs
    print(f"\n  --- M15 $15, MO=80, gap=1, α=1.00, mom=OFF ---")
    for sipu in [True, False]:
        r = run(sym, m15, info, 15.0, 80, 1, 1.0, False, sipu)
        print(f"    sipu={sipu}: ${r['combined']:,.2f}  (real=${r['realized']:,.2f}, flt=${r['floating']:,.2f})  {r['closes']}c, max={r['max_seen']}")

    print(f"\n  --- M15 $20, MO=60, gap=1, α=1.00, mom=ON ---")
    for sipu in [True, False]:
        r = run(sym, m15, info, 20.0, 60, 1, 1.0, True, sipu)
        print(f"    sipu={sipu}: ${r['combined']:,.2f}  (real=${r['realized']:,.2f}, flt=${r['floating']:,.2f})  {r['closes']}c, max={r['max_seen']}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
