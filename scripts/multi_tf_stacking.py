#!/usr/bin/env python3
"""
Multi-Timeframe Stacking Prototype — M1 + M5 + H1 on same symbol

Runs three engines simultaneously on BTCUSD:
- M1: micro-reversions (step=$5, max_open=20)
- M5: swing-reversions (step=$25, max_open=30)
- H1: macro-reversions (step=$50, max_open=40)

Uses the unified runner's process_symbol engine (validated at $87K for H1).
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TFConfig:
    name: str
    timeframe: int
    step: float
    max_open: int
    alpha: float = 1.0
    gap: int = 1


def load_tf_bars(symbol: str, tf: int, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 24 * 60 * days if tf == mt5.TIMEFRAME_M1 else 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def simulate_single_engine(symbol: str, bars: list[dict], info, cfg: TFConfig) -> dict:
    """Simulate a single timeframe engine using the unified runner's logic."""
    from live_penetration_lattice_unified_shadow import process_symbol, SymbolState, init_symbol_state

    engine_cfg = {
        "step": cfg.step,
        "max_open_per_side": cfg.max_open,
        "close_alpha": cfg.alpha,
        "close_gap": cfg.gap,
        "momentum_gate": True,
        "rearm_cooldown_bars": 0,
        "timeframe": "M1" if cfg.timeframe == mt5.TIMEFRAME_M1 else "H1" if cfg.timeframe == mt5.TIMEFRAME_H1 else "M5",
    }

    state = init_symbol_state(symbol, engine_cfg, bars)
    state = process_symbol(symbol, engine_cfg, bars, state)

    return {
        "combined_net_usd": state.realized_net_usd,
        "realized_closes": state.realized_closes,
        "rearm_opens": state.rearm_opens,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-Timeframe Stacking Prototype")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "multi_tf_stacking.csv"))
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

        print(f"\n{'='*100}")
        print(f"  MULTI-TIMEFRAME STACKING — {symbol}, {args.days}d")
        print(f"{'='*100}")

        # Load bars for each timeframe
        configs = [
            TFConfig("M1", mt5.TIMEFRAME_M1, step=5.0, max_open=20, alpha=1.0, gap=1),
            TFConfig("M5", mt5.TIMEFRAME_M5, step=25.0, max_open=30, alpha=1.0, gap=1),
            TFConfig("H1", mt5.TIMEFRAME_H1, step=50.0, max_open=40, alpha=1.0, gap=1),
        ]

        all_results = {}
        total_stacked = 0.0
        rows = []

        for cfg in configs:
            print(f"\n  Loading {cfg.name} bars...")
            bars = load_tf_bars(symbol, cfg.timeframe, args.days)
            if not bars:
                print(f"  ⚠️  No bars for {cfg.name}")
                continue
            print(f"  {len(bars)} {cfg.name} bars loaded")

            print(f"  Simulating {cfg.name} engine (step=${cfg.step}, max_open={cfg.max_open})...")
            result = simulate_single_engine(symbol, bars, info, cfg)
            if not result:
                print(f"  ⚠️  Empty result for {cfg.name}")
                continue

            realized = result.get("combined_net_usd", 0)
            closes = result.get("realized_closes", 0)
            all_results[cfg.name] = {"realized": realized, "closes": closes, "bars": len(bars)}
            total_stacked += realized
            print(f"  {cfg.name}: ${realized:,.2f}, {closes} closes")

            rows.append({
                "timeframe": cfg.name,
                "bars": len(bars),
                "step": cfg.step,
                "max_open": cfg.max_open,
                "realized_usd": round(realized, 2),
                "closes": closes,
            })

        # Add stacked row
        rows.append({
            "timeframe": "STACKED",
            "bars": sum(r["bars"] for r in rows if r["timeframe"] != "STACKED"),
            "step": "mixed",
            "max_open": "mixed",
            "realized_usd": round(total_stacked, 2),
            "closes": sum(r["closes"] for r in rows if r["timeframe"] != "STACKED"),
        })

        print(f"\n{'='*100}")
        print(f"  RESULTS")
        print(f"{'='*100}")
        print(f"  {'TF':<10} {'Bars':>8} {'Step':>8} {'MaxOpen':>8} {'Realized':>12} {'Closes':>8}")
        print(f"  {'─'*80}")
        for r in rows:
            print(f"  {r['timeframe']:<10} {r['bars']:>8} {str(r['step']):>8} {str(r['max_open']):>8} ${r['realized_usd']:>11,.2f} {r['closes']:>8}")

        # Write CSV
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timeframe", "bars", "step", "max_open", "realized_usd", "closes"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  Wrote {out_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
