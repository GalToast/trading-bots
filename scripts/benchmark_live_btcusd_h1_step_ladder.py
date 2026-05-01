#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_live_btcusd_h1_cap_ladder import (
    LiveShape,
    load_broker_context,
    load_live_shape,
    marked_floating_net,
)
from tick_penetration_lattice_core import engine_from_args, load_ticks_range


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CSV = REPORTS / "live_btcusd_h1_step_ladder.csv"
DEFAULT_MD = REPORTS / "live_btcusd_h1_step_ladder.md"
DEFAULT_STEPS = "30,45,50,60,75,90"


def parse_steps(raw: str) -> list[float]:
    values: list[float] = []
    seen: set[float] = set()
    for part in str(raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        value = round(float(text), 6)
        if value <= 0.0:
            raise ValueError(f"step must be positive, got {value}")
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    if not values:
        raise ValueError("at least one step is required")
    return values


def make_engine(shape: LiveShape, step: float):
    return engine_from_args(
        symbol=shape.symbol,
        timeframe_name=shape.timeframe_name,
        step=step,
        max_open_per_side=shape.max_open_per_side,
        variant_name=shape.variant_name,
        close_alpha=shape.close_alpha,
        momentum_gate=shape.momentum_gate,
        cooldown_bars=shape.cooldown_bars,
        sell_gap=shape.sell_gap,
        buy_gap=shape.buy_gap,
    )


def replay_step(shape: LiveShape, *, step: float, start_utc: datetime, end_utc: datetime, chunk_hours: int) -> dict[str, object]:
    engine = make_engine(shape, step=step)
    cursor = start_utc
    chunk = timedelta(hours=max(1, int(chunk_hours)))
    total_ticks = 0
    last_tick: dict[str, float] | None = None
    while cursor < end_utc:
        chunk_end = min(end_utc, cursor + chunk)
        ticks = load_ticks_range(shape.symbol, cursor, chunk_end)
        if ticks:
            last_tick = ticks[-1]
            total_ticks += engine.process_ticks(ticks, action_sink=None, event_path=None, emit=False)
        cursor = chunk_end
    realized = float(engine.state.realized_net_usd or 0.0)
    floating = marked_floating_net(engine, shape.symbol, last_tick)
    closes = int(engine.state.realized_closes or 0)
    days = max((end_utc - start_utc).total_seconds() / 86400.0, 1e-9)
    marked_net = realized + floating
    buy_count = sum(1 for ticket in engine.state.open_tickets or [] if str(ticket.get("direction", "")).upper() == "BUY")
    sell_count = sum(1 for ticket in engine.state.open_tickets or [] if str(ticket.get("direction", "")).upper() == "SELL")
    return {
        "step": round(step, 6),
        "ticks_processed": int(total_ticks),
        "realized_net_usd": round(realized, 3),
        "marked_floating_usd": round(floating, 3),
        "marked_net_usd": round(marked_net, 3),
        "realized_closes": closes,
        "rearm_opens": int(engine.state.rearm_opens or 0),
        "open_count": len(engine.state.open_tickets or []),
        "buy_open_count": buy_count,
        "sell_open_count": sell_count,
        "max_open_total": int(engine.state.max_open_total or 0),
        "net_per_close_usd": round(marked_net / closes, 3) if closes > 0 else 0.0,
        "closes_per_day": round(closes / days, 3),
        "rearms_per_close": round(float(engine.state.rearm_opens or 0) / closes, 3) if closes > 0 else 0.0,
        "next_buy_level": round(float(engine.state.next_buy_level or 0.0), 6),
        "next_sell_level": round(float(engine.state.next_sell_level or 0.0), 6),
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def rank_step_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rows,
        key=lambda row: (
            float(row["marked_net_usd"]),
            float(row["realized_net_usd"]),
            -int(row["open_count"]),
            -float(row["step"]),
        ),
        reverse=True,
    )


def write_md(
    path: Path,
    rows: list[dict[str, object]],
    *,
    shape: LiveShape,
    start_utc: datetime,
    end_utc: datetime,
    broker_context: dict[str, float | int | str] | None,
) -> None:
    ranked = rank_step_rows(rows)
    baseline = next((row for row in rows if abs(float(row["step"]) - float(shape.step)) < 1e-9), rows[0])
    best = ranked[0]
    lines = [
        "# Live BTCUSD H1 Step Ladder",
        "",
        f"- Window: `{start_utc.isoformat()}` to `{end_utc.isoformat()}`",
        f"- Engine: `TickStatefulRearmEngine` replaying the current live BTC H1 shape from `penetration_lattice_shadow_btcusd_exc2_tight_state.json`",
        f"- Shape: `step={shape.step}` live baseline, `max_open_per_side={shape.max_open_per_side}`, `{shape.variant_name}`, `gap={shape.sell_gap}/{shape.buy_gap}`, `momentum_gate={str(shape.momentum_gate).lower()}`",
        "- This is a replay-only step comparison under the current effective H1 gate behavior. It is benchmark guidance, not broker truth by itself.",
    ]
    if broker_context is not None:
        lines.append(
            f"- Current broker context: realized `{broker_context['realized_usd']:+.2f}`, floating `{broker_context['floating_usd']:+.2f}`, net `{broker_context['net_usd']:+.2f}`, closes `{broker_context['closed_positions']}`, open `{broker_context['open_positions']}` as of `{broker_context['updated_at']}`"
        )
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Step | Marked Net | Realized | Floating | Closes | Rearm | Open | BUY | SELL | Net/Close |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in ranked:
        label = f"{row['step']}"
        if abs(float(row["step"]) - float(shape.step)) < 1e-9:
            label += " (live)"
        lines.append(
            f"| `{label}` | {row['marked_net_usd']} | {row['realized_net_usd']} | {row['marked_floating_usd']} | "
            f"{row['realized_closes']} | {row['rearm_opens']} | {row['open_count']} | {row['buy_open_count']} | {row['sell_open_count']} | {row['net_per_close_usd']} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            f"- Best tested step by marked net: `step={best['step']}` at `{best['marked_net_usd']}` marked net.",
            f"- Current live step baseline: `step={baseline['step']}` at `{baseline['marked_net_usd']}` marked net.",
            f"- Best-vs-live marked-net delta: `{float(best['marked_net_usd']) - float(baseline['marked_net_usd']):+.3f}`",
            f"- Best-vs-live realized delta: `{float(best['realized_net_usd']) - float(baseline['realized_net_usd']):+.3f}`",
            f"- Best-vs-live floating delta: `{float(best['marked_floating_usd']) - float(baseline['marked_floating_usd']):+.3f}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the live BTCUSD H1 lane across a step-size ladder.")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--steps", default=DEFAULT_STEPS)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--csv-out", default=str(DEFAULT_CSV))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    steps = parse_steps(args.steps)
    shape = load_live_shape()
    broker_context = load_broker_context()
    if round(shape.step, 6) not in steps:
        steps.append(round(shape.step, 6))
        steps = sorted(steps)
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=max(1, int(args.days)))
        rows: list[dict[str, object]] = []
        total = len(steps)
        for idx, step in enumerate(steps, start=1):
            row = replay_step(shape, step=step, start_utc=start_utc, end_utc=end_utc, chunk_hours=int(args.chunk_hours))
            rows.append(row)
            if args.progress:
                print(
                    f"[{idx}/{total}] step={step} marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
                    f"floating={row['marked_floating_usd']} closes={row['realized_closes']} rearm={row['rearm_opens']}",
                    flush=True,
                )
        rows = sorted(rows, key=lambda row: float(row["step"]))
        write_csv(Path(args.csv_out), rows)
        write_md(Path(args.md_out), rows, shape=shape, start_utc=start_utc, end_utc=end_utc, broker_context=broker_context)
        for row in rank_step_rows(rows):
            print(
                f"step={row['step']}: marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
                f"floating={row['marked_floating_usd']} closes={row['realized_closes']} rearm={row['rearm_opens']} "
                f"open={row['open_count']}"
            )
        print(f"Wrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
