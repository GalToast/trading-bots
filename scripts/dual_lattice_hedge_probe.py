#!/usr/bin/env python3
"""Dual-Lattice Hedge Probe — test the wave cancellation theory.

Hypothesis: Running symmetric BUY-tight + SELL-tight lattices on the same symbol:
1. Net floating P/L cancels (one wins when other loses)
2. Net realized profit ADDS (both close at profit during oscillation)
3. Trend risk eliminated by floating cancellation

This sim runs two mirrored engines on the same tick stream and measures:
- Per-bar net floating P/L (should stay near $0 if theory holds)
- Per-bar realized P/L (should exceed single lattice)
- Drawdown events (when does symmetry break?)
- Correlation between the two lattices' floating P/L (should be negative)

Usage:
    python scripts/dual_lattice_hedge_probe.py --symbol NAS100 --days 7
    python scripts/dual_lattice_hedge_probe.py --symbol EURUSD --days 14
    python scripts/dual_lattice_hedge_probe.py --all  # run all symbols
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from tick_penetration_lattice_core import (
    TickStatefulRearmEngine,
    TickTicket,
    TickRearmToken,
    TickEngineState,
    engine_from_args,
    load_recent_bars,
    tick_pnl_usd,
    timeframe_seconds,
)

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CSV = REPORTS / "dual_lattice_hedge_probe.csv"
DEFAULT_MD = REPORTS / "dual_lattice_hedge_probe.md"
DEFAULT_JSON = REPORTS / "dual_lattice_hedge_probe.json"


VOLUME = 0.01
UTC = timezone.utc


@dataclass
class LatticeSnapshot:
    """Snapshot of one lattice's state at a point in time."""
    symbol: str
    side: str  # "BUY-tight" or "SELL-tight"
    tick_time: int
    realized_net: float
    realized_closes: int
    floating_pnl: float
    open_count: int
    anchor: float


@dataclass
class DualSnapshot:
    """Combined snapshot of both lattices."""
    tick_time: int
    buy_realized: float
    sell_realized: float
    buy_floating: float
    sell_floating: float
    net_floating: float  # buy_floating + sell_floating
    net_realized: float  # buy_realized + sell_realized
    buy_opens: int
    sell_opens: int
    buy_closes: int
    sell_closes: int


@dataclass
class ProbeResult:
    symbol: str
    days: int
    timeframe: str
    # Single lattice baselines
    single_buy_realized: float
    single_buy_floating_max: float
    single_buy_closes: int
    single_sell_realized: float
    single_sell_floating_max: float
    single_sell_closes: int
    # Dual lattice results
    dual_net_realized: float
    dual_net_floating_mean: float
    dual_net_floating_std: float
    dual_net_floating_max: float
    dual_net_floating_min: float
    dual_buy_realized: float
    dual_sell_realized: float
    dual_buy_closes: int
    dual_sell_closes: int
    dual_correlation: float  # correlation between buy/sell floating
    # Comparison
    realized_vs_single_buy: float  # dual_net / single_buy
    realized_vs_single_sell: float  # dual_net / single_sell
    realized_vs_combined: float  # dual_net / (single_buy + single_sell)
    floating_reduction: float  # max(single floating) / dual floating max


def compute_floating(engine: TickStatefulRearmEngine, symbol: str, bid: float, ask: float) -> float:
    """Compute total floating P/L for an engine."""
    total = 0.0
    for ticket in engine.state.open_tickets or []:
        direction = str(ticket.get("direction", "") or "").upper()
        fill_price = float(ticket.get("fill_price", ticket.get("trigger_level", 0.0)))
        total += tick_pnl_usd(symbol, direction, fill_price, bid if direction == "BUY" else ask, volume=engine.volume)
    return total


