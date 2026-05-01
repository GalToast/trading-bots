#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from tick_penetration_lattice_core import TickStatefulRearmEngine, engine_from_args, load_ticks_range, tick_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "reports" / "tick_native_btc_warp_timeframes.csv"
DEFAULT_MD = ROOT / "reports" / "tick_native_btc_warp_timeframes.md"


@dataclass(frozen=True)
class WarpSpec:
    label: str
    timeframe: str
    step: float
    max_open_per_side: int
    momentum_gate: bool = False
    close_alpha: float = 1.0
    sell_gap: int = 1
    buy_gap: int = 1
    variant_name: str = "rearm_lvl2_exc1"


def build_specs() -> list[WarpSpec]:
    return [
        WarpSpec("m1_step25_mo12", "M1", 25.0, 12, False),
        WarpSpec("m1_step25_mo30", "M1", 25.0, 30, False),
        WarpSpec("m1_step50_mo12", "M1", 50.0, 12, False),
        WarpSpec("m1_step50_mo30", "M1", 50.0, 30, False),
        WarpSpec("m1_step50_mo60", "M1", 50.0, 60, False),
        WarpSpec("m1_step75_mo12", "M1", 75.0, 12, False),
        WarpSpec("m1_step75_mo30", "M1", 75.0, 30, False),
        WarpSpec("m1_step100_mo12", "M1", 100.0, 12, False),
        WarpSpec("m1_step100_mo30", "M1", 100.0, 30, False),
        WarpSpec("m1_step100_mo60", "M1", 100.0, 60, False),
        WarpSpec("m1_step125_mo12", "M1", 125.0, 12, False),
        WarpSpec("m1_step125_mo30", "M1", 125.0, 30, False),
        WarpSpec("m1_step150_mo12", "M1", 150.0, 12, False),
        WarpSpec("m1_step150_mo30", "M1", 150.0, 30, False),
        WarpSpec("m1_step200_mo12", "M1", 200.0, 12, False),
        WarpSpec("m1_step200_mo30", "M1", 200.0, 30, False),
        WarpSpec("m1_step50_mo30_mom", "M1", 50.0, 30, True),
        WarpSpec("m1_step75_mo30_mom", "M1", 75.0, 30, True),
        WarpSpec("m1_step100_mo30_mom", "M1", 100.0, 30, True),
        WarpSpec("m1_step150_mo30_mom", "M1", 150.0, 30, True),
        WarpSpec("m5_current_warp", "M5", 100.0, 60, False),
        WarpSpec("m5_probation_live_shape", "M5", 100.0, 12, False),
        WarpSpec("m15_warp", "M15", 15.0, 80, False),
        WarpSpec("m15_warp_on20", "M15", 20.0, 60, True),
    ]


def marked_floating_net(symbol: str, engine: TickStatefulRearmEngine, last_tick: dict[str, float] | None) -> float:
    if last_tick is None:
        return 0.0
    bid = float(last_tick["bid"])
    ask = float(last_tick["ask"])
    floating = 0.0
    for ticket in engine.state.open_tickets or []:
        direction = str(ticket.get("direction", "") or "").upper()
        fill_price = float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("trigger_level", 0.0))) or 0.0)
        if direction == "BUY":
            floating += tick_pnl_usd(symbol, direction, fill_price, bid)
        elif direction == "SELL":
            floating += tick_pnl_usd(symbol, direction, fill_price, ask)
    return float(floating)


