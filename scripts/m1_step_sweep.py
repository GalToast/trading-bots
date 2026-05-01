#!/usr/bin/env python3
"""
M1 Step Size Sweep — BTCUSD 1-minute bars

Testing: step sizes from $5 to $200 on M1 timeframe
Goal: Find the M1 sweet spot like we found $100 on M5
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class M1Test:
    step: float
    max_open: int


def load_m15_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def simulate_m1_engine(symbol: str, bars: list[dict], info, step: float, max_open: int, alpha: float = 1.0, gap: int = 1, momentum_gate: bool = True) -> dict:
    """Simulate M1 engine using unified runner's process_symbol."""
    from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state

    cfg = {
        "step": step,
        "max_open_per_side": max_open,
        "close_alpha": alpha,
        "close_gap": gap,
        "momentum_gate": momentum_gate,
        "rearm_cooldown_bars": 0,
        "timeframe": "M15",
    }

    state = init_symbol_state(symbol, cfg, bars)
    state = process_symbol(symbol, cfg, bars, state)

    return {
        "combined_net_usd": state.realized_net_usd,
        "realized_closes": state.realized_closes,
        "rearm_opens": state.rearm_opens,
        "max_open_total": state.max_open_total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 Step Size Sweep — BTCUSD")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "m1_step_sweep.csv"))
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbol = args.symbol
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Symbol info not found for {symbol}")
            return 1

        bars = load_m15_bars(symbol, args.days)
        if not bars:
            print(f"No M15 bars for {symbol}")
            return 1

        print(f"\n{'='*120}")
        print(f"  M15 STEP SIZE SWEEP — {symbol}, {args.days}d ({len(bars)} M15 bars)")
        print(f"{'='*120}\n")

        tests = [
            M1Test(step=5.0, max_open=20),
            M1Test(step=10.0, max_open=30),
            M1Test(step=20.0, max_open=40),
            M1Test(step=30.0, max_open=40),
            M1Test(step=50.0, max_open=40),
            M1Test(step=75.0, max_open=40),
            M1Test(step=100.0, max_open=40),
            M1Test(step=100.0, max_open=60),
            M1Test(step=150.0, max_open=40),
            M1Test(step=200.0, max_open=40),
        ]

        rows = []
        for t in tests:
            print(f"  Testing M1 step=${t.step}, max_open={t.max_open}...")
            result = simulate_m1_engine(symbol, bars, info, step=t.step, max_open=t.max_open, alpha=1.0, gap=1)
            realized = result.get("combined_net_usd", 0)
            closes = result.get("realized_closes", 0)
            rearm = result.get("rearm_opens", 0)
            max_seen = result.get("max_open_total", 0)
            print(f"    → ${realized:,.2f}, {closes} closes, {rearm} rearm, max_seen={max_seen}")

            rows.append({
                "timeframe": "M15",
                "step": t.step,
                "max_open": t.max_open,
                "realized_usd": round(realized, 2),
                "closes": closes,
                "rearm_opens": rearm,
                "max_seen": max_seen,
            })

        # Sort by realized
        rows.sort(key=lambda x: x["realized_usd"], reverse=True)

        print(f"\n{'='*120}")
        print(f"  RESULTS (sorted by realized)")
        print(f"{'='*120}")
        print(f"  {'TF':<6} {'Step':>8} {'MaxOpen':>8} {'Realized':>14} {'Closes':>8} {'Rearm':>8} {'MaxSeen':>8}")
        print(f"  {'─'*80}")
        for r in rows:
            print(f"  {r['timeframe']:<6} ${r['step']:>7,.0f} {r['max_open']:>8} ${r['realized_usd']:>13,.2f} {r['closes']:>8} {r['rearm_opens']:>8} {r['max_seen']:>8}")

        # Write CSV
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timeframe", "step", "max_open", "realized_usd", "closes", "rearm_opens", "max_seen"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  Wrote {out_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
