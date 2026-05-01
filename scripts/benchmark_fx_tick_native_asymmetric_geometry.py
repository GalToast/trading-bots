#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import default_apex_mix
from tick_penetration_lattice_core import TickStatefulRearmEngine, engine_from_args, load_ticks_range, tick_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "reports" / "fx_tick_native_asymmetric_geometry.csv"
DEFAULT_MD = ROOT / "reports" / "fx_tick_native_asymmetric_geometry.md"

SELL_STEP_GRID = {
    "GBPUSD": [0.5, 0.75, 1.0],
    "EURUSD": [0.75, 1.0, 1.25],
    "NZDUSD": [0.25, 0.5, 0.75],
}

BUY_STEP_GRID = {
    "GBPUSD": [0.5, 0.75, 1.0, 1.25],
    "EURUSD": [0.75, 1.0, 1.25],
    "NZDUSD": [0.5, 0.75, 1.0],
}

SYMMETRIC_PIVOT = {
    "GBPUSD": 1.0,
    "EURUSD": 1.0,
    "NZDUSD": 0.5,
}


@dataclass(frozen=True)
class AsymmetricShape:
    step_sell: float
    step_buy: float

    @property
    def is_symmetric(self) -> bool:
        return abs(float(self.step_sell) - float(self.step_buy)) < 1e-9


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark tick-native FX with side-asymmetric step geometry.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--timeframe", default="M1")
    parser.add_argument("--chunk-hours", type=int, default=6)
    parser.add_argument("--variant", default="rearm_lvl2_exc2")
    parser.add_argument("--close-alpha", type=float, default=0.5)
    parser.add_argument("--sell-gap", type=int, default=1)
    parser.add_argument("--buy-gap", type=int, default=1)
    parser.add_argument("--momentum-gate", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--csv-out", default=str(DEFAULT_CSV))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    return parser.parse_args()


def _marks_floating_net(
    engine: TickStatefulRearmEngine,
    symbol: str,
    last_tick: dict[str, float] | None,
) -> float:
    if last_tick is None:
        return 0.0
    bid = float(last_tick["bid"])
    ask = float(last_tick["ask"])
    floating = 0.0
    for ticket in engine.state.open_tickets or []:
        direction = str(ticket.get("direction", "") or "").upper()
        fill_price = float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("trigger_level", 0.0))) or 0.0)
        floating += tick_pnl_usd(symbol, direction, fill_price, bid if direction == "BUY" else ask, volume=engine.volume)
    return float(floating)


def shape_grid() -> dict[str, list[AsymmetricShape]]:
    out: dict[str, list[AsymmetricShape]] = {}
    for symbol in ("GBPUSD", "EURUSD", "NZDUSD"):
        out[symbol] = [
            AsymmetricShape(step_sell=float(ss), step_buy=float(sb))
            for ss in SELL_STEP_GRID[symbol]
            for sb in BUY_STEP_GRID[symbol]
        ]
    return out


def make_engine(symbol: str, shape: AsymmetricShape, *, max_open_per_side: int, variant_name: str, close_alpha: float, momentum_gate: bool, sell_gap: int, buy_gap: int) -> TickStatefulRearmEngine:
    mix = default_apex_mix()
    _, cfg = mix[symbol]
    base_step = max(float(shape.step_sell), float(shape.step_buy))
    return engine_from_args(
        symbol=symbol,
        timeframe_name="M1",
        step=base_step,
        max_open_per_side=int(max_open_per_side),
        variant_name=variant_name,
        close_alpha=float(close_alpha),
        momentum_gate=bool(momentum_gate),
        cooldown_bars=0,
        sell_gap=int(sell_gap),
        buy_gap=int(buy_gap),
        step_sell=float(shape.step_sell),
        step_buy=float(shape.step_buy),
        volume=0.01,
    )


