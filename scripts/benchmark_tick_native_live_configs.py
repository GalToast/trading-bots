#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import default_apex_mix
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from tick_penetration_lattice_core import (
    TickBoundedRearmEngine,
    TickStatefulRearmEngine,
    bounded_engine_from_args,
    engine_from_args,
    load_ticks_range,
)


ROOT = Path(__file__).resolve().parent.parent
REPORT_CSV = ROOT / "reports" / "tick_native_live_configs.csv"
REPORT_MD = ROOT / "reports" / "tick_native_live_configs.md"


@dataclass(frozen=True)
class LaneSpec:
    lane_id: str
    symbol: str
    engine_kind: str
    timeframe: str
    raw_step: float | None = None
    raw_variant: str = ""
    raw_momentum_gate: bool = False
    raw_cooldown_bars: int = 0
    raw_sell_gap: int = 1
    raw_buy_gap: int = 1
    max_open_per_side: int = 0
    bounded_variant: str = ""
    bounded_close_gap: int = 1
    bounded_same_bar_min_pnl: float = 0.0
    bounded_same_bar_shallow_level_cap: int = 0


def build_lane_specs() -> list[LaneSpec]:
    mix = default_apex_mix()
    eur_cfg = mix["EURUSD"][1]
    gbp_cfg = mix["GBPUSD"][1]
    nzd_cfg = mix["NZDUSD"][1]
    usdjpy_cfg = mix["USDJPY"][1]
    return [
        LaneSpec(
            lane_id="live_rearm_941777",
            symbol="EURUSD",
            engine_kind="raw",
            timeframe="M1",
            raw_step=float(eur_cfg.step_pips) * 0.0001,
            raw_variant="rearm_lvl2_exc2",
            max_open_per_side=int(eur_cfg.max_open_per_side),
        ),
        LaneSpec(
            lane_id="live_rearm_941777",
            symbol="GBPUSD",
            engine_kind="raw",
            timeframe="M1",
            raw_step=float(gbp_cfg.step_pips) * 0.0001,
            raw_variant="rearm_lvl2_exc2",
            max_open_per_side=int(gbp_cfg.max_open_per_side),
        ),
        LaneSpec(
            lane_id="live_rearm_941777",
            symbol="USDJPY",
            engine_kind="bounded",
            timeframe="M1",
            bounded_variant="rearm_lvl2_exc2",
            bounded_close_gap=1,
        ),
        LaneSpec(
            lane_id="live_momentum_alpha50_941778",
            symbol="EURUSD",
            engine_kind="raw",
            timeframe="M1",
            raw_step=float(eur_cfg.step_pips) * 0.0001,
            raw_variant="rearm_lvl2_exc1",
            raw_momentum_gate=True,
            max_open_per_side=int(eur_cfg.max_open_per_side),
        ),
        LaneSpec(
            lane_id="live_momentum_alpha50_941778",
            symbol="GBPUSD",
            engine_kind="raw",
            timeframe="M1",
            raw_step=float(gbp_cfg.step_pips) * 0.0001,
            raw_variant="rearm_lvl2_exc1",
            raw_momentum_gate=True,
            max_open_per_side=int(gbp_cfg.max_open_per_side),
        ),
        LaneSpec(
            lane_id="live_momentum_alpha50_941778",
            symbol="NZDUSD",
            engine_kind="raw",
            timeframe="M1",
            raw_step=float(nzd_cfg.step_pips) * 0.0001,
            raw_variant="rearm_lvl2_exc1",
            raw_momentum_gate=True,
            max_open_per_side=int(nzd_cfg.max_open_per_side),
        ),
        LaneSpec(
            lane_id="live_btcusd_exc2_tight_941779",
            symbol="BTCUSD",
            engine_kind="raw",
            timeframe="H1",
            raw_step=45.0,
            raw_variant="rearm_lvl2_exc2",
            raw_momentum_gate=True,
            max_open_per_side=50,
        ),
    ]


def make_engine(spec: LaneSpec, mix: dict[str, tuple[str, object]]) -> TickStatefulRearmEngine | TickBoundedRearmEngine:
    if spec.engine_kind == "raw":
        return engine_from_args(
            symbol=spec.symbol,
            timeframe_name=spec.timeframe,
            step=float(spec.raw_step or 0.0),
            max_open_per_side=int(spec.max_open_per_side),
            variant_name=spec.raw_variant,
            momentum_gate=spec.raw_momentum_gate,
            cooldown_bars=spec.raw_cooldown_bars,
            sell_gap=spec.raw_sell_gap,
            buy_gap=spec.raw_buy_gap,
        )
    bounded_cfg = mix[spec.symbol][1]
    if not isinstance(bounded_cfg, BoundedConfig):
        raise RuntimeError(f"Expected bounded config for {spec.symbol}")
    return bounded_engine_from_args(
        symbol=spec.symbol,
        timeframe_name=spec.timeframe,
        cfg=bounded_cfg,
        variant_name=spec.bounded_variant,
        close_gap=spec.bounded_close_gap,
        same_bar_min_pnl=spec.bounded_same_bar_min_pnl,
        same_bar_shallow_level_cap=spec.bounded_same_bar_shallow_level_cap,
    )