def run_single_lattice(symbol: str, ticks: list, *, step_buy: float, step_sell: float, max_open: int, alpha: float, variant: str) -> dict:
    """Run a single lattice and return results."""
    engine = engine_from_args(
        symbol=symbol,
        timeframe_name="M15",
        step=max(step_buy, step_sell),
        max_open_per_side=max_open,
        variant_name=variant,
        close_alpha=alpha,
        close_style="all_profitable",
        momentum_gate=False,
        cooldown_bars=1,
        sell_gap=1,
        buy_gap=1,
        step_sell=step_sell,
        step_buy=step_buy,
        volume=VOLUME,
        max_floating_loss_usd=-999999,  # Disable kill to measure pure floating
    )

    realized_net = 0.0
    realized_closes = 0
    max_floating = 0.0
    min_floating = 0.0

    for tick in ticks:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        floating = compute_floating(engine, symbol, bid, ask)
        max_floating = max(max_floating, floating)
        min_floating = min(min_floating, floating)

        realized_net = float(engine.state.realized_net_usd)
        realized_closes = int(engine.state.realized_closes)

    return {
        "realized_net": realized_net,
        "realized_closes": realized_closes,
        "max_floating": max_floating,
        "min_floating": min_floating,
        "final_opens": len(engine.state.open_tickets),
    }


def run_dual_lattice(symbol: str, ticks: list, *, step_buy: float, step_sell: float, max_open: int, alpha: float, variant: str) -> dict:
    """Run two mirrored lattices (BUY-tight + SELL-tight) on the same tick stream."""
    # BUY-tight: tight BUY steps, wide SELL steps
    buy_engine = engine_from_args(
        symbol=symbol,
        timeframe_name="M15",
        step=max(step_buy, step_sell),
        max_open_per_side=max_open,
        variant_name=variant,
        close_alpha=alpha,
        close_style="all_profitable",
        momentum_gate=False,
        cooldown_bars=1,
        sell_gap=1,
        buy_gap=1,
        step_sell=step_sell * 2.0,  # Wide SELL
        step_buy=step_buy,  # Tight BUY
        volume=VOLUME,
        max_floating_loss_usd=-999999,
    )

    # SELL-tight: tight SELL steps, wide BUY steps
    sell_engine = engine_from_args(
        symbol=symbol,
        timeframe_name="M15",
        step=max(step_buy, step_sell),
        max_open_per_side=max_open,
        variant_name=variant,
        close_alpha=alpha,
        close_style="all_profitable",
        momentum_gate=False,
        cooldown_bars=1,
        sell_gap=1,
        buy_gap=1,
        step_sell=step_sell,  # Tight SELL
        step_buy=step_buy * 2.0,  # Wide BUY
        volume=VOLUME,
        max_floating_loss_usd=-999999,
    )

    snapshots: list[DualSnapshot] = []
    buy_floating_series: list[float] = []
    sell_floating_series: list[float] = []

    for tick in ticks:
        bid = float(tick["bid"])
        ask = float(tick["ask"])

        buy_engine.process_tick(tick, action_sink=None, event_path=None, emit=False)
        sell_engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        buy_floating = compute_floating(buy_engine, symbol, bid, ask)
        sell_floating = compute_floating(sell_engine, symbol, bid, ask)

        buy_floating_series.append(buy_floating)
        sell_floating_series.append(sell_floating)

        snapshots.append(DualSnapshot(
            tick_time=int(tick["time"]),
            buy_realized=float(buy_engine.state.realized_net_usd),
            sell_realized=float(sell_engine.state.realized_net_usd),
            buy_floating=buy_floating,
            sell_floating=sell_floating,
            net_floating=buy_floating + sell_floating,
            net_realized=float(buy_engine.state.realized_net_usd) + float(sell_engine.state.realized_net_usd),
            buy_opens=len(buy_engine.state.open_tickets),
            sell_opens=len(sell_engine.state.open_tickets),
            buy_closes=int(buy_engine.state.realized_closes),
            sell_closes=int(sell_engine.state.realized_closes),
        ))

    # Compute statistics
    net_floatings = [s.net_floating for s in snapshots]
    buy_floatings = [s.buy_floating for s in snapshots]
    sell_floatings = [s.sell_floating for s in snapshots]

    # Correlation
    if len(buy_floatings) > 1:
        mean_b = sum(buy_floatings) / len(buy_floatings)
        mean_s = sum(sell_floatings) / len(sell_floatings)
        cov = sum((b - mean_b) * (s - mean_s) for b, s in zip(buy_floatings, sell_floatings)) / len(buy_floatings)
        std_b = (sum((b - mean_b) ** 2 for b in buy_floatings) / len(buy_floatings)) ** 0.5
        std_s = (sum((s - mean_s) ** 2 for s in sell_floatings) / len(sell_floatings)) ** 0.5
        correlation = cov / (std_b * std_s) if std_b > 0 and std_s > 0 else 0
    else:
        correlation = 0

    final = snapshots[-1] if snapshots else DualSnapshot(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    return {
        "buy_realized": final.buy_realized,
        "sell_realized": final.sell_realized,
        "net_realized": final.net_realized,
        "buy_closes": final.buy_closes,
        "sell_closes": final.sell_closes,
        "net_floating_mean": sum(net_floatings) / len(net_floatings) if net_floatings else 0,
        "net_floating_std": (sum((x - sum(net_floatings)/len(net_floatings))**2 for x in net_floatings) / len(net_floatings))**0.5 if len(net_floatings) > 1 else 0,
        "net_floating_max": max(net_floatings) if net_floatings else 0,
        "net_floating_min": min(net_floatings) if net_floatings else 0,
        "correlation": correlation,
        "snapshots": snapshots,
    }


def load_bars_as_ticks(symbol: str, timeframe: str, days: int) -> list[dict]:
    """Load M15/M5 bars and convert to tick-like events."""
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }
    tf_val = tf_map.get(timeframe, mt5.TIMEFRAME_M15)
    bars_count = days * 24 * 4  # Approximate M15 bars per day

    bars = mt5.copy_rates_from_pos(symbol, tf_val, 0, bars_count)
    if bars is None or len(bars) == 0:
        return []

    # Convert bars to tick-like events (use close as bid/ask with spread)
    ticks = []
    for bar in bars:
        spread = (bar["high"] - bar["low"]) * 0.1  # 10% of bar range as spread
        ticks.append({
            "time": int(bar["time"]),
            "time_msc": int(bar["time"]) * 1000,
            "bid": float(bar["close"]),
            "ask": float(bar["close"]) + spread,
            "last": float(bar["close"]),
            "volume": int(bar["tick_volume"]),
        })

    return ticks


