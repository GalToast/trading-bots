#!/usr/bin/env python3
"""Tick-native gap=0 (cascade) vs gap=1 validation on recent BTC M15 data.

The bar-level sweep proved cascade (gap=0) = 27x better than gap=1:
  $15 step, gap=0: $8,884/hr (204,017 closes, $86.55/close)
  $15 step, gap=1:   $328/hr (  4,306 closes, $152/close)

But bar-level replay may overstate cascade because a single bar's high/low
can sweep all levels at once. This test uses TICK data to see if cascade
still holds at tick granularity.

Uses TickStatefulRearmEngine from tick_penetration_lattice_core.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tick_penetration_lattice_core import (
    TickStatefulRearmEngine,
    engine_from_args,
    load_ticks_range,
    tick_pnl_usd,
)


def replay_engine(symbol: str, timeframe: str, step: float, max_open: int,
                  sell_gap: int, buy_gap: int, start: datetime, end: datetime,
                  *, momentum_gate: bool = False, variant_name: str = "rearm_lvl2_exc1") -> dict:
    engine = engine_from_args(
        symbol=symbol,
        timeframe_name=timeframe,
        step=step,
        max_open_per_side=max_open,
        variant_name=variant_name,
        close_alpha=1.0,
        momentum_gate=momentum_gate,
        cooldown_bars=0,
        sell_gap=sell_gap,
        buy_gap=buy_gap,
    )

    chunk = timedelta(hours=24)
    cursor = start
    total_ticks = 0
    last_tick = None

    while cursor < end:
        chunk_end = min(end, cursor + chunk)
        ticks = load_ticks_range(symbol, cursor, chunk_end)
        if ticks:
            last_tick = ticks[-1]
            total_ticks += engine.process_ticks(ticks, action_sink=None, event_path=None, emit=False)
        cursor = chunk_end

    # Compute floating PnL
    floating = 0.0
    if last_tick:
        bid = float(last_tick["bid"])
        ask = float(last_tick["ask"])
        for ticket in engine.state.open_tickets or []:
            direction = str(ticket.get("direction", "")).upper()
            fill = float(ticket.get("fill_price", 0.0))
            if direction == "BUY":
                floating += tick_pnl_usd(symbol, direction, fill, bid)
            elif direction == "SELL":
                floating += tick_pnl_usd(symbol, direction, fill, ask)

    hours = max((end - start).total_seconds() / 3600.0, 1.0)
    net = float(engine.state.realized_net_usd)
    closes = int(engine.state.realized_closes)
    avg_close = net / closes if closes > 0 else 0.0
    net_per_hr = net / hours

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "step": step,
        "max_open": max_open,
        "sell_gap": sell_gap,
        "buy_gap": buy_gap,
        "hours": hours,
        "ticks_processed": total_ticks,
        "closes": closes,
        "net_usd": round(net, 2),
        "avg_per_close": round(avg_close, 2),
        "net_per_hr": round(net_per_hr, 2),
        "floating_usd": round(floating, 2),
        "open_tickets_remaining": len(engine.state.open_tickets or []),
    }


def main():
    mt5.initialize()

    # Use recent 7-day tick window
    now = datetime.now(timezone.utc)
    end = now
    start = now - timedelta(days=7)

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--timeframe", default="M1", choices=["M1", "M5", "M15", "H1"])
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--chunk-hours", type=int, default=24)
    parser.add_argument("--variant", default="rearm_lvl2_exc1")
    parser.add_argument("--max-open", type=int, default=80)
    args = parser.parse_args()

    symbol = args.symbol
    timeframe = args.timeframe
    start = end - timedelta(days=args.days)
    max_open_per_side = args.max_open

    print(f"=== TICK-NATIVE GAP=0 vs GAP=1 VALIDATION ===")
    print(f"Symbol: {symbol}, Timeframe: {timeframe}")
    print(f"Window: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Duration: {args.days} days ({args.days * 24} hours)")
    print()

    # Sweep across steps matching the bar-level study
    steps = [15.0, 20.0, 25.0]
    max_open_per_side = 80

    configs = []
    for step in steps:
        configs.append({"step": step, "sell_gap": 0, "buy_gap": 0, "label": f"${step:.0f} gap=0"})
        configs.append({"step": step, "sell_gap": 1, "buy_gap": 1, "label": f"${step:.0f} gap=1"})

    results = []
    for cfg in configs:
        print(f"Running {cfg['label']} ...", end=" ", flush=True)
        r = replay_engine(
            symbol=symbol,
            timeframe=timeframe,
            step=cfg["step"],
            max_open=max_open_per_side,
            sell_gap=cfg["sell_gap"],
            buy_gap=cfg["buy_gap"],
            start=start,
            end=end,
            variant_name=args.variant,
        )
        results.append((cfg["label"], r))
        print(f"closes={r['closes']}, net=${r['net_usd']:.2f}, ${r['net_per_hr']:.2f}/hr, avg=${r['avg_per_close']:.2f}/close, float=${r['floating_usd']:.2f}")

    print()
    print("=" * 100)
    print(f"{'Config':<25} {'Hours':>7} {'Ticks':>10} {'Closes':>8} {'Net $':>12} {'$/close':>9} {'$/hr':>10} {'Float $':>10} {'Open':>5}")
    print("-" * 100)
    for label, r in results:
        print(f"{label:<25} {r['hours']:>7.0f} {r['ticks_processed']:>10} {r['closes']:>8} {r['net_usd']:>12.2f} {r['avg_per_close']:>9.2f} {r['net_per_hr']:>10.2f} {r['floating_usd']:>10.2f} {r['open_tickets_remaining']:>5}")
    print("=" * 100)

    # Summary comparison
    print()
    print("=== GAP=0 vs GAP=1 RATIO ===")
    gap0_results = [(l, r) for l, r in results if "gap=0" in l]
    gap1_results = [(l, r) for l, r in results if "gap=1" in l]

    for (label0, r0), (label1, r1) in zip(gap0_results, gap1_results):
        closes_ratio = r0["closes"] / max(r1["closes"], 1)
        net_ratio = r0["net_usd"] / max(abs(r1["net_usd"]), 0.01)
        per_hr_ratio = r0["net_per_hr"] / max(abs(r1["net_per_hr"]), 0.01)
        print(f"  {label0} vs {label1}:")
        print(f"    Closes ratio: {closes_ratio:.1f}x")
        print(f"    Net $ ratio:  {net_ratio:.1f}x")
        print(f"    $/hr ratio:   {per_hr_ratio:.1f}x")

    mt5.shutdown()


if __name__ == "__main__":
    main()
