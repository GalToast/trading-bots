"""Sweep adaptive step multiplier thresholds and multipliers.

Tests 400 combinations of:
- threshold_1: [5, 7, 10, 12, 15]  (first widening trigger)
- threshold_2: [12, 15, 20, 25, 30]  (second widening trigger)
- multiplier_1: [1.2, 1.3, 1.5, 1.8]  (step multiplier at threshold_1)
- multiplier_2: [1.5, 1.8, 2.0, 2.5]  (step multiplier at threshold_2)

Run: python scripts/benchmark_adaptive_step_multiplier_sweep.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).parent))
from tick_penetration_lattice_core import (
    TickStatefulRearmEngine,
    engine_from_args,
    load_ticks_range,
)

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc

# Sweep ranges
THRESHOLD_1_VALUES = [5, 7, 10, 12, 15]
THRESHOLD_2_VALUES = [12, 15, 20, 25, 30]
MULTIPLIER_1_VALUES = [1.2, 1.3, 1.5, 1.8]
MULTIPLIER_2_VALUES = [1.5, 1.8, 2.0, 2.5]

# Default current values (hard-coded in tick_penetration_lattice_core.py)
CURRENT_T1 = 10
CURRENT_T2 = 20
CURRENT_M1 = 1.5
CURRENT_M2 = 2.0


def run_sweep():
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return

    try:
        symbol = "EURUSD"
        timeframe = "M1"
        lookback_days = 14

        end_utc = datetime.now(UTC)
        start_utc = end_utc - timedelta(days=lookback_days)

        print("=" * 100)
        print(f"ADAPTIVE STEP MULTIPLIER SWEEP")
        print(f"  Symbol: {symbol}, Timeframe: {timeframe}")
        print(f"  Window: {start_utc.strftime('%Y-%m-%d %H:%M')} to {end_utc.strftime('%Y-%m-%d %H:%M')} ({lookback_days} days)")
        print(f"  Combos: {len(THRESHOLD_1_VALUES)} x {len(THRESHOLD_2_VALUES)} x {len(MULTIPLIER_1_VALUES)} x {len(MULTIPLIER_2_VALUES)} = {len(THRESHOLD_1_VALUES) * len(THRESHOLD_2_VALUES) * len(MULTIPLIER_1_VALUES) * len(MULTIPLIER_2_VALUES)}")
        print("=" * 100)
        print()

        # Load ticks
        ticks = load_ticks_range(symbol, start_utc, end_utc)
        if not ticks:
            print(f"No ticks loaded for {symbol}. Try a different date range.")
            return

        print(f"Loaded {len(ticks)} ticks for {symbol}")
        print()

        # Base engine config (use a config known to produce closes)
        # Step is in PRICE UNITS: for EURUSD pip_size=0.0001, so 2.0 pips = 0.0002
        base_kwargs = dict(
            symbol=symbol,
            step=0.0002,  # 2.0 pips — tight enough to fill on M1
            max_open_per_side=24,
            variant_name="rearm_lvl2_exc2",  # live FX variant
            timeframe_name=timeframe,
            close_alpha=1.0,  # full bar extreme (live setting)
            momentum_gate=False,  # DISABLE momentum gate for backtest
            cooldown_bars=12,  # live FX cooldown
            sell_gap=1,
            buy_gap=1,
        )

        # Test baseline (current defaults)
        engine = engine_from_args(**base_kwargs)
        for tick in ticks:
            engine.process_tick(tick)
        baseline_net = float(engine.state.realized_net_usd)
        baseline_closes = int(engine.state.realized_closes)
        baseline_max_dd = _compute_max_drawdown(engine)
        print(f"BASELINE (t1=10, t2=20, m1=1.5x, m2=2.0x):")
        print(f"  Closes: {baseline_closes}, Net: ${baseline_net:.2f}, $/close: ${baseline_net/max(1,baseline_closes):.2f}")
        print(f"  Max drawdown: ${baseline_max_dd:.2f}")
        print()

        # Sweep
        results = []
        total = len(THRESHOLD_1_VALUES) * len(THRESHOLD_2_VALUES) * len(MULTIPLIER_1_VALUES) * len(MULTIPLIER_2_VALUES)
        count = 0

        for t1 in THRESHOLD_1_VALUES:
            for t2 in THRESHOLD_2_VALUES:
                for m1 in MULTIPLIER_1_VALUES:
                    for m2 in MULTIPLIER_2_VALUES:
                        count += 1
                        if t1 >= t2:
                            continue  # Skip invalid configs

                        engine = engine_from_args(**base_kwargs)
                        # Monkey-patch the adaptive config (engine uses adapt_cfg internally)
                        if hasattr(engine, 'adapt_cfg'):
                            engine.adapt_cfg.threshold_1 = t1
                            engine.adapt_cfg.threshold_2 = t2
                            engine.adapt_cfg.multiplier_1 = m1
                            engine.adapt_cfg.multiplier_2 = m2

                        for tick in ticks:
                            engine.process_tick(tick)

                        net = float(engine.state.realized_net_usd)
                        closes = int(engine.state.realized_closes)
                        max_dd = _compute_max_drawdown(engine)
                        per_close = net / max(1, closes)

                        results.append({
                            "t1": t1, "t2": t2, "m1": m1, "m2": m2,
                            "closes": closes, "net_usd": round(net, 2),
                            "per_close": round(per_close, 4),
                            "max_drawdown": round(max_dd, 2),
                        })

                        if count % 50 == 0 or count == total:
                            print(f"  Progress: {count}/{total} ({100*count/total:.0f}%)")

        # Sort by net PnL descending
        results.sort(key=lambda r: r["net_usd"], reverse=True)

        # Print top 20
        print()
        print("=" * 100)
        print("TOP 20 CONFIGURATIONS BY NET PnL")
        print("=" * 100)
        print(f"{'Rank':>4} {'T1':>4} {'T2':>4} {'M1':>5} {'M2':>5} {'Closes':>7} {'Net $':>10} {'$/Close':>9} {'Max DD':>10}")
        print("-" * 100)
        for i, r in enumerate(results[:20]):
            print(f"{i+1:>4} {r['t1']:>4} {r['t2']:>4} {r['m1']:>5.1f} {r['m2']:>5.1f} {r['closes']:>7} {r['net_usd']:>10.2f} {r['per_close']:>9.4f} {r['max_drawdown']:>10.2f}")

        # Print bottom 5 (worst)
        print()
        print("=" * 100)
        print("BOTTOM 5 CONFIGURATIONS (WORST)")
        print("=" * 100)
        print(f"{'Rank':>4} {'T1':>4} {'T2':>4} {'M1':>5} {'M2':>5} {'Closes':>7} {'Net $':>10} {'$/Close':>9} {'Max DD':>10}")
        print("-" * 100)
        for i, r in enumerate(results[-5:]):
            rank = len(results) - 5 + i + 1
            print(f"{rank:>4} {r['t1']:>4} {r['t2']:>4} {r['m1']:>5.1f} {r['m2']:>5.1f} {r['closes']:>7} {r['net_usd']:>10.2f} {r['per_close']:>9.4f} {r['max_drawdown']:>10.2f}")

        # Save results
        output_path = REPO / "reports" / "sweep_adaptive_step_multiplier_eurusd_14d.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump({
                "symbol": symbol,
                "timeframe": timeframe,
                "window_days": lookback_days,
                "start_utc": start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "ticks_loaded": len(ticks),
                "baseline": {
                    "net_usd": baseline_net,
                    "closes": baseline_closes,
                    "per_close": round(baseline_net / max(1, baseline_closes), 4),
                    "max_drawdown": baseline_max_dd,
                },
                "top_20": results[:20],
                "bottom_5": results[-5:],
                "all_results": results,
            }, f, indent=2)

        print(f"\nResults saved to: {output_path}")

    finally:
        mt5.shutdown()


def _compute_max_drawdown(engine):
    """Approximate max drawdown from realized closes."""
    # Simple: track running net and find max peak-to-trough
    # The engine doesn't expose per-close PnL directly, so we use realized_net as proxy
    # A better approach would track per-close events
    return 0.0  # Placeholder — would need event-level tracking


if __name__ == "__main__":
    run_sweep()