def run_probe(symbol: str, days: int, timeframe: str) -> ProbeResult:
    """Run the full dual-lattice probe for one symbol."""
    print(f"\n{'='*60}")
    print(f"  {symbol} — Dual-Lattice Hedge Probe ({days}d {timeframe})")
    print(f"{'='*60}")

    # Load bars as ticks
    print(f"  Loading {days} days of {timeframe} bars as tick events...")
    ticks = load_bars_as_ticks(symbol, timeframe, days)
    print(f"  Loaded {len(ticks)} bars-as-ticks")

    if len(ticks) < 100:
        print(f"  WARNING: Too few bars ({len(ticks)}), skipping")
        return None

    # Use ATR-based steps
    # Compute ATR from bars
    atrs = []
    for i in range(1, min(len(ticks), 100)):
        atrs.append(abs(float(ticks[i]["bid"]) - float(ticks[i-1]["bid"])))
    atr = sum(atrs) / len(atrs) if atrs else 1.0

    # Use ATR-based step sizing (1× ATR as base step)
    step_base = atr

    # Single lattice baselines
    print(f"  ATR ≈ {step_base:.4f}, running single lattices...")

    # Single BUY-tight lattice
    single_buy = run_single_lattice(
        symbol, ticks,
        step_buy=step_base,
        step_sell=step_base * 2,
        max_open=12,
        alpha=0.5,
        variant="rearm_lvl2_exc2",
    )
    print(f"  Single BUY-tight: ${single_buy['realized_net']:.2f} ({single_buy['realized_closes']} closes), max floating ${single_buy['max_floating']:.2f}")

    # Single SELL-tight lattice
    single_sell = run_single_lattice(
        symbol, ticks,
        step_buy=step_base * 2,
        step_sell=step_base,
        max_open=12,
        alpha=0.5,
        variant="rearm_lvl2_exc2",
    )
    print(f"  Single SELL-tight: ${single_sell['realized_net']:.2f} ({single_sell['realized_closes']} closes), max floating ${single_sell['max_floating']:.2f}")

    # Dual lattice
    print(f"  Running dual lattice (BUY-tight + SELL-tight)...")
    dual = run_dual_lattice(
        symbol, ticks,
        step_buy=step_base,
        step_sell=step_base,
        max_open=12,
        alpha=0.5,
        variant="rearm_lvl2_exc2",
    )

    print(f"  Dual BUY:    ${dual['buy_realized']:.2f} ({dual['buy_closes']} closes)")
    print(f"  Dual SELL:   ${dual['sell_realized']:.2f} ({dual['sell_closes']} closes)")
    print(f"  Dual NET:    ${dual['net_realized']:.2f}")
    print(f"  Dual floating: mean=${dual['net_floating_mean']:.2f}, std=${dual['net_floating_std']:.2f}, max=${dual['net_floating_max']:.2f}")
    print(f"  Correlation: {dual['correlation']:.3f}")

    # Compute comparison metrics
    combined_single = single_buy['realized_net'] + single_sell['realized_net']
    realized_vs_combined = dual['net_realized'] / combined_single if combined_single != 0 else 0

    single_max_floating = max(abs(single_buy['max_floating']), abs(single_sell['max_floating']))
    floating_reduction = dual['net_floating_max'] / single_max_floating if single_max_floating > 0 else 0

    result = ProbeResult(
        symbol=symbol,
        days=days,
        timeframe=timeframe,
        single_buy_realized=single_buy['realized_net'],
        single_buy_floating_max=single_buy['max_floating'],
        single_buy_closes=single_buy['realized_closes'],
        single_sell_realized=single_sell['realized_net'],
        single_sell_floating_max=single_sell['max_floating'],
        single_sell_closes=single_sell['realized_closes'],
        dual_net_realized=dual['net_realized'],
        dual_net_floating_mean=dual['net_floating_mean'],
        dual_net_floating_std=dual['net_floating_std'],
        dual_net_floating_max=dual['net_floating_max'],
        dual_net_floating_min=dual['net_floating_min'],
        dual_buy_realized=dual['buy_realized'],
        dual_sell_realized=dual['sell_realized'],
        dual_buy_closes=dual['buy_closes'],
        dual_sell_closes=dual['sell_closes'],
        dual_correlation=dual['correlation'],
        realized_vs_single_buy=dual['net_realized'] / single_buy['realized_net'] if single_buy['realized_net'] != 0 else 0,
        realized_vs_single_sell=dual['net_realized'] / single_sell['realized_net'] if single_sell['realized_net'] != 0 else 0,
        realized_vs_combined=realized_vs_combined,
        floating_reduction=floating_reduction,
    )

    return result


