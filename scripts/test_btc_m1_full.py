#!/usr/bin/env python3
"""BTCUSD M1 test on available data (~19 days)."""
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


def load_all_m1_bars(symbol):
    """Load ALL available M1 bars for BTCUSD (~19 days)."""
    all_rates = []
    # Load in reverse: start from most recent and go backward
    for offset in [0, 10000, 20000, 30000, 40000, 50000, 60000]:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, offset, 10000)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        if len(rates) < 10000:
            break
    if not all_rates:
        return []
    # Sort by time (oldest first)
    all_rates.sort(key=lambda r: r[0])
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


def main():
    mt5.initialize()

    sym = "BTCUSD"
    info = mt5.symbol_info(sym)
    print(f"\nLoading {sym} ALL available M1 bars...")
    bars = load_all_m1_bars(sym)
    days = len(bars) / 1440
    print(f"Loaded {len(bars)} bars ({days:.1f} days)")

    if not bars:
        print("No bars")
        mt5.shutdown()
        return

    print(f"\n{'='*110}")
    print(f"  BTCUSD M1 — FULL SWEEP ({days:.1f} days of data)")
    print(f"  Testing: step=$10 to $200, max_open=30 to 60")
    print(f"  Config: alpha=1.0, gap=1, rearm_lvl2_exc1, momentum=True")
    print(f"{'='*110}")

    results = []
    for step in [10, 20, 30, 50, 75, 100, 150, 200]:
        for mop in [30, 60]:
            t0 = time.time()
            r = run_engine(sym, bars, info, float(step), mop, 1, 1.0, "rearm_lvl2_exc1", True)
            elapsed = time.time() - t0
            results.append({"step": step, "mop": mop, **r})
            print(f"  step=${step:>4d} mop={mop:>3d}: ${r['combined']:>12,.2f}  (real=${r['realized']:>10,.2f}, flt=${r['floating']:>9,.2f})  {r['closes']}c [{elapsed:.1f}s]")

    best = max(results, key=lambda x: x["combined"])
    print(f"\n{'='*110}")
    print(f"  OPTIMAL M1: step=${best['step']}, max_open={best['mop']} → ${best['combined']:,.2f}")
    print(f"  Annualized (extrapolated to 90d): ${best['combined'] / days * 90:,.2f}")
    print(f"{'='*110}")

    # Also run M5 on the SAME period for comparison
    print(f"\n{'='*110}")
    print(f"  M5 on same {days:.1f}-day period for comparison")
    print(f"{'='*110}")

    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 1, int(days * 288))
    if rates is not None and len(rates) > 0:
        m5_bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                    "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]
        print(f"  Loaded {len(m5_bars)} M5 bars")

        for step in [100, 200, 500]:
            t0 = time.time()
            r = run_engine(sym, m5_bars, info, float(step), 60, 1, 1.0, "rearm_lvl2_exc1", True)
            elapsed = time.time() - t0
            print(f"  M5 step=${step:>4d}: ${r['combined']:>12,.2f}  {r['closes']}c [{elapsed:.1f}s]")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
