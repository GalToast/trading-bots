#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_live_btcusd_h1_step_ladder import load_live_shape, parse_steps, replay_step


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CSV = REPORTS / "live_btcusd_h1_step_robustness.csv"
DEFAULT_MD = REPORTS / "live_btcusd_h1_step_robustness.md"
DEFAULT_STEPS = "30,45,50,75"
DEFAULT_WINDOWS = "3,5,7"


def parse_windows(raw: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for part in str(raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        value = int(text)
        if value <= 0:
            raise ValueError(f"window must be positive, got {value}")
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    if not values:
        raise ValueError("at least one window is required")
    return values


def rank_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rows,
        key=lambda row: (
            int(row["days"]),
            -float(row["marked_net_usd"]),
            -float(row["realized_net_usd"]),
            int(row["open_count"]),
            float(row["step"]),
        ),
    )


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, rows: list[dict[str, object]], *, live_step: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_days: dict[int, list[dict[str, object]]] = defaultdict(list)
    totals: dict[float, dict[str, float]] = defaultdict(lambda: {"wins": 0.0, "marked_net_sum": 0.0, "realized_sum": 0.0})
    for row in rows:
        days = int(row["days"])
        step = float(row["step"])
        by_days[days].append(row)
        totals[step]["marked_net_sum"] += float(row["marked_net_usd"])
        totals[step]["realized_sum"] += float(row["realized_net_usd"])
    for days, day_rows in by_days.items():
        winner = max(day_rows, key=lambda row: (float(row["marked_net_usd"]), float(row["realized_net_usd"])))
        totals[float(winner["step"])]["wins"] += 1.0

    consensus = sorted(
        (
            {
                "step": step,
                "wins": int(metrics["wins"]),
                "avg_marked_net_usd": round(metrics["marked_net_sum"] / max(len(by_days), 1), 3),
                "avg_realized_net_usd": round(metrics["realized_sum"] / max(len(by_days), 1), 3),
            }
            for step, metrics in totals.items()
        ),
        key=lambda row: (row["wins"], row["avg_marked_net_usd"], row["avg_realized_net_usd"], -row["step"]),
        reverse=True,
    )

    lines = [
        "# Live BTCUSD H1 Step Robustness",
        "",
        "- Engine: `TickStatefulRearmEngine` replaying the current live BTC H1 shape over multiple recent windows",
        f"- Tested steps: `{', '.join(str(int(step)) if float(step).is_integer() else str(step) for step in sorted(totals))}`",
        f"- Live step baseline: `{live_step}`",
        "",
        "## Window Winners",
        "",
        "| Days | Best Step | Marked Net | Realized | Floating | Closes |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for days in sorted(by_days):
        winner = max(by_days[days], key=lambda row: (float(row["marked_net_usd"]), float(row["realized_net_usd"])))
        lines.append(
            f"| `{days}` | `{winner['step']}` | {winner['marked_net_usd']} | {winner['realized_net_usd']} | {winner['marked_floating_usd']} | {winner['realized_closes']} |"
        )

    lines.extend(
        [
            "",
            "## Step Consensus",
            "",
            "| Step | Window Wins | Avg Marked Net | Avg Realized |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for row in consensus:
        label = f"{row['step']}"
        if abs(float(row["step"]) - float(live_step)) < 1e-9:
            label += " (live)"
        lines.append(
            f"| `{label}` | {row['wins']} | {row['avg_marked_net_usd']} | {row['avg_realized_net_usd']} |"
        )

    lines.extend(["", "## Full Matrix", ""])
    for days in sorted(by_days):
        lines.extend(
            [
                f"### `{days}`-Day Window",
                "",
                "| Step | Marked Net | Realized | Floating | Closes | Open |",
                "| ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in sorted(by_days[days], key=lambda entry: float(entry["step"])):
            label = f"{row['step']}"
            if abs(float(row["step"]) - float(live_step)) < 1e-9:
                label += " (live)"
            lines.append(
                f"| `{label}` | {row['marked_net_usd']} | {row['realized_net_usd']} | {row['marked_floating_usd']} | {row['realized_closes']} | {row['open_count']} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark BTCUSD H1 step robustness across multiple recent windows.")
    parser.add_argument("--steps", default=DEFAULT_STEPS)
    parser.add_argument("--windows", default=DEFAULT_WINDOWS)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--csv-out", default=str(DEFAULT_CSV))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    steps = parse_steps(args.steps)
    windows = parse_windows(args.windows)
    shape = load_live_shape()
    if round(shape.step, 6) not in steps:
        steps.append(round(shape.step, 6))
        steps = sorted(steps)
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        end_utc = datetime.now(timezone.utc)
        rows: list[dict[str, object]] = []
        total = len(steps) * len(windows)
        done = 0
        for days in windows:
            start_utc = end_utc - timedelta(days=days)
            for step in steps:
                row = replay_step(shape, step=step, start_utc=start_utc, end_utc=end_utc, chunk_hours=int(args.chunk_hours))
                row["days"] = int(days)
                rows.append(row)
                done += 1
                if args.progress:
                    print(
                        f"[{done}/{total}] days={days} step={step} marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
                        f"floating={row['marked_floating_usd']} closes={row['realized_closes']}",
                        flush=True,
                    )
        rows = rank_rows(rows)
        write_csv(Path(args.csv_out), rows)
        write_md(Path(args.md_out), rows, live_step=float(shape.step))
        print(f"Wrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