def write_csv(results: list[ProbeResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "symbol", "days", "timeframe",
            "single_buy_realized", "single_buy_closes", "single_buy_floating_max",
            "single_sell_realized", "single_sell_closes", "single_sell_floating_max",
            "dual_net_realized", "dual_buy_realized", "dual_sell_realized",
            "dual_buy_closes", "dual_sell_closes",
            "dual_net_floating_mean", "dual_net_floating_std",
            "dual_net_floating_max", "dual_net_floating_min",
            "dual_correlation",
            "realized_vs_single_buy", "realized_vs_single_sell",
            "realized_vs_combined", "floating_reduction",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "symbol": r.symbol,
                "days": r.days,
                "timeframe": r.timeframe,
                "single_buy_realized": round(r.single_buy_realized, 2),
                "single_buy_closes": r.single_buy_closes,
                "single_buy_floating_max": round(r.single_buy_floating_max, 2),
                "single_sell_realized": round(r.single_sell_realized, 2),
                "single_sell_closes": r.single_sell_closes,
                "single_sell_floating_max": round(r.single_sell_floating_max, 2),
                "dual_net_realized": round(r.dual_net_realized, 2),
                "dual_buy_realized": round(r.dual_buy_realized, 2),
                "dual_sell_realized": round(r.dual_sell_realized, 2),
                "dual_buy_closes": r.dual_buy_closes,
                "dual_sell_closes": r.dual_sell_closes,
                "dual_net_floating_mean": round(r.dual_net_floating_mean, 2),
                "dual_net_floating_std": round(r.dual_net_floating_std, 2),
                "dual_net_floating_max": round(r.dual_net_floating_max, 2),
                "dual_net_floating_min": round(r.dual_net_floating_min, 2),
                "dual_correlation": round(r.dual_correlation, 3),
                "realized_vs_single_buy": round(r.realized_vs_single_buy, 2),
                "realized_vs_single_sell": round(r.realized_vs_single_sell, 2),
                "realized_vs_combined": round(r.realized_vs_combined, 2),
                "floating_reduction": round(r.floating_reduction, 2),
            })
    print(f"\nWrote {path}")


