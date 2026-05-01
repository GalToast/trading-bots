#!/usr/bin/env python3
"""Reproduce codex-btc's no-stops matrix exactly.
Uses StatefulRearmRawEngine with level_idx >= 2 filter."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_penetration_lattice_shadow import (
    StatefulRearmRawEngine,
    REARM_VARIANTS,
)
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
DAYS = 90


def load_closed_h1_bars(symbol, days):
    """Load H1 bars, skipping the currently-forming bar (offset=1)."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]


def run_engine(sym, bars, info, step, mop, gap, alpha, variant_name, mom):
    """Run StatefulRearmRawEngine exactly like no-stops matrix."""
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
        "max_open": int(engine.state.max_open_total),
    }


STEPS = {"BTCUSD": 50.0, "ETHUSD": 10.0, "SOLUSD": 0.50, "XRPUSD": 0.01}


def main():
    mt5.initialize()

    print(f"\n{'='*110}")
    print(f"  EXACT REPRODUCTION: No-Stops Matrix Runner (StatefulRearmRawEngine)")
    print(f"  Config: alpha=1.0, gap=1, rearm_lvl2_exc1, momentum=True")
    print(f"{'='*110}")

    for mop in [30, 40]:
        total = 0.0
        details = []
        for sym in CRYPTO:
            info = mt5.symbol_info(sym)
            bars = load_closed_h1_bars(sym, DAYS)
            if not bars:
                print(f"  ⚠️  {sym}: no bars")
                continue
            step = STEPS[sym]
            t0 = time.time()
            r = run_engine(sym, bars, info, step, mop, 1, 1.0, "rearm_lvl2_exc1", True)
            elapsed = time.time() - t0
            total += r["combined"]
            details.append(f"{sym}: ${r['combined']:>12,.2f}  (real=${r['realized']:>10,.2f}, flt=${r['floating']:>8,.2f}, {r['closes']}c, {r['rearm_opens']}rearms) [{elapsed:.1f}s]")

        print(f"\n  max_open={mop}: TOTAL = ${total:>12,.2f}")
        for d in details:
            print(f"    {d}")

    # Also test the exact winning config from codex-btc: max_open=40
    print(f"\n{'='*110}")
    print(f"  WINNER CONFIG: max_open=40, all 4 symbols")
    print(f"{'='*110}")
    total = 0.0
    for sym in CRYPTO:
        info = mt5.symbol_info(sym)
        bars = load_closed_h1_bars(sym, DAYS)
        if not bars: continue
        step = STEPS[sym]
        r = run_engine(sym, bars, info, step, 40, 1, 1.0, "rearm_lvl2_exc1", True)
        total += r["combined"]
        print(f"  {sym:12s} ${r['combined']:>12,.2f}  ({r['closes']}c, {r['rearm_opens']}rearms)")
    print(f"\n  TOTAL: ${total:,.2f}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
