#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType

import MetaTrader5 as mt5

from tick_penetration_lattice_core import TickStatefulRearmEngine, engine_from_args, load_ticks_range, tick_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "reports" / "live_btcusd_h1_rearm_gate_benchmark.csv"
DEFAULT_MD = ROOT / "reports" / "live_btcusd_h1_rearm_gate_benchmark.md"
SYMBOL = "BTCUSD"


@dataclass(frozen=True)
class VariantSpec:
    label: str
    patch_buy_rearm_gate: bool


def build_specs() -> list[VariantSpec]:
    return [
        VariantSpec(label="current_contradictory_gate", patch_buy_rearm_gate=False),
        VariantSpec(label="buy_rearm_gate_consistent", patch_buy_rearm_gate=True),
    ]


def patch_buy_rearm_gate(engine: TickStatefulRearmEngine) -> None:
    original = engine._momentum_gate_allows

    def patched(self: TickStatefulRearmEngine, direction: str, level: float, tick: dict[str, object]) -> bool:
        if str(direction or "").upper() == "BUY" and bool(self.momentum_gate):
            return float(tick["ask"]) <= float(level)
        return original(direction, level, tick)

    engine._momentum_gate_allows = MethodType(patched, engine)


def marked_floating_net(engine: TickStatefulRearmEngine, last_tick: dict[str, float] | None) -> float:
    if last_tick is None:
        return 0.0
    bid = float(last_tick["bid"])
    ask = float(last_tick["ask"])
    floating = 0.0
    for ticket in engine.state.open_tickets or []:
        direction = str(ticket.get("direction", "") or "").upper()
        fill_price = float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("trigger_level", 0.0))) or 0.0)
        if direction == "BUY":
            floating += tick_pnl_usd(SYMBOL, direction, fill_price, bid)
        elif direction == "SELL":
            floating += tick_pnl_usd(SYMBOL, direction, fill_price, ask)
    return float(floating)


def make_engine(spec: VariantSpec) -> TickStatefulRearmEngine:
    engine = engine_from_args(
        symbol=SYMBOL,
        timeframe_name="H1",
        step=45.0,
        max_open_per_side=50,
        variant_name="rearm_lvl2_exc2",
        close_alpha=1.0,
        momentum_gate=True,
        cooldown_bars=0,
        sell_gap=1,
        buy_gap=1,
    )
    if spec.patch_buy_rearm_gate:
        patch_buy_rearm_gate(engine)
    return engine


def replay_spec(spec: VariantSpec, *, start_utc: datetime, end_utc: datetime, chunk_hours: int) -> dict[str, object]:
    engine = make_engine(spec)
    cursor = start_utc
    chunk = timedelta(hours=max(1, int(chunk_hours)))
    total_ticks = 0
    last_tick: dict[str, float] | None = None
    while cursor < end_utc:
        chunk_end = min(end_utc, cursor + chunk)
        ticks = load_ticks_range(SYMBOL, cursor, chunk_end)
        if ticks:
            last_tick = ticks[-1]
            total_ticks += engine.process_ticks(ticks, action_sink=None, event_path=None, emit=False)
        cursor = chunk_end
    realized = float(engine.state.realized_net_usd or 0.0)
    floating = marked_floating_net(engine, last_tick)
    closes = int(engine.state.realized_closes or 0)
    days = max((end_utc - start_utc).total_seconds() / 86400.0, 1e-9)
    marked_net = realized + floating
    buy_count = sum(1 for ticket in engine.state.open_tickets or [] if str(ticket.get("direction", "")).upper() == "BUY")
    sell_count = sum(1 for ticket in engine.state.open_tickets or [] if str(ticket.get("direction", "")).upper() == "SELL")
    return {
        "label": spec.label,
        "days": round(days, 3),
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


def write_md(path: Path, rows: list[dict[str, object]], *, start_utc: datetime, end_utc: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = rows[0]
    lines = [
        "# Live BTCUSD H1 Rearm Gate Benchmark",
        "",
        f"- Window: `{start_utc.isoformat()}` to `{end_utc.isoformat()}`",
        "- Engine: `TickStatefulRearmEngine` with live H1 BTC shape (`step=45`, `rearm_lvl2_exc2`, `gap=1/1`, `momentum_gate=true`)",
        "- Comparison isolates the BUY rearm momentum gate only.",
        "- `buy_rearm_gate_consistent` changes BUY rearm gating from contradictory `ask > level` to consistent `ask <= level` at reopen time.",
        "",
        "## Results",
        "",
        "| Label | Marked Net | Realized | Floating | Closes | Rearm | Open | BUY | SELL | Net/Close |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['label']}` | {row['marked_net_usd']} | {row['realized_net_usd']} | {row['marked_floating_usd']} | "
            f"{row['realized_closes']} | {row['rearm_opens']} | {row['open_count']} | {row['buy_open_count']} | {row['sell_open_count']} | {row['net_per_close_usd']} |"
        )
    if len(rows) >= 2:
        alt = rows[1]
        lines.extend(
            [
                "",
                "## Delta vs Current",
                "",
                f"- Marked net delta: `{float(alt['marked_net_usd']) - float(baseline['marked_net_usd']):+.3f}`",
                f"- Realized delta: `{float(alt['realized_net_usd']) - float(baseline['realized_net_usd']):+.3f}`",
                f"- Floating delta: `{float(alt['marked_floating_usd']) - float(baseline['marked_floating_usd']):+.3f}`",
                f"- Close delta: `{int(alt['realized_closes']) - int(baseline['realized_closes']):+d}`",
                f"- Rearm-open delta: `{int(alt['rearm_opens']) - int(baseline['rearm_opens']):+d}`",
                f"- Open-count delta: `{int(alt['open_count']) - int(baseline['open_count']):+d}`",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark BTCUSD H1 rearm-gate variants using the tick-native engine.")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--progress", action="store_true")
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
        for idx, spec in enumerate(build_specs(), start=1):
            row = replay_spec(spec, start_utc=start_utc, end_utc=end_utc, chunk_hours=int(args.chunk_hours))
            rows.append(row)
            if args.progress:
                print(
                    f"[{idx}/2] {row['label']} marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
                    f"floating={row['marked_floating_usd']} closes={row['realized_closes']} rearm={row['rearm_opens']}",
                    flush=True,
                )
        write_csv(Path(args.csv_out), rows)
        write_md(Path(args.md_out), rows, start_utc=start_utc, end_utc=end_utc)
        for row in rows:
            print(
                f"{row['label']}: marked={row['marked_net_usd']} realized={row['realized_net_usd']} "
                f"floating={row['marked_floating_usd']} closes={row['realized_closes']} "
                f"rearm={row['rearm_opens']} open={row['open_count']}"
            )
        print(f"Wrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