def write_markdown(results: list[ProbeResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Dual-Lattice Hedge Probe Results",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Theory",
        "",
        "Running symmetric BUY-tight + SELL-tight lattices on the same symbol:",
        "1. **Net floating P/L should cancel** — when price rallies, BUY lattice profits, SELL lattice loses (and vice versa)",
        "2. **Net realized profit should ADD** — both lattices close at profit during oscillation",
        "3. **Trend risk eliminated** — floating positions offset each other",
        "",
        "## Key Metrics",
        "",
        "- **realized_vs_combined**: dual net realized / (single buy + single sell). >1.0 = synergy",
        "- **floating_reduction**: dual max floating / single max floating. <1.0 = risk reduction",
        "- **dual_correlation**: correlation between buy/sell floating P/L. Should be negative for cancellation",
        "",
        "## Results",
        "",
        "| Symbol | Single BUY | Single SELL | Combined | Dual Net | vs Combined | Floating Red | Correlation |",
        "|--------|-----------|-------------|----------|----------|-------------|--------------|-------------|",
    ]

    for r in results:
        combined = r.single_buy_realized + r.single_sell_realized
        lines.append(
            f"| {r.symbol} | ${r.single_buy_realized:.2f} ({r.single_buy_closes}x) "
            f"| ${r.single_sell_realized:.2f} ({r.single_sell_closes}x) "
            f"| ${combined:.2f} "
            f"| **${r.dual_net_realized:.2f}** ({r.dual_buy_closes + r.dual_sell_closes}x) "
            f"| {r.realized_vs_combined:.2f}× "
            f"| {r.floating_reduction:.2f}× "
            f"| {r.dual_correlation:.3f} |"
        )

    lines.extend([
        "",
        "## Interpretation",
        "",
        "### If realized_vs_combined > 1.0:",
        "- Dual lattice produces MORE profit than running both separately",
        "- The hedge creates a synergy effect",
        "",
        "### If floating_reduction < 1.0:",
        "- Dual lattice has LESS floating risk than single lattice",
        "- The hedge successfully cancels floating P/L",
        "",
        "### If dual_correlation is negative:",
        "- Buy and sell floating P/L move in opposite directions",
        "- This confirms the wave cancellation mechanism",
        "",
        "## Verdict",
        "",
    ])

    # Add verdict
    if results:
        avg_synergy = sum(r.realized_vs_combined for r in results) / len(results)
        avg_risk_reduction = sum(r.floating_reduction for r in results) / len(results)
        avg_correlation = sum(r.dual_correlation for r in results) / len(results)

        if avg_synergy > 1.0 and avg_risk_reduction < 1.0:
            lines.append("✅ **THEORY CONFIRMED** — dual lattice creates synergy AND reduces risk")
        elif avg_synergy > 1.0:
            lines.append(f"⚠️ **PARTIAL SUCCESS** — {avg_synergy:.2f}× synergy but no risk reduction ({avg_risk_reduction:.2f}×)")
        elif avg_risk_reduction < 1.0:
            lines.append(f"⚠️ **PARTIAL SUCCESS** — {avg_risk_reduction:.2f}× risk reduction but no synergy ({avg_synergy:.2f}×)")
        else:
            lines.append(f"❌ **THEORY NOT CONFIRMED** — no synergy ({avg_synergy:.2f}×) and no risk reduction ({avg_risk_reduction:.2f}×)")

        lines.append(f"\nAverage correlation between BUY/SELL floating: {avg_correlation:.3f}")
        if avg_correlation < -0.5:
            lines.append("✅ Strong negative correlation confirms wave cancellation")
        elif avg_correlation < 0:
            lines.append("⚠️ Weak negative correlation — partial cancellation")
        else:
            lines.append("❌ Positive correlation — no cancellation effect")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}")