def replay_symbol_shape(
    symbol: str,
    shape: AsymmetricShape,
    *,
    max_open_per_side: int,
    variant_name: str,
    close_alpha: float,
    momentum_gate: bool,
    sell_gap: int,
    buy_gap: int,
    timeframe: str,
    start_utc: datetime,
    end_utc: datetime,
    chunk_hours: int,
) -> dict[str, object]:
    engine = make_engine(
        symbol,
        shape,
        max_open_per_side=max_open_per_side,
        variant_name=variant_name,
        close_alpha=close_alpha,
        momentum_gate=momentum_gate,
        sell_gap=sell_gap,
        buy_gap=buy_gap,
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
    floating = _marks_floating_net(engine, symbol, last_tick)
    closes = int(engine.state.realized_closes or 0)
    days = max((end_utc - start_utc).total_seconds() / 86400.0, 1e-9)
    marked_net = realized + floating
    return {
        "symbol": symbol,
        "timeframe": str(timeframe),
        "step_sell": round(float(shape.step_sell), 3),
        "step_buy": round(float(shape.step_buy), 3),
        "is_symmetric": 1 if shape.is_symmetric else 0,
        "ticks_processed": int(total_ticks),
        "days": round(days, 3),
        "realized_net_usd": round(realized, 3),
        "marked_floating_usd": round(floating, 3),
        "marked_net_usd": round(marked_net, 3),
        "realized_closes": closes,
        "rearm_opens": int(engine.state.rearm_opens or 0),
        "open_count": len(engine.state.open_tickets or []),
        "max_open_total": int(engine.state.max_open_total or 0),
        "next_buy_level": round(float(engine.state.next_buy_level or 0.0), 6),
        "next_sell_level": round(float(engine.state.next_sell_level or 0.0), 6),
        "close_alpha": float(close_alpha),
        "variant": variant_name,
    }


def build_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "# FX Tick-Native Asymmetric Geometry",
        "",
        "This benchmark sweeps side-specific entry spacing under strict tick-native execution.",
    ]
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, rows: list[dict[str, object]], *, start_utc: datetime, end_utc: datetime) -> None:
    lines: list[str] = [
        "# FX Tick-Native Asymmetric Geometry",
        "",
        f"- Window: `{start_utc.isoformat()}` to `{end_utc.isoformat()}`",
        f"- Geometry set from asymmetric side ladders for GBPUSD/EURUSD/NZDUSD",
        "",
        "## Top by Marked Net (Per-Symbol)",
        "",
        "| Symbol | Sell Step | Buy Step | Symmetric? | Marked Net | Realized | Floating | Closes | Open |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['symbol']} | {row['step_sell']} | {row['step_buy']} | "
            f"{str(row['is_symmetric'] == 1).lower()} | {row['marked_net_usd']} | {row['realized_net_usd']} | "
            f"{row['marked_floating_usd']} | {row['realized_closes']} | {row['open_count']} |"
        )
    lines.append("")
    lines.append("## Read")
    lines.append("")
    for symbol in ("GBPUSD", "EURUSD", "NZDUSD"):
        symbol_rows = [r for r in rows if r["symbol"] == symbol]
        if not symbol_rows:
            continue
        best = max(symbol_rows, key=lambda row: float(row["marked_net_usd"]))
        lines.append(f"- {symbol}: best row `sell={best['step_sell']} / buy={best['step_buy']}` -> `{best['marked_net_usd']}`.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        mix = default_apex_mix()
        rows: list[dict[str, object]] = []
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=max(1, int(args.days)))
        for symbol in args.symbols:
            symbol = symbol.upper()
            if symbol not in mix:
                print(f"Missing mix entry for {symbol}")
                continue
            if symbol not in SELL_STEP_GRID or symbol not in BUY_STEP_GRID:
                continue
            cfg = mix[symbol][1]
            if symbol not in SYMMETRIC_PIVOT:
                continue
            for shape in shape_grid()[symbol]:
                row = replay_symbol_shape(
                    symbol,
                    shape,
                    max_open_per_side=int(getattr(cfg, "max_open_per_side", 20)),
                    variant_name=str(args.variant),
                    close_alpha=float(args.close_alpha),
                    momentum_gate=bool(args.momentum_gate),
                    sell_gap=int(args.sell_gap),
                    buy_gap=int(args.buy_gap),
                    timeframe=str(args.timeframe),
                    start_utc=start_utc,
                    end_utc=end_utc,
                    chunk_hours=int(args.chunk_hours),
                )
                baseline = float(SYMMETRIC_PIVOT[symbol])
                row["delta_vs_symmetry"] = round(
                    float(row["marked_net_usd"])
                    - float(replay_symbol_shape(
                        symbol,
                        AsymmetricShape(step_sell=baseline, step_buy=baseline),
                        max_open_per_side=int(getattr(cfg, "max_open_per_side", 20)),
                        variant_name=str(args.variant),
                        close_alpha=float(args.close_alpha),
                        momentum_gate=bool(args.momentum_gate),
                        sell_gap=int(args.sell_gap),
                        buy_gap=int(args.buy_gap),
                        timeframe=str(args.timeframe),
                        start_utc=start_utc,
                        end_utc=end_utc,
                        chunk_hours=int(args.chunk_hours),
                    )["marked_net_usd"]),
                    3,
                )
                rows.append(row)

        ranked = sorted(rows, key=lambda row: (str(row["symbol"]), float(row["marked_net_usd"])), reverse=True)
        ranked = sorted(ranked, key=lambda row: float(row["marked_net_usd"]), reverse=True)
        write_csv(Path(args.csv_out), ranked)
        write_md(Path(args.md_out), ranked, start_utc=start_utc, end_utc=end_utc)
        if args.progress:
            for row in ranked[:10]:
                print(
                    f"{row['symbol']} sell={row['step_sell']} buy={row['step_buy']} -> "
                    f"marked={row['marked_net_usd']} realized={row['realized_net_usd']} opens={row['open_count']}"
                )
        if ranked:
            best = ranked[0]
            print(f"Best: {best['symbol']} {best['step_sell']}/{best['step_buy']} marked={best['marked_net_usd']}")
        print(f"Wrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
