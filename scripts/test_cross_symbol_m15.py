#!/usr/bin/env python3
"""Cross-symbol M15 validation: ETHUSD, SOLUSD, XRPUSD with BTCUSD-optimal config."""
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


def load_m15_bars(symbol, days):
    total = 96 * days
    all_rates = []
    offset = 1
    while offset < total:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, offset, min(100000, total - offset))
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        offset += 100000
    if not all_rates:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in all_rates]


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


CRYPTO_STEPS = {"ETHUSD": 5.0, "SOLUSD": 1.0, "XRPUSD": 0.01}


def main():
    mt5.initialize()

    print(f"\n{'='*110}")
    print(f"  CROSS-SYMBOL M15 VALIDATION — ETHUSD, SOLUSD, XRPUSD")
    print(f"  Config: MO=80, gap=1, α=1.00, mom=OFF (BTCUSD-optimal)")
    print(f"{'='*110}")

    grand_total = 0.0
    for sym in ["ETHUSD", "SOLUSD", "XRPUSD"]:
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"\n  ⚠️  {sym}: no symbol info")
            continue
        bars = load_m15_bars(sym, 90)
        if not bars:
            print(f"\n  ⚠️  {sym}: no bars")
            continue
        step = CRYPTO_STEPS[sym]
        print(f"\n  {sym}: {len(bars)} bars, step=${step}")

        # Test mom=OFF and mom=ON
        for mom in [False, True]:
            t0 = time.time()
            r = run_engine(sym, bars, info, step, 80, 1, 1.0, "rearm_lvl2_exc1", mom)
            elapsed = time.time() - t0
            mom_str = "mom=OFF" if not mom else "mom=ON"
            print(f"    {mom_str}: ${r['combined']:>12,.2f}  (real=${r['realized']:>10,.2f}, flt=${r['floating']:>9,.2f})  {r['closes']}c [{elapsed:.1f}s]")
            if not mom:
                grand_total += r["combined"]

    print(f"\n{'='*110}")
    print(f"  CROSS-SYMBOL M15 TOTAL (mom=OFF): ${grand_total:,.2f}")
    print(f"  BTCUSD M15 (my engine): $1,091,338")
    print(f"  COMBINED: ${grand_total + 1091338:,.2f}")
    print(f"{'='*110}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
