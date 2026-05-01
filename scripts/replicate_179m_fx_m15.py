#!/usr/bin/env python3
"""Replicate qwen-main's M15 $15 MO=80 mom=OFF = $1.79M finding + FX M15 sweep."""
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
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}


def load_closed_bars(symbol, tf_name, days):
    tf = TIMEFRAME_MAP[tf_name]
    bars_per_day = {"M5": 288, "M15": 96, "H1": 24}[tf_name]
    total = bars_per_day * days
    all_rates = []
    offset = 1
    while offset < total:
        count = min(100000, total - offset)
        rates = mt5.copy_rates_from_pos(symbol, tf, offset, count)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        offset += 100000
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

    # ============ PART 1: Replicate qwen-main's M15 $1.79M ============
    print(f"\n{'='*110}")
    print(f"  PART 1: Replicating qwen-main's BTCUSD M15 $15 MO=80 mom=OFF = $1.79M")
    print(f"{'='*110}")

    sym = "BTCUSD"
    info = mt5.symbol_info(sym)
    bars = load_closed_bars(sym, "M15", 90)
    print(f"  Loaded {len(bars)} M15 bars for {sym}")

    if bars:
        t0 = time.time()
        r = run_engine(sym, bars, info, 15.0, 80, 1, 1.0, "rearm_lvl2_exc1", False, True)
        elapsed = time.time() - t0
        print(f"\n  MY RESULT: ${r['combined']:,.2f}  (real=${r['realized']:,.2f}, flt=${r['floating']:,.2f})")
        print(f"    {r['closes']} closes, {r['rearm_opens']} rearm opens, max_seen={r['max_open_seen']} [{elapsed:.1f}s]")
        print(f"  QWEN-MAIN: $1,789,450  (their result)")
        diff = abs(r["combined"] - 1789450)
        pct = diff / 1789450 * 100
        if pct < 1:
            print(f"  ✅ MATCH: {pct:.2f}% difference — engines aligned!")
        else:
            print(f"  ⚠️  DISCREPANCY: {pct:.2f}% difference — needs investigation")

    # ============ PART 2: FX M15 Sweep ============
    print(f"\n{'='*110}")
    print(f"  PART 2: FX M15 Sweep — GBPUSD, EURUSD, NZDUSD")
    print(f"  Testing: steps $0.0001-$0.002, MO=60/80, mom=ON/OFF")
    print(f"{'='*110}")

    fx_configs = [
        ("GBPUSD", 0.0005, True),
        ("EURUSD", 0.0005, True),
        ("NZDUSD", 0.0003, True),
    ]

    grand_total = 0.0
    for sym_fx, step, step_is_price in fx_configs:
        info_fx = mt5.symbol_info(sym_fx)
        bars_fx = load_closed_bars(sym_fx, "M15", 60)
        if not bars_fx:
            print(f"\n  ⚠️  {sym_fx}: no bars")
            continue
        print(f"\n  {sym_fx}: {len(bars_fx)} bars (60d)")

        best = None
        for mop in [60, 80]:
            for mom in [True, False]:
                r = run_engine(sym_fx, bars_fx, info_fx, step, mop, 1, 1.0, "rearm_lvl2_exc1", mom, step_is_price)
                if best is None or r["combined"] > best["combined"]:
                    best = {"mop": mop, "mom": mom, **r}

        grand_total += best["combined"]
        mom_str = "ON" if best["mom"] else "OFF"
        print(f"    BEST: MO={best['mop']}, mom={mom_str} → ${best['combined']:,.2f}")
        print(f"    {best['closes']} closes, {best['rearm_opens']} rearm opens, max_seen={best['max_open_seen']}")

    print(f"\n{'='*110}")
    print(f"  FX M15 TOTAL: ${grand_total:,.2f}")
    print(f"  BTCUSD M15: $1,789,450")
    print(f"  COMBINED M15: ${grand_total + 1789450:,.2f}")
    print(f"{'='*110}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
