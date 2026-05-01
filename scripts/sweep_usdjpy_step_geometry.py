#!/usr/bin/env python3
"""
USDJPY Step Geometry Sweep
===========================
Tests different base_step_px sizes for USDJPY bounded rearm to find the sweet spot
between edge capture and anchor reset frequency.

Hypothesis: base_step_px=0.005 is too tight for USDJPY's volatility basin.
Wider steps should reduce anchor resets while maintaining edge capture.

Uses tick-native replay (not bar-replay) for execution-realistic results.

Usage:
  python scripts/sweep_usdjpy_step_geometry.py --days 3
  python scripts/sweep_usdjpy_step_geometry.py --days 7 --chunk-hours 6

Output:
  reports/usdjpy_step_geometry_sweep.csv
  reports/usdjpy_step_geometry_sweep.md
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from tick_penetration_lattice_core import (
    TickBoundedRearmEngine,
    bounded_engine_from_args,
    load_ticks_range,
)


ROOT = Path(__file__).resolve().parent.parent
REPORT_CSV = ROOT / "reports" / "usdjpy_step_geometry_sweep.csv"
REPORT_MD = ROOT / "reports" / "usdjpy_step_geometry_sweep.md"

# Step sizes to sweep: current 0.005 plus wider alternatives
STEP_SIZES = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030, 0.040, 0.050]

# Close gap configs to cross-sweep
CLOSE_GAPS = [1, 2, 3]

# Rearm variant
REARM_VARIANT = "rearm_lvl2_exc2"


@dataclass(frozen=True)
class SweepConfig:
    step_px: float
    close_gap: int


def make_bounded_cfg(step_px: float) -> BoundedConfig:
    """Create bounded config matching current live USDJPY settings but with different step."""
    return BoundedConfig(
        step_pips=step_px / 0.001,  # step_pips = step_px / pip_size (USDJPY pip = 0.001)
        max_open_per_side=20,
        max_floating_loss_usd=-10.0,
        vwap_lookback=20,
        regime_lookback_bars=60,
        max_range_pips=24.0,
        breakout_buffer_pips=5.0,
        max_lattice_window_bars=240,
        cooldown_bars=60,
    )


def replay_step_config(
    symbol: str,
    cfg: BoundedConfig,
    *,
    step_px: float,
    close_gap: int,
    start_utc: datetime,
    end_utc: datetime,
    chunk: timedelta,
    variant: str = REARM_VARIANT,
) -> dict[str, object]:
    """Replay a single step/close_gap config over tick data."""
    engine = bounded_engine_from_args(
        symbol=symbol,
        timeframe_name="M1",
        cfg=cfg,
        variant_name=variant,
        close_gap=close_gap,
        same_bar_min_pnl=0.0,
        same_bar_shallow_level_cap=0,
    )

    # Override step_px
    engine.base_step_px = step_px

    cursor = start_utc
    total_ticks = 0
    while cursor < end_utc:
        chunk_end = min(end_utc, cursor + chunk)
        ticks = load_ticks_range(symbol, cursor, chunk_end)
        total_ticks += engine.process_ticks(ticks, action_sink=None, event_path=None, emit=False)
        cursor = chunk_end

    realized_net = float(engine.state.realized_net_usd or 0.0)
    realized_closes = int(engine.state.realized_closes or 0)
    anchor_resets = int(engine.state.anchor_resets or 0)
    rearm_opens = int(engine.state.rearm_opens or 0)
    max_open_total = int(engine.state.max_open_total or 0)

    # Derived metrics
    avg_pnl_per_close = realized_net / realized_closes if realized_closes > 0 else 0.0
    pnl_per_anchor_reset = realized_net / anchor_resets if anchor_resets > 0 else 0.0
    resets_per_day = anchor_resets / max(1, (end_utc - start_utc).total_seconds() / 86400)

    return {
        "step_px": step_px,
        "step_pips": cfg.step_pips,
        "close_gap": close_gap,
        "ticks_processed": total_ticks,
        "realized_net_usd": round(realized_net, 3),
        "realized_closes": realized_closes,
        "avg_pnl_per_close": round(avg_pnl_per_close, 4),
        "anchor_resets": anchor_resets,
        "resets_per_day": round(resets_per_day, 2),
        "pnl_per_anchor_reset": round(pnl_per_anchor_reset, 4),
        "rearm_opens": rearm_opens,
        "max_open_total": max_open_total,
        "win_rate": "N/A",  # Would need per-close tracking
        "days_simulated": round((end_utc - start_utc).total_seconds() / 86400, 2),
    }


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "step_px",
        "step_pips",
        "close_gap",
        "ticks_processed",
        "realized_net_usd",
        "realized_closes",
        "avg_pnl_per_close",
        "anchor_resets",
        "resets_per_day",
        "pnl_per_anchor_reset",
        "rearm_opens",
        "max_open_total",
        "days_simulated",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_md(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Sort by realized_net_usd descending for the report
    sorted_rows = sorted(rows, key=lambda r: float(r["realized_net_usd"] or 0), reverse=True)

    lines = [
        "# USDJPY Step Geometry Sweep",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "**Hypothesis:** base_step_px=0.005 is too tight for USDJPY's volatility basin.",
        "Wider steps should reduce anchor reset frequency while maintaining edge capture.",
        "",
        "## Results (sorted by realized net PnL)",
        "",
        "| Step (px) | Step (pips) | Close Gap | Net PnL | Closes | Avg/Close | Resets | Resets/Day | PnL/Reset | Max Open |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in sorted_rows:
        lines.append(
            f"| {row['step_px']:.3f} | {row['step_pips']:.1f} | {row['close_gap']} | "
            f"**${row['realized_net_usd']:.2f}** | {row['realized_closes']} | "
            f"${row['avg_pnl_per_close']:.4f} | {row['anchor_resets']} | "
            f"{row['resets_per_day']:.1f} | ${row['pnl_per_anchor_reset']:.4f} | "
            f"{row['max_open_total']} |"
        )

    # Add analysis section
    lines.append("")
    lines.append("## Key Findings")
    lines.append("")

    # Find best step
    best_row = sorted_rows[0] if sorted_rows else None
    if best_row:
        lines.append(f"**Best performing config:** step={best_row['step_px']:.3f} ({best_row['step_pips']:.1f} pips), gap={best_row['close_gap']}, net=${best_row['realized_net_usd']:.2f}")
        lines.append("")

    # Compare baseline (0.005) vs wider steps
    baseline_rows = [r for r in rows if r["step_px"] == 0.005]
    wider_rows = [r for r in rows if r["step_px"] > 0.005]

    if baseline_rows and wider_rows:
        baseline_best = max(baseline_rows, key=lambda r: float(r["realized_net_usd"] or 0))
        wider_best = max(wider_rows, key=lambda r: float(r["realized_net_usd"] or 0))

        baseline_pnl = float(baseline_best["realized_net_usd"] or 0)
        wider_pnl = float(wider_best["realized_net_usd"] or 0)
        pnl_improvement = wider_pnl - baseline_pnl
        baseline_resets = int(baseline_best["anchor_resets"] or 0)
        wider_resets = int(wider_best["anchor_resets"] or 0)
        reset_reduction = ((baseline_resets - wider_resets) / baseline_resets * 100) if baseline_resets > 0 else 0

        lines.append(f"### Baseline vs Wider Steps")
        lines.append("")
        lines.append(f"| Metric | Baseline (0.005) | Best Wider ({wider_best['step_px']:.3f}) | Delta |")
        lines.append(f"| --- | ---: | ---: | ---: |")
        lines.append(f"| Net PnL | ${baseline_pnl:.2f} | ${wider_pnl:.2f} | **${pnl_improvement:+.2f}** |")
        lines.append(f"| Anchor Resets | {baseline_resets} | {wider_resets} | **- {reset_reduction:.0f}%** |")
        lines.append(f"| PnL/Reset | ${float(baseline_best['pnl_per_anchor_reset']):.4f} | ${float(wider_best['pnl_per_anchor_reset']):.4f} | |")
        lines.append("")

        if pnl_improvement > 0:
            lines.append(f"**Wider steps OUTPERFORM baseline by ${pnl_improvement:+.2f} with {reset_reduction:.0f}% fewer anchor resets.** ✅")
        else:
            lines.append(f"Wider steps underperform baseline by ${pnl_improvement:.2f}. The 0.005 step may be near optimal, or wider steps need different close geometry.")
        lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append("- **Anchor resets** happen when price breaches the outermost lattice level — each reset burns spread without closing")
    lines.append("- **PnL/Reset** measures edge capture efficiency per reset event")
    lines.append("- **Resets/Day** shows churn rate — high values mean the lattice is constantly re-centering")
    lines.append("- The goal is to find the step size that maximizes net PnL while minimizing wasteful resets")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep USDJPY step geometry using tick-native replay.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--csv-out", default=str(REPORT_CSV))
    parser.add_argument("--md-out", default=str(REPORT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 70)
    print("USDJPY STEP GEOMETRY SWEEP")
    print("=" * 70)
    print(f"Days: {args.days}")
    print(f"Step sizes: {STEP_SIZES}")
    print(f"Close gaps: {CLOSE_GAPS}")
    print(f"Total configs: {len(STEP_SIZES) * len(CLOSE_GAPS)}")
    print()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=max(1, int(args.days)))
        chunk = timedelta(hours=max(1, int(args.chunk_hours)))

        print(f"Replay window: {start_utc.isoformat()} → {end_utc.isoformat()}")
        print(f"Chunk size: {chunk}")
        print()

        rows = []
        total = len(STEP_SIZES) * len(CLOSE_GAPS)
        idx = 0

        for step_px in STEP_SIZES:
            cfg = make_bounded_cfg(step_px)
            for close_gap in CLOSE_GAPS:
                idx += 1
                print(f"[{idx}/{total}] step={step_px:.3f} ({cfg.step_pips:.1f} pips), gap={close_gap}...")

                result = replay_step_config(
                    symbol="USDJPY",
                    cfg=cfg,
                    step_px=step_px,
                    close_gap=close_gap,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    chunk=chunk,
                )

                rows.append(result)
                print(
                    f"  → Net: ${result['realized_net_usd']:.2f} | "
                    f"Closes: {result['realized_closes']} | "
                    f"Resets: {result['anchor_resets']} | "
                    f"PnL/Reset: ${result['pnl_per_anchor_reset']:.4f}"
                )

        # Write outputs
        write_csv(rows, Path(args.csv_out))
        write_md(rows, Path(args.md_out))

        print()
        print("=" * 70)
        print("RESULTS SUMMARY")
        print("=" * 70)

        # Top 5 by net PnL
        sorted_rows = sorted(rows, key=lambda r: float(r["realized_net_usd"] or 0), reverse=True)
        print("\nTop 5 configs by net PnL:")
        for i, row in enumerate(sorted_rows[:5], 1):
            print(
                f"  {i}. step={row['step_px']:.3f} gap={row['close_gap']} | "
                f"Net: ${row['realized_net_usd']:.2f} | "
                f"Closes: {row['realized_closes']} | "
                f"Resets: {row['anchor_resets']} | "
                f"PnL/Reset: ${row['pnl_per_anchor_reset']:.4f}"
            )

        print(f"\nWrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")

        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
