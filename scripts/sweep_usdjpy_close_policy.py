#!/usr/bin/env python3
"""
USDJPY Close Policy × Step Width Sweep
========================================
Tests whether better close policies can push wider-step USDJPY over break-even.

Hypothesis: The edge IS real (100% WR, +$0.066 avg raw), but spread costs dominate.
A close policy that captures MORE per close (alpha, min_pnl filter) + wider steps
should flip the edge positive.

Policies tested:
- baseline: close on first penetration back to any level (gap=2)
- same_bar_min_pnl: only close when penetration captures >= threshold (0.01, 0.03, 0.05, 0.10)
- shallow_level_cap: restrict same-bar closes to shallow levels only (1, 2, 3)

Steps tested: 0.040 (sweet spot from previous sweep), 0.020 (mid-range), 0.010 (narrow)

Usage:
  python scripts/sweep_usdjpy_close_policy.py --days 3
  python scripts/sweep_usdjpy_close_policy.py --days 1 --chunk-hours 12

Output:
  reports/usdjpy_close_policy_sweep.csv
  reports/usdjpy_close_policy_sweep.md
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
REPORT_CSV = ROOT / "reports" / "usdjpy_close_policy_sweep.csv"
REPORT_MD = ROOT / "reports" / "usdjpy_close_policy_sweep.md"

# Step sizes from previous sweep (focus on promising range)
STEP_SIZES = [0.010, 0.020, 0.040]

# Same-bar min PnL thresholds to sweep
SAME_BAR_MIN_PNL = [0.0, 0.01, 0.03, 0.05, 0.07, 0.10]

# Shallow level caps
SHALLOW_LEVEL_CAPS = [0, 1, 2, 3]

# Base step for pips conversion (USDJPY pip = 0.001)
PIP_SIZE = 0.001


@dataclass(frozen=True)
class SweepConfig:
    step_px: float
    same_bar_min_pnl: float
    shallow_level_cap: int


def make_bounded_cfg(step_px: float) -> BoundedConfig:
    """Create bounded config matching current live USDJPY settings but with different step."""
    return BoundedConfig(
        step_pips=step_px / PIP_SIZE,
        max_open_per_side=20,
        max_floating_loss_usd=-10.0,
        vwap_lookback=20,
        regime_lookback_bars=60,
        max_range_pips=24.0,
        breakout_buffer_pips=5.0,
        max_lattice_window_bars=240,
        cooldown_bars=60,
    )


def replay_config(
    symbol: str,
    cfg: BoundedConfig,
    *,
    step_px: float,
    close_gap: int,
    same_bar_min_pnl: float,
    shallow_level_cap: int,
    start_utc: datetime,
    end_utc: datetime,
    chunk: timedelta,
    variant: str = "rearm_lvl2_exc2",
) -> dict[str, object]:
    """Replay a single config over tick data."""
    engine = bounded_engine_from_args(
        symbol=symbol,
        timeframe_name="M1",
        cfg=cfg,
        variant_name=variant,
        close_gap=close_gap,
        same_bar_min_pnl=same_bar_min_pnl,
        same_bar_shallow_level_cap=shallow_level_cap,
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
        "same_bar_min_pnl": same_bar_min_pnl,
        "shallow_level_cap": shallow_level_cap,
        "ticks_processed": total_ticks,
        "realized_net_usd": round(realized_net, 3),
        "realized_closes": realized_closes,
        "avg_pnl_per_close": round(avg_pnl_per_close, 4),
        "anchor_resets": anchor_resets,
        "resets_per_day": round(resets_per_day, 2),
        "pnl_per_anchor_reset": round(pnl_per_anchor_reset, 4),
        "rearm_opens": rearm_opens,
        "max_open_total": max_open_total,
        "days_simulated": round((end_utc - start_utc).total_seconds() / 86400, 2),
    }


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "step_px", "step_pips", "close_gap", "same_bar_min_pnl", "shallow_level_cap",
        "ticks_processed", "realized_net_usd", "realized_closes", "avg_pnl_per_close",
        "anchor_resets", "resets_per_day", "pnl_per_anchor_reset",
        "rearm_opens", "max_open_total", "days_simulated",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_md(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Sort by realized_net_usd descending
    sorted_rows = sorted(rows, key=lambda r: float(r["realized_net_usd"] or 0), reverse=True)

    lines = [
        "# USDJPY Close Policy × Step Width Sweep",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Script:** `scripts/sweep_usdjpy_close_policy.py`",
        "",
        "**Hypothesis:** The edge IS real (100% WR, +$0.066 avg raw), but spread costs dominate.",
        "A close policy that captures MORE per close + wider steps should flip the edge positive.",
        "",
        "## Top 10 Configs (by net PnL)",
        "",
        "| Step | Pips | Min PnL | Shallow Cap | Net PnL | Closes | Avg/Close | Resets | PnL/Reset |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in sorted_rows[:10]:
        min_pnl_label = f"${row['same_bar_min_pnl']:.2f}" if row['same_bar_min_pnl'] > 0 else "none"
        shallow_label = str(row['shallow_level_cap']) if row['shallow_level_cap'] > 0 else "unlimited"
        lines.append(
            f"| {row['step_px']:.3f} | {row['step_pips']:.1f} | {min_pnl_label} | {shallow_label} | "
            f"**${row['realized_net_usd']:.2f}** | {row['realized_closes']} | "
            f"${row['avg_pnl_per_close']:.4f} | {row['anchor_resets']} | "
            f"${row['pnl_per_anchor_reset']:.4f} |"
        )

    # Break-even analysis
    lines.append("")
    lines.append("## Break-Even Analysis")
    lines.append("")

    positive_rows = [r for r in rows if float(r["realized_net_usd"] or 0) > 0]
    near_breakeven = [r for r in rows if -5.0 < float(r["realized_net_usd"] or 0) <= 0]

    if positive_rows:
        lines.append(f"**🎉 {len(positive_rows)} config(s) are PROFITABLE!**")
        lines.append("")
        for row in sorted(positive_rows, key=lambda r: float(r["realized_net_usd"]), reverse=True)[:5]:
            min_pnl_label = f"${row['same_bar_min_pnl']:.2f}" if row['same_bar_min_pnl'] > 0 else "none"
            shallow_label = str(row['shallow_level_cap']) if row['shallow_level_cap'] > 0 else "unlimited"
            lines.append(
                f"- step={row['step_px']:.3f}, min_pnl={min_pnl_label}, shallow_cap={shallow_label}: "
                f"**${row['realized_net_usd']:.2f}** ({row['realized_closes']} closes, {row['anchor_resets']} resets)"
            )
    elif near_breakeven:
        lines.append(f"No profitable configs, but {len(near_breakeven)} are within $5 of break-even:")
        lines.append("")
        for row in sorted(near_breakeven, key=lambda r: float(r["realized_net_usd"] or 0), reverse=True)[:5]:
            min_pnl_label = f"${row['same_bar_min_pnl']:.2f}" if row['same_bar_min_pnl'] > 0 else "none"
            shallow_label = str(row['shallow_level_cap']) if row['shallow_level_cap'] > 0 else "unlimited"
            lines.append(
                f"- step={row['step_px']:.3f}, min_pnl={min_pnl_label}, shallow_cap={shallow_label}: "
                f"${row['realized_net_usd']:.2f} ({row['realized_closes']} closes)"
            )
    else:
        best = sorted_rows[0] if sorted_rows else None
        if best:
            lines.append(f"No profitable configs found. Best: step={best['step_px']:.3f}, net=${best['realized_net_usd']:.2f}")
            lines.append("")
            lines.append("**Interpretation:** Even with close policy optimization, USDJPY spread costs exceed edge capture.")
            lines.append("This suggests USDJPY may need a fundamentally different approach (regime filter, spread-adaptive sizing, etc.)")

    # By-step analysis
    lines.append("")
    lines.append("## Results by Step Width")
    lines.append("")

    for step_px in sorted(set(r["step_px"] for r in rows)):
        step_rows = [r for r in rows if r["step_px"] == step_px]
        best_step = max(step_rows, key=lambda r: float(r["realized_net_usd"] or 0))
        worst_step = min(step_rows, key=lambda r: float(r["realized_net_usd"] or 0))
        avg_step = sum(float(r["realized_net_usd"] or 0) for r in step_rows) / len(step_rows)

        lines.append(f"### Step {step_px:.3f} ({step_rows[0]['step_pips']:.1f} pips)")
        lines.append(f"- Best: ${best_step['realized_net_usd']:.2f} (min_pnl=${best_step['same_bar_min_pnl']:.2f}, shallow_cap={best_step['shallow_level_cap']})")
        lines.append(f"- Worst: ${worst_step['realized_net_usd']:.2f} (min_pnl=${worst_step['same_bar_min_pnl']:.2f}, shallow_cap={worst_step['shallow_level_cap']})")
        lines.append(f"- Average: ${avg_step:.2f} across {len(step_rows)} configs")
        lines.append("")

    lines.append("## Key Insights")
    lines.append("")
    lines.append("- **same_bar_min_pnl** filters out sub-spread closes — if this helps, it means the edge is in larger penetrations, not micro-fluctuations")
    lines.append("- **shallow_level_cap** restricts same-bar closes to shallow levels — if this helps, it means deep-level same-bar closes are the bleed")
    lines.append("- If neither policy flips the edge positive, USDJPY needs a fundamentally different approach")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep USDJPY close policies with tick-native replay.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--close-gap", type=int, default=2)
    parser.add_argument("--csv-out", default=str(REPORT_CSV))
    parser.add_argument("--md-out", default=str(REPORT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 70)
    print("USDJPY CLOSE POLICY × STEP WIDTH SWEEP")
    print("=" * 70)
    print(f"Days: {args.days}")
    print(f"Steps: {STEP_SIZES}")
    print(f"Same-bar min PnL: {SAME_BAR_MIN_PNL}")
    print(f"Shallow level caps: {SHALLOW_LEVEL_CAPS}")
    print(f"Close gap: {args.close_gap}")
    total = len(STEP_SIZES) * len(SAME_BAR_MIN_PNL) * len(SHALLOW_LEVEL_CAPS)
    print(f"Total configs: {total}")
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
        idx = 0

        for step_px in STEP_SIZES:
            cfg = make_bounded_cfg(step_px)
            for min_pnl in SAME_BAR_MIN_PNL:
                for shallow_cap in SHALLOW_LEVEL_CAPS:
                    idx += 1
                    print(
                        f"[{idx}/{total}] step={step_px:.3f} ({cfg.step_pips:.1f} pips), "
                        f"min_pnl=${min_pnl:.2f}, shallow_cap={shallow_cap}..."
                    )

                    result = replay_config(
                        symbol="USDJPY",
                        cfg=cfg,
                        step_px=step_px,
                        close_gap=args.close_gap,
                        same_bar_min_pnl=min_pnl,
                        shallow_level_cap=shallow_cap,
                        start_utc=start_utc,
                        end_utc=end_utc,
                        chunk=chunk,
                    )

                    rows.append(result)
                    print(
                        f"  → Net: ${result['realized_net_usd']:.2f} | "
                        f"Closes: {result['realized_closes']} | "
                        f"Resets: {result['anchor_resets']} | "
                        f"PnL/Close: ${result['avg_pnl_per_close']:.4f}"
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
                f"  {i}. step={row['step_px']:.3f} min_pnl=${row['same_bar_min_pnl']:.2f} shallow={row['shallow_level_cap']} | "
                f"Net: ${row['realized_net_usd']:.2f} | "
                f"Closes: {row['realized_closes']} | "
                f"Resets: {row['anchor_resets']}"
            )

        # Check for profitable configs
        positive = [r for r in rows if float(r["realized_net_usd"] or 0) > 0]
        if positive:
            print(f"\n🎉 {len(positive)} config(s) are PROFITABLE!")
        else:
            best = sorted_rows[0]
            print(f"\nNo profitable configs. Best: ${best['realized_net_usd']:.2f}")

        print(f"\nWrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")

        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