def write_json(results: list[ProbeResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "results": [
            {
                "symbol": r.symbol,
                "days": r.days,
                "timeframe": r.timeframe,
                "single_lattice": {
                    "buy": {"realized": r.single_buy_realized, "closes": r.single_buy_closes, "max_floating": r.single_buy_floating_max},
                    "sell": {"realized": r.single_sell_realized, "closes": r.single_sell_closes, "max_floating": r.single_sell_floating_max},
                },
                "dual_lattice": {
                    "net_realized": r.dual_net_realized,
                    "buy_realized": r.dual_buy_realized,
                    "sell_realized": r.dual_sell_realized,
                    "buy_closes": r.dual_buy_closes,
                    "sell_closes": r.dual_sell_closes,
                    "net_floating": {
                        "mean": r.dual_net_floating_mean,
                        "std": r.dual_net_floating_std,
                        "max": r.dual_net_floating_max,
                        "min": r.dual_net_floating_min,
                    },
                    "correlation": r.dual_correlation,
                },
                "comparison": {
                    "realized_vs_combined": r.realized_vs_combined,
                    "floating_reduction": r.floating_reduction,
                },
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual-Lattice Hedge Probe")
    parser.add_argument("--symbol", default="NAS100", help="Symbol to test")
    parser.add_argument("--days", type=int, default=7, help="Days of data")
    parser.add_argument("--timeframe", default="M15", help="Timeframe")
    parser.add_argument("--all", action="store_true", help="Run all symbols")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return 1

    symbols = ["NAS100", "US30", "EURUSD", "GBPUSD", "BTCUSD", "ETHUSD"] if args.all else [args.symbol]
    results = []

    for symbol in symbols:
        try:
            result = run_probe(symbol, args.days, args.timeframe)
            if result:
                results.append(result)
        except Exception as e:
            print(f"ERROR {symbol}: {e}")
            import traceback
            traceback.print_exc()

    mt5.shutdown()

    if results:
        write_csv(results, DEFAULT_CSV)
        write_markdown(results, DEFAULT_MD)
        write_json(results, DEFAULT_JSON)

        # Print summary
        print(f"\n{'='*60}")
        print(f"  DUAL-LATTICE HEDGE PROBE — SUMMARY")
        print(f"{'='*60}")
        for r in results:
            combined = r.single_buy_realized + r.single_sell_realized
            print(f"\n  {r.symbol}:")
            print(f"    Single lattices: ${combined:.2f} (BUY ${r.single_buy_realized:.2f} + SELL ${r.single_sell_realized:.2f})")
            print(f"    Dual net:        ${r.dual_net_realized:.2f} ({r.realized_vs_combined:.2f}× combined)")
            print(f"    Floating max:    ${r.dual_net_floating_max:.2f} ({r.floating_reduction:.2f}× single)")
            print(f"    Correlation:     {r.dual_correlation:.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
