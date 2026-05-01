#!/usr/bin/env python3
"""Correlation Backtest — test structure-synchronized bundle theory.

Hypothesis: Running structure-matched lattices on correlated symbols
produces MORE profit than the sum of independent single-symbol lattices.

This tests the "structure-synchronized bundle" theory:
- ETH flag + NAS100 flag → both BUY-tight → tech rally = double close
- EURUSD pressure + GBPUSD consolidation → both active → FX move = double close

Backtest approach:
1. Load M15 bars for multiple symbols
2. Run single-symbol lattices independently
3. Run bundle (all symbols together) with SHARED floating budget
4. Compare: bundle profit vs sum of single-symbol profits

Usage:
    python scripts/correlation_backtest.py --symbols ETHUSD NAS100 --days 14
    python scripts/correlation_backtest.py --symbols EURUSD GBPUSD --days 14
    python scripts/correlation_backtest.py --symbols BTCUSD ETHUSD --days 14
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tick_penetration_lattice_core import (
    TickStatefulRearmEngine,
    engine_from_args,
    tick_pnl_usd,
)

VOLUME = 0.01
UTC = timezone.utc


def load_bars(symbol: str, days: int, timeframe: str = "M15") -> list[dict]:
    """Load M15 bars for a symbol."""
    tf_map = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
    tf_val = tf_map.get(timeframe, mt5.TIMEFRAME_M15)
    bars_count = days * 24 * 4  # M15 bars per day
    bars = mt5.copy_rates_from_pos(symbol, tf_val, 0, bars_count)
    if bars is None:
        return []
    return [
        {
            "time": int(b["time"]),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "tick_volume": int(b["tick_volume"]),
        }
        for b in bars
    ]


def simulate_single(bars: list[dict], symbol: str, step: float, max_open: int, alpha: float) -> dict:
    """Simulate a single-symbol lattice on bar data."""
    engine = engine_from_args(
        symbol=symbol,
        timeframe_name="M15",
        step=step,
        max_open_per_side=max_open,
        variant_name="rearm_lvl2_exc1",
        close_alpha=alpha,
        close_style="all_profitable",
        momentum_gate=False,
        cooldown_bars=1,
        sell_gap=1,
        buy_gap=1,
        volume=VOLUME,
        max_floating_loss_usd=-999999,  # Disable kill
    )

    for bar in bars:
        bid = bar["close"]
        ask = bar["close"] + (bar["high"] - bar["low"]) * 0.1  # Approximate spread
        tick = {
            "time": bar["time"],
            "time_msc": bar["time"] * 1000,
            "bid": bid,
            "ask": ask,
            "last": bid,
            "volume": bar["tick_volume"],
        }
        engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

    return {
        "realized_net": float(engine.state.realized_net_usd),
        "realized_closes": int(engine.state.realized_closes),
        "max_floating": float(engine.state.max_floating_loss_usd or 0),
        "final_opens": len(engine.state.open_tickets),
    }


def simulate_bundle(bars_list: list[list[dict]], symbols: list[str], step: float, max_open: int, alpha: float, shared_floating: float) -> dict:
    """Simulate a multi-symbol bundle with shared floating budget.

    All symbols share the same floating loss budget.
    This tests whether cross-symbol hedging reduces floating risk.
    """
    engines = []
    for symbol in symbols:
        engine = engine_from_args(
            symbol=symbol,
            timeframe_name="M15",
            step=step,
            max_open_per_side=max_open,
            variant_name="rearm_lvl2_exc1",
            close_alpha=alpha,
            close_style="all_profitable",
            momentum_gate=False,
            cooldown_bars=1,
            sell_gap=1,
            buy_gap=1,
            volume=VOLUME,
            max_floating_loss_usd=-999999,  # Per-symbol kill disabled
        )
        engines.append(engine)

    max_bars = max(len(b) for b in bars_list)

    # Align bars by time and process synchronously
    bar_index = {s: 0 for s in symbols}
    for bar_idx in range(max_bars):
        for i, symbol in enumerate(symbols):
            if bar_index[symbol] >= len(bars_list[i]):
                continue
            bar = bars_list[i][bar_index[symbol]]
            bid = bar["close"]
            ask = bar["close"] + (bar["high"] - bar["low"]) * 0.1
            tick = {
                "time": bar["time"],
                "time_msc": bar["time"] * 1000,
                "bid": bid,
                "ask": ask,
                "last": bid,
                "volume": bar["tick_volume"],
            }
            engines[i].process_tick(tick, action_sink=None, event_path=None, emit=False)
            bar_index[symbol] += 1

        # Check shared floating budget
        total_floating = 0.0
        for i, symbol in enumerate(symbols):
            bars_i = bars_list[i]
            if bar_index[symbol] > 0 and bar_index[symbol] <= len(bars_i):
                current_bar = bars_i[min(bar_index[symbol] - 1, len(bars_i) - 1)]
                bid = current_bar["close"]
                for ticket in engines[i].state.open_tickets or []:
                    direction = str(ticket.get("direction", "")).upper()
                    fill = float(ticket.get("fill_price", 0))
                    total_floating += tick_pnl_usd(symbol, direction, fill, bid, volume=VOLUME)

        # Kill all if shared floating exceeds budget
        if total_floating < shared_floating:
            for engine in engines:
                engine.state.max_floating_loss_usd = -999999  # Already killed
            break

    total_realized = sum(float(e.state.realized_net_usd) for e in engines)
    total_closes = sum(int(e.state.realized_closes) for e in engines)
    total_opens = sum(len(e.state.open_tickets or []) for e in engines)

    return {
        "realized_net": total_realized,
        "realized_closes": total_closes,
        "total_floating_at_kill": total_floating if total_floating < shared_floating else 0,
        "final_opens": total_opens,
        "per_symbol": {
            symbols[i]: {
                "realized": float(e.state.realized_net_usd),
                "closes": int(e.state.realized_closes),
                "opens": len(e.state.open_tickets or []),
            }
            for i, e in enumerate(engines)
        },
    }


def run_backtest(symbols: list[str], days: int, timeframe: str) -> dict:
    """Run the full correlation backtest."""
    mt5.initialize()

    # Load bars
    all_bars = {}
    for symbol in symbols:
        bars = load_bars(symbol, days, timeframe)
        all_bars[symbol] = bars
        print(f"  {symbol}: {len(bars)} bars loaded")

    if not all(all_bars[s] for s in symbols):
        print("  ERROR: Could not load bars for all symbols")
        return {}

    # Compute ATR-based steps per symbol
    steps = {}
    for symbol in symbols:
        bars = all_bars[symbol]
        atrs = []
        for i in range(1, min(len(bars), 50)):
            atrs.append(bars[i]["high"] - bars[i]["low"])
        atr = sum(atrs) / len(atrs) if atrs else 1.0
        steps[symbol] = atr  # 1x ATR as step

    # Single-symbol simulations
    print(f"\n  Running single-symbol simulations...")
    single_results = {}
    for symbol in symbols:
        result = simulate_single(all_bars[symbol], symbol, steps[symbol], max_open=12, alpha=0.5)
        single_results[symbol] = result
        print(f"    {symbol}: ${result['realized_net']:.2f} ({result['realized_closes']} closes, step=${steps[symbol]:.2f})")

    sum_single = sum(r["realized_net"] for r in single_results.values())
    sum_closes = sum(r["realized_closes"] for r in single_results.values())

    # Bundle simulation
    print(f"\n  Running bundle simulation...")
    bars_list = [all_bars[s] for s in symbols]
    # Use average step for bundle
    avg_step = sum(steps.values()) / len(steps)
    bundle_result = simulate_bundle(bars_list, symbols, avg_step, max_open=12, alpha=0.5, shared_floating=-15.0)
    print(f"    Bundle: ${bundle_result['realized_net']:.2f} ({bundle_result['realized_closes']} closes)")
    for sym, data in bundle_result.get("per_symbol", {}).items():
        print(f"      {sym}: ${data['realized']:.2f} ({data['closes']} closes)")

    # Comparison
    bundle_vs_single = bundle_result["realized_net"] / sum_single if sum_single != 0 else 0

    result = {
        "symbols": symbols,
        "days": days,
        "timeframe": timeframe,
        "single_results": single_results,
        "sum_single_realized": round(sum_single, 2),
        "sum_single_closes": sum_closes,
        "bundle_result": bundle_result,
        "bundle_vs_single": round(bundle_vs_single, 3),
        "synergy": round(bundle_result["realized_net"] - sum_single, 2),
    }

    mt5.shutdown()
    return result


def save_result(result: dict) -> None:
    output_path = ROOT / "reports" / "correlation_backtest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result["generated_at"] = datetime.now(UTC).isoformat()
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved to {output_path}")


def print_summary(result: dict) -> None:
    if not result:
        return
    symbols = result["symbols"]
    print(f"\n{'='*60}")
    print(f"  CORRELATION BACKTEST SUMMARY")
    print(f"{'='*60}")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Period: {result['days']} days {result['timeframe']}")
    print(f"\n  Single-symbol results:")
    for sym, data in result["single_results"].items():
        print(f"    {sym}: ${data['realized_net']:.2f} ({data['realized_closes']} closes)")
    print(f"\n  Sum of singles: ${result['sum_single_realized']:.2f}")
    print(f"  Bundle result:  ${result['bundle_result']['realized_net']:.2f} ({result['bundle_result']['realized_closes']} closes)")
    print(f"  Bundle vs single: {result['bundle_vs_single']:.2f}x")
    print(f"  Synergy: ${result['synergy']:.2f}")

    if result["bundle_vs_single"] > 1.0:
        print(f"\n  ✅ SYNERGY DETECTED — bundle outperformed singles by {result['bundle_vs_single']:.2f}x")
    elif result["bundle_vs_single"] > 0.9:
        print(f"\n  ⚠️ NEUTRAL — bundle ≈ sum of singles ({result['bundle_vs_single']:.2f}x)")
    else:
        print(f"\n  ❌ SUB-SYNERGY — bundle underperformed ({result['bundle_vs_single']:.2f}x)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Correlation Backtest")
    parser.add_argument("--symbols", nargs="+", default=["ETHUSD", "NAS100"], help="Symbols to test")
    parser.add_argument("--days", type=int, default=14, help="Days of data")
    parser.add_argument("--timeframe", default="M15", help="Timeframe")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_backtest(args.symbols, args.days, args.timeframe)
    if result:
        save_result(result)
        print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
