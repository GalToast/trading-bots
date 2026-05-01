#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from tick_penetration_lattice_core import TickStatefulRearmEngine, engine_from_args, load_ticks_range, tick_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
STATE_PATH = REPORTS / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"
SCOREBOARD_PATH = REPORTS / "penetration_lattice_lane_scoreboard.csv"
DEFAULT_CSV = REPORTS / "live_btcusd_h1_cap_ladder.csv"
DEFAULT_MD = REPORTS / "live_btcusd_h1_cap_ladder.md"
LANE_ID = "live_btcusd_exc2_tight_941779"
DEFAULT_CAPS = "12,16,20,24,30,40,50,60"


@dataclass(frozen=True)
class LiveShape:
    symbol: str
    timeframe_name: str
    step: float
    max_open_per_side: int
    variant_name: str
    close_alpha: float
    momentum_gate: bool
    cooldown_bars: int
    sell_gap: int
    buy_gap: int


def parse_caps(raw: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for part in str(raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        value = int(text)
        if value <= 0:
            raise ValueError(f"cap must be positive, got {value}")
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    if not values:
        raise ValueError("at least one cap is required")
    return values


def load_live_shape(path: Path = STATE_PATH) -> LiveShape:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata = dict(payload.get("metadata") or {})
    symbol = ""
    symbols = metadata.get("symbols")
    if isinstance(symbols, list) and symbols:
        symbol = str(symbols[0] or "").upper()
    if not symbol:
        symbol = "BTCUSD"
    return LiveShape(
        symbol=symbol,
        timeframe_name=str(metadata.get("timeframe") or "H1").upper(),
        step=float(metadata.get("step") or 45.0),
        max_open_per_side=int(metadata.get("max_open_per_side") or 50),
        variant_name=str(metadata.get("raw_rearm_variant") or "rearm_lvl2_exc2"),
        close_alpha=float(metadata.get("raw_close_alpha") or 1.0),
        momentum_gate=bool(metadata.get("raw_rearm_momentum_gate")),
        cooldown_bars=int(metadata.get("raw_rearm_cooldown_bars") or 0),
        sell_gap=int(metadata.get("raw_sell_gap") or 1),
        buy_gap=int(metadata.get("raw_buy_gap") or 1),
    )


def load_broker_context(path: Path = SCOREBOARD_PATH) -> dict[str, float | int | str] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if str(row.get("lane_id") or "") != LANE_ID:
                continue
            if str(row.get("symbol") or "").upper() != "TOTAL":
                continue
            return {
                "updated_at": str(row.get("updated_at") or ""),
                "realized_usd": float(row.get("realized_usd") or 0.0),
                "floating_usd": float(row.get("floating_usd") or 0.0),
                "net_usd": float(row.get("net_usd") or 0.0),
                "closed_positions": int(float(row.get("closed_positions", row.get("closes", 0)) or 0)),
                "open_positions": int(float(row.get("open_positions", row.get("open_count", 0)) or 0)),
            }
    return None


def make_engine(shape: LiveShape, max_open_per_side: int) -> TickStatefulRearmEngine:
    return engine_from_args(
        symbol=shape.symbol,
        timeframe_name=shape.timeframe_name,
        step=shape.step,
        max_open_per_side=max_open_per_side,
        variant_name=shape.variant_name,
        close_alpha=shape.close_alpha,
        momentum_gate=shape.momentum_gate,
        cooldown_bars=shape.cooldown_bars,
        sell_gap=shape.sell_gap,
        buy_gap=shape.buy_gap,
    )


def marked_floating_net(engine: TickStatefulRearmEngine, symbol: str, last_tick: dict[str, float] | None) -> float:
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


def replay_cap(shape: LiveShape, *, max_open_per_side: int, start_utc: datetime, end_utc: datetime, chunk_hours: int) -> dict[str, object]:
    engine = make_engine(shape, max_open_per_side=max_open_per_side)
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
        "max_open_per_side": int(max_open_per_side),
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


def rank_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rows,
        key=lambda row: (
            float(row["marked_net_usd"]),
            float(row["realized_net_usd"]),
            -int(row["open_count"]),
            -int(row["max_open_per_side"]),
        ),
        reverse=True,
    )


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(
    path: Path,
    rows: list[dict[str, object]],
    *,
    shape: LiveShape,
    start_utc: datetime,
    end_utc: datetime,
    broker_context: dict[str, float | int | str] | None,
) -> None:
    ranked = rank_rows(rows)
    live_cap = int(shape.max_open_per_side)
    baseline = next((row for row in rows if int(row["max_open_per_side"]) == live_cap), rows[0])
    best = ranked[0]
    lines = [
        "# Live BTCUSD H1 Cap Ladder",
        "",
        f"- Window: `{start_utc.isoformat()}` to `{end_utc.isoformat()}`",
        f"- Engine: `TickStatefulRearmEngine` replaying the current live BTC H1 shape from `{STATE_PATH.name}`",
        f"- Shape: `step={shape.step}`, `max_open_per_side={shape.max_open_per_side}` live baseline, `{shape.variant_name}`, `gap={shape.sell_gap}/{shape.buy_gap}`, `momentum_gate={str(shape.momentum_gate).lower()}`",
        "- This is a replay-only cap comparison under the current effective H1 gate behavior. It is benchmark guidance, not broker truth by itself.",
    ]
    if broker_context is not None:
        lines.extend(
            [
                f"- Current broker context: realized `{broker_context['realized_usd']:+.2f}`, floating `{broker_context['floating_usd']:+.2f}`, net `{broker_context['net_usd']:+.2f}`, closes `{broker_context['closed_positions']}`, open `{broker_context['open_positions']}` as of `{broker_context['updated_at']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Max Open/Side | Marked Net | Realized | Floating | Closes | Rearm | Open | BUY | SELL | Net/Close |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in ranked:
        label = f"{row['max_open_per_side']}"
        if int(row["max_open_per_side"]) == live_cap:
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
            f"- Best tested cap by marked net: `max_open_per_side={best['max_open_per_side']}` at `{best['marked_net_usd']}` marked net.",
            f"- Current live cap baseline: `max_open_per_side={baseline['max_open_per_side']}` at `{baseline['marked_net_usd']}` marked net.",
            f"- Best-vs-live marked-net delta: `{float(best['marked_net_usd']) - float(baseline['marked_net_usd']):+.3f}`",
            f"- Best-vs-live realized delta: `{float(best['realized_net_usd']) - float(baseline['realized_net_usd']):+.3f}`",
            f"- Best-vs-live floating delta: `{float(best['marked_floating_usd']) - float(baseline['marked_floating_usd']):+.3f}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the live BTCUSD H1 lane across a max-open-per-side ladder.")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--caps", default=DEFAULT_CAPS)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--csv-out", default=str(DEFAULT_CSV))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    caps = parse_caps(args.caps)
    shape = load_live_shape()
    broker_context = load_broker_context()
    if shape.max_open_per_side not in caps:
        caps.append(shape.max_open_per_side)
        caps = sorted(caps)
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=max(1, int(args.days)))
        rows: list[dict[str, object]] = []
        total = len(caps)
        for idx, max_open in enumerate(caps, start=1):
            row = replay_cap(shape, max_open_per_side=max_open, start_utc=start_utc, end_utc=end_utc, chunk_hours=int(args.chunk_hours))
            rows.append(row)
            if args.progress:
                print(
                    f"[{idx}/{total}] max_open={max_open} marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
                    f"floating={row['marked_floating_usd']} closes={row['realized_closes']} rearm={row['rearm_opens']}",
                    flush=True,
                )
        rows = sorted(rows, key=lambda row: int(row["max_open_per_side"]))
        write_csv(Path(args.csv_out), rows)
        write_md(Path(args.md_out), rows, shape=shape, start_utc=start_utc, end_utc=end_utc, broker_context=broker_context)
        for row in rank_rows(rows):
            print(
                f"max_open={row['max_open_per_side']}: marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
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