def replay_spec(spec: WarpSpec, *, symbol: str, start_utc: datetime, end_utc: datetime, chunk_hours: int) -> dict[str, object]:
    engine = engine_from_args(
        symbol=symbol,
        timeframe_name=spec.timeframe,
        step=float(spec.step),
        max_open_per_side=int(spec.max_open_per_side),
        variant_name=spec.variant_name,
        close_alpha=float(spec.close_alpha),
        momentum_gate=bool(spec.momentum_gate),
        cooldown_bars=0,
        sell_gap=int(spec.sell_gap),
        buy_gap=int(spec.buy_gap),
    )
    cursor = start_utc
    chunk = timedelta(hours=max(1, int(chunk_hours)))
    total_ticks = 0
    last_tick: dict[str, float] | None = None
    while cursor < end_utc:
        chunk_end = min(end_utc, cursor + chunk)
        ticks = load_ticks_range(symbol, cursor, chunk_end)
        if ticks:
            last_tick = ticks[-1]
            total_ticks += engine.process_ticks(ticks, action_sink=None, event_path=None, emit=False)
        cursor = chunk_end
    realized = float(engine.state.realized_net_usd or 0.0)
    floating = marked_floating_net(symbol, engine, last_tick)
    closes = int(engine.state.realized_closes or 0)
    days = max((end_utc - start_utc).total_seconds() / 86400.0, 1e-9)
    marked_net = realized + floating
    return {
        "label": spec.label,
        "timeframe": spec.timeframe,
        "step": round(float(spec.step), 3),
        "max_open_per_side": int(spec.max_open_per_side),
        "momentum_gate": bool(spec.momentum_gate),
        "days": round(days, 3),
        "ticks_processed": int(total_ticks),
        "realized_net_usd": round(realized, 3),
        "marked_floating_usd": round(floating, 3),
        "marked_net_usd": round(marked_net, 3),
        "realized_closes": closes,
        "rearm_opens": int(engine.state.rearm_opens or 0),
        "open_count": len(engine.state.open_tickets or []),
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


def write_md(path: Path, rows: list[dict[str, object]], *, symbol: str, start_utc: datetime, end_utc: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Tick-Native {symbol} Warp Timeframe Benchmark",
        "",
        f"- Window: `{start_utc.isoformat()}` to `{end_utc.isoformat()}`",
        "- Engine: `TickStatefulRearmEngine`",
        "- Variant: `rearm_lvl2_exc1`, `close_alpha=1.0`, `gap=1/1`",
        "- Marked net = realized plus floating marked to final executable bid/ask in the replay window",
        "",
        "## Ranked By Marked Net",
        "",
        "| Label | TF | Step | Max Open | Mom | Marked Net | Realized | Floating | Closes | Close/Day | Net/Close | Open | Rearm |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['label']}` | `{row['timeframe']}` | {row['step']} | {row['max_open_per_side']} | "
            f"`{str(row['momentum_gate']).lower()}` | {row['marked_net_usd']} | {row['realized_net_usd']} | "
            f"{row['marked_floating_usd']} | {row['realized_closes']} | {row['closes_per_day']} | "
            f"{row['net_per_close_usd']} | {row['open_count']} | {row['rearm_opens']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare BTC warp configs across M1/M5/M15 on the tick-native engine.")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--labels", nargs="*", default=None)
    parser.add_argument("--csv-out", default=str(DEFAULT_CSV))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=max(1, int(args.days)))
        rows: list[dict[str, object]] = []
        specs = build_specs()
        if args.labels:
            wanted = {str(label) for label in args.labels}
            specs = [spec for spec in specs if spec.label in wanted]
            if not specs:
                print("No matching labels")
                return 1
        for idx, spec in enumerate(specs, start=1):
            row = replay_spec(
                spec,
                symbol=str(args.symbol),
                start_utc=start_utc,
                end_utc=end_utc,
                chunk_hours=int(args.chunk_hours),
            )
            rows.append(row)
            if args.progress:
                print(
                    f"[{idx}/{len(specs)}] {row['label']} tf={row['timeframe']} "
                    f"step={row['step']} mo={row['max_open_per_side']} "
                    f"marked={row['marked_net_usd']} closes={row['realized_closes']} open={row['open_count']}",
                    flush=True,
                )
        rows.sort(key=lambda row: float(row["marked_net_usd"]), reverse=True)
        write_csv(Path(args.csv_out), rows)
        write_md(Path(args.md_out), rows, symbol=str(args.symbol), start_utc=start_utc, end_utc=end_utc)
        top = rows[0]
        print(
            f"Best {top['label']} | tf={top['timeframe']} step={top['step']} max_open={top['max_open_per_side']} "
            f"mom={top['momentum_gate']} marked_net={top['marked_net_usd']}"
        )
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
