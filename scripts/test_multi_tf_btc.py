#!/usr/bin/env python3
"""Multi-timeframe stacking: M1 + M5 + H1 on BTCUSD simultaneously.
Each TF captures different volatility cycles. Non-overlapping edges = multiplicative gains."""
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


TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}


def load_bars_tf(symbol, tf_name, days):
    tf = TIMEFRAME_MAP[tf_name]
    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "H1": 24}[tf_name]
    max_bars = 100000
    total = bars_per_day * days
    all_rates = []
    offset = 1  # Skip currently-forming bar
    while offset < total:
        count = min(max_bars, total - offset)
        rates = mt5.copy_rates_from_pos(symbol, tf, offset, count)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        offset += count
    if not all_rates:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in all_rates]


def run_engine(sym, bars, info, step, mop, gap, alpha, variant_name, mom, step_is_price):
    variant = REARM_VARIANTS.get(variant_name)
    if variant is None:
        return {}
    close_mode = "one_level" if gap == 1 else "two_level"
    cfg = RawConfig(step_pips=step, max_open_per_side=mop, close_mode=close_mode, step_is_price_units=step_is_price)
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
    spread_px = spread_price(info)

    print(f"\n{'='*110}")
    print(f"  MULTI-TIMEFRAME STACKING TEST — BTCUSD M1, M5, H1")
    print(f"  Testing whether combining TFs captures non-overlapping edges")
    print(f"{'='*110}")

    # Load all timeframes
    results = {}
    for tf in ["H1", "M5", "M1"]:
        print(f"\n  Loading {sym} {tf} bars for 90d...")
        t0 = time.time()
        bars = load_bars_tf(sym, tf, 90)
        print(f"  Loaded {len(bars)} bars [{time.time()-t0:.1f}s]")

        if not bars:
            continue

        # H1 uses $50 steps (price units), M1/M5 use pip-like steps
        if tf == "H1":
            step = 50.0
            mop = 60
            step_is_price = True
        else:
            # M1 and M5: use $5 equivalent in pips
            # BTC pip is typically $0.01, so $5 = 500 pips
            step = 500.0
            mop = 60
            step_is_price = False

        t0 = time.time()
        r = run_engine(sym, bars, info, step, mop, 1, 1.0, "rearm_lvl2_exc1", True, step_is_price)
        elapsed = time.time() - t0
        results[tf] = r

        print(f"  {tf:4s}: ${r['combined']:>12,.2f}  (real=${r['realized']:>10,.2f}, flt=${r['floating']:>8,.2f})")
        print(f"      {r['closes']} closes, {r['rearm_opens']} rearm opens, max_seen={r['max_open_seen']} [{elapsed:.1f}s]")

    # Combined total
    total_combined = sum(r["combined"] for r in results.values())
    total_closes = sum(r["closes"] for r in results.values())
    print(f"\n{'='*110}")
    print(f"  MULTI-TIMEFRAME TOTAL: ${total_combined:,.2f} ({total_closes} closes across {len(results)} TFs)")
    print(f"{'='*110}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