def replay_spec(spec: LaneSpec, *, days: int, chunk_hours: int, mix: dict[str, tuple[str, object]]) -> dict[str, object]:
    engine = make_engine(spec, mix)
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(days=max(1, int(days)))
    chunk = timedelta(hours=max(1, int(chunk_hours)))
    cursor = start_utc
    total_ticks = 0
    while cursor < end_utc:
        chunk_end = min(end_utc, cursor + chunk)
        ticks = load_ticks_range(spec.symbol, cursor, chunk_end)
        total_ticks += engine.process_ticks(ticks, action_sink=None, event_path=None, emit=False)
        cursor = chunk_end
    return {
        "lane_id": spec.lane_id,
        "symbol": spec.symbol,
        "engine_kind": spec.engine_kind,
        "timeframe": spec.timeframe,
        "days": int(days),
        "ticks_processed": total_ticks,
        "realized_net_usd": round(float(engine.state.realized_net_usd or 0.0), 3),
        "realized_closes": int(engine.state.realized_closes or 0),
        "rearm_opens": int(engine.state.rearm_opens or 0),
        "open_count": len(engine.state.open_tickets or []),
        "max_open_total": int(engine.state.max_open_total or 0),
        "next_buy_level": round(float(engine.state.next_buy_level or 0.0), 6),
        "next_sell_level": round(float(engine.state.next_sell_level or 0.0), 6),
    }


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else [
        "lane_id",
        "symbol",
        "engine_kind",
        "timeframe",
        "days",
        "ticks_processed",
        "realized_net_usd",
        "realized_closes",
        "rearm_opens",
        "open_count",
        "max_open_total",
        "next_buy_level",
        "next_sell_level",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_md(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Tick-Native Live Config Benchmark",
        "",
        "| Lane | Symbol | Engine | TF | Days | Ticks | Realized | Closes | Rearm | Open | Max Open |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['lane_id']}` | `{row['symbol']}` | `{row['engine_kind']}` | `{row['timeframe']}` | "
            f"{row['days']} | {row['ticks_processed']} | {row['realized_net_usd']} | {row['realized_closes']} | "
            f"{row['rearm_opens']} | {row['open_count']} | {row['max_open_total']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark current live lane configs using the tick-native engines.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--chunk-hours", type=int, default=12)
    parser.add_argument("--bounded-same-bar-min-pnl", type=float, default=0.0)
    parser.add_argument("--bounded-same-bar-shallow-level-cap", type=int, default=0)
    parser.add_argument("--csv-out", default=str(REPORT_CSV))
    parser.add_argument("--md-out", default=str(REPORT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        mix = default_apex_mix()
        specs: list[LaneSpec] = []
        for spec in build_lane_specs():
            if spec.engine_kind == "bounded":
                spec = LaneSpec(
                    lane_id=spec.lane_id,
                    symbol=spec.symbol,
                    engine_kind=spec.engine_kind,
                    timeframe=spec.timeframe,
                    raw_step=spec.raw_step,
                    raw_variant=spec.raw_variant,
                    raw_momentum_gate=spec.raw_momentum_gate,
                    raw_cooldown_bars=spec.raw_cooldown_bars,
                    raw_sell_gap=spec.raw_sell_gap,
                    raw_buy_gap=spec.raw_buy_gap,
                    max_open_per_side=spec.max_open_per_side,
                    bounded_variant=spec.bounded_variant,
                    bounded_close_gap=spec.bounded_close_gap,
                    bounded_same_bar_min_pnl=max(0.0, float(args.bounded_same_bar_min_pnl)),
                    bounded_same_bar_shallow_level_cap=max(0, int(args.bounded_same_bar_shallow_level_cap)),
                )
            specs.append(spec)
        rows = [replay_spec(spec, days=int(args.days), chunk_hours=int(args.chunk_hours), mix=mix) for spec in specs]
        write_csv(rows, Path(args.csv_out))
        write_md(rows, Path(args.md_out))
        for row in rows:
            print(
                f"{row['lane_id']} {row['symbol']}: realized={row['realized_net_usd']} "
                f"closes={row['realized_closes']} rearm={row['rearm_opens']} open={row['open_count']} ticks={row['ticks_processed']}"
            )
        print(f"Wrote {Path(args.csv_out)}")
        print(f"Wrote {Path(args.md_out)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
