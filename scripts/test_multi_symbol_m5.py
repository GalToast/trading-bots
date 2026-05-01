#!/usr/bin/env python3
"""Multi-symbol M5 validation: ETHUSD, SOLUSD, XRPUSD with optimal BTC config."""
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


def load_closed_bars(symbol, tf_name, days):
    tf_map = {"M5": mt5.TIMEFRAME_M5, "H1": mt5.TIMEFRAME_H1}
    bars_per_day = {"M5": 288, "H1": 24}
    tf = tf_map[tf_name]
    total = bars_per_day[tf_name] * days
    all_rates = []
    offset = 1
    while offset < total:
        rates = mt5.copy_rates_from_pos(symbol, tf, offset, min(100000, total - offset))
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

    # Test config: optimal BTCUSD M5 on other crypto symbols
    configs = [
        ("ETHUSD", 100.0, True),    # $100 steps
        ("SOLUSD", 10.0, True),     # $10 steps
        ("XRPUSD", 0.10, True),     # $0.10 steps
        ("DOGEUSD", 0.01, True),    # $0.01 steps
    ]

    print(f"\n{'='*110}")
    print(f"  MULTI-SYMBOL M5 VALIDATION — Optimal BTCUSD config on other crypto")
    print(f"  Config: max_open=60, gap=1, alpha=1.00, momentum=OFF, 90d")
    print(f"{'='*110}")

    grand_total = 0.0
    for sym, step, step_is_price in configs:
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"\n  ⚠️  {sym}: no symbol info")
            continue
        bars = load_closed_bars(sym, "M5", 90)
        if not bars:
            print(f"\n  ⚠️  {sym}: no bars")
            continue
        print(f"\n  {sym}: {len(bars)} bars, spread={spread_price(info):.6f}")

        t0 = time.time()
        r = run_engine(sym, bars, info, step, 60, 1, 1.0, "rearm_lvl2_exc1", False, step_is_price)
        elapsed = time.time() - t0
        grand_total += r["combined"]

        print(f"    ${r['combined']:>12,.2f}  (real=${r['realized']:>10,.2f}, flt=${r['floating']:>9,.2f})")
        print(f"    {r['closes']} closes, {r['rearm_opens']} rearm opens, max_seen={r['max_open_seen']} [{elapsed:.1f}s]")

    print(f"\n{'='*110}")
    print(f"  MULTI-SYMBOL TOTAL: ${grand_total:,.2f}")
    print(f"  BTCUSD M5: $663,000 (from qwen-main's honing)")
    print(f"  COMBINED CRYPTO: ${grand_total + 663000:,.2f}")
    print(f"{'='*110}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
