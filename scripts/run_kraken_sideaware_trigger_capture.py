#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kraken_config as cfg  # noqa: E402
from build_kraken_spot_live_radar import build_once as build_radar_once  # noqa: E402
from run_kraken_crossing_pressure_tape import FILL_LIKE_RESULTS  # noqa: E402
from run_kraken_crossing_pressure_tape import load_radar_side_heartbeat_products, parse_float_csv  # noqa: E402
from run_kraken_crossing_pressure_tape import summarize as summarize_cycles  # noqa: E402
from run_kraken_crossing_pressure_tape import run as run_crossing_tape  # noqa: E402
from kraken_spot_client import KrakenSpotClient  # noqa: E402
from run_kraken_maker_microfill_calibrator import load_pair_info, run_trial  # noqa: E402


DEFAULT_EVENT_PATH = REPORTS / "kraken_sideaware_trigger_capture_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_sideaware_trigger_capture_summary.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_RADAR_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def radar_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        state_path=str(args.radar_cache_path),
        json_path=str(args.radar_path),
        csv_path=str(args.radar_csv_path),
        md_path=str(args.radar_md_path),
        quotes=args.quotes,
        all_quotes=False,
        max_products=args.max_products,
        chunk_size=args.chunk_size,
        keep_seconds=args.keep_seconds,
        poll_seconds=args.poll_seconds,
        loop=False,
        max_spread_bps=args.max_radar_spread_bps,
        hot_bps=args.hot_bps,
        building_bps=args.building_bps,
        starting_cash=args.starting_cash,
        deploy_pct=args.deploy_pct,
        maker_fee_bps=args.maker_fee_bps,
        taker_fee_bps=args.taker_fee_bps,
        use_websocket=args.use_websocket,
        websocket_timeout_seconds=args.websocket_timeout_seconds,
    )


def tape_args(args: argparse.Namespace, *, cycle_index: int) -> SimpleNamespace:
    cycle_summary_path = args.summary_path.with_name(f"{args.summary_path.stem}_cycle_{cycle_index:04d}{args.summary_path.suffix}")
    return SimpleNamespace(
        products=[],
        product_source="radar-side-heartbeat",
        quote_currencies=args.quotes.split(","),
        top_products=args.top_products,
        min_spread_bps=0.0,
        min_volume_24h=0.0,
        radar_path=args.radar_path,
        radar_cache_path=args.radar_cache_path,
        radar_states=["live_hot", "building"],
        max_radar_spread_bps=args.max_radar_spread_bps,
        min_best_short_bps=args.min_best_short_bps,
        min_radar_samples=args.min_radar_samples,
        min_ask_down_bps=args.min_ask_down_bps,
        min_bid_up_bps=args.min_bid_up_bps,
        min_latest_ask_down_bps=args.min_latest_ask_down_bps,
        min_latest_bid_up_bps=args.min_latest_bid_up_bps,
        side_lookback_seconds=args.side_lookback_seconds,
        side_mode=args.side_mode,
        offsets=args.offsets.split(","),
        cycles=args.tape_cycles,
        ttl_seconds=args.ttl_seconds,
        poll_seconds=args.tape_poll_seconds,
        ghost_penalty_bps=args.ghost_penalty_bps,
        event_path=args.event_path,
        summary_path=cycle_summary_path,
    )


def load_cycle_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("action") == "crossing_pressure_cycle":
            records.append(row)
    return records


def is_fill_like(result: Any) -> bool:
    return str(result or "") in FILL_LIKE_RESULTS


def directional_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[str, dict[str, Any]] = {}
    for record in records:
        key = f"{record.get('product_id')}|{record.get('side')}|{float(record.get('offset_frac', 0.0)):.4f}"
        row = by_key.setdefault(
            key,
            {
                "key": key,
                "trials": 0,
                "fill_like": 0,
                "fill_rate": 0.0,
                "example": None,
            },
        )
        row["trials"] += 1
        if record.get("fill_like"):
            row["fill_like"] += 1
            row["example"] = row.get("example") or record
    leaders = []
    for row in by_key.values():
        row["fill_rate"] = round(row["fill_like"] / row["trials"], 6) if row["trials"] else 0.0
        leaders.append(row)
    leaders.sort(key=lambda row: (-float(row["fill_rate"]), -int(row["fill_like"]), str(row["key"])))
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_sideaware_directional_summary",
        "records": len(records),
        "fill_like_records": sum(1 for record in records if record.get("fill_like")),
        "leaders": leaders,
    }


def load_directional_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("action") == "sideaware_directional_trial":
            records.append(row)
    return records


def run_directional_tape(args: argparse.Namespace, *, cycle_index: int) -> dict[str, Any]:
    products, selected = load_radar_side_heartbeat_products(tape_args(args, cycle_index=cycle_index))
    offsets = parse_float_csv(args.offsets)
    client = KrakenSpotClient()
    pair_info = load_pair_info(client, products)
    leaders: list[dict[str, Any]] = []
    for product in products:
        info = pair_info[product]
        for offset in offsets:
            trial = run_trial(
                client=client,
                product=product,
                rest_pair=info.rest_pair,
                side=args.trial_side,
                price_offset_frac=offset,
                tick_back=None,
                tick_size=info.tick_size,
                ttl_seconds=args.ttl_seconds,
                poll_seconds=args.tape_poll_seconds,
                ghost_penalty_bps=args.ghost_penalty_bps,
            )
            append_jsonl(args.event_path, trial)
            record = {
                "action": "sideaware_directional_trial",
                "cycle_index": cycle_index,
                "ts_utc": utc_now_iso(),
                "product_id": product,
                "side": args.trial_side,
                "offset_frac": float(offset),
                "result": trial.get("result"),
                "reason": trial.get("reason"),
                "fill_like": is_fill_like(trial.get("result")),
                "elapsed_seconds": trial.get("elapsed_seconds"),
                "initial_spread_bps": trial.get("initial_spread_bps"),
                "last_spread_bps": trial.get("last_spread_bps"),
                "order_price": trial.get("order_price"),
                "read": "Public-only side-specific trigger capture. No private endpoints, validate calls, or live orders.",
            }
            append_jsonl(args.event_path, record)
            leaders.append(
                {
                    "key": f"{product}|{args.trial_side}|{float(offset):.4f}",
                    "fill_like": int(record["fill_like"]),
                    "result": record["result"],
                    "elapsed_seconds": record["elapsed_seconds"],
                }
            )
    return {
        "selected_products": selected,
        "leaders": leaders,
        "fill_like_records": sum(int(row["fill_like"]) for row in leaders),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    trigger_runs: list[dict[str, Any]] = []
    for cycle_index in range(1, int(args.cycles) + 1):
        radar_payload = build_radar_once(radar_args(args))
        if args.trial_side == "paired":
            tape_summary = run_crossing_tape(tape_args(args, cycle_index=cycle_index))
        else:
            tape_summary = run_directional_tape(args, cycle_index=cycle_index)
        selected = tape_summary.get("selected_products") or []
        trigger_run = {
            "action": "sideaware_trigger_capture_cycle",
            "cycle_index": cycle_index,
            "ts_utc": utc_now_iso(),
            "radar_generated_at": radar_payload.get("generated_at"),
            "selected_products": selected,
            "leaders": tape_summary.get("leaders", [])[:5],
            "two_sided_records": tape_summary.get("two_sided_records", 0),
            "fill_like_records": tape_summary.get("fill_like_records", 0),
            "read": "Public-only trigger capture. No private endpoints, validate calls, or live orders.",
        }
        append_jsonl(args.event_path, trigger_run)
        trigger_runs.append(trigger_run)
        if cycle_index < int(args.cycles):
            time.sleep(max(0.0, float(args.sleep_seconds)))

    if args.trial_side == "paired":
        all_records = load_cycle_records(args.event_path)
        aggregate = summarize_cycles(all_records)
    else:
        all_records = load_directional_records(args.event_path)
        aggregate = directional_summary(all_records)
    summary = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_sideaware_trigger_capture",
        "read": "Continuous public-only side-aware radar refresh plus immediate paired fillability tape. Not live-order permission.",
        "parameters": vars(args),
        "trigger_runs": trigger_runs,
        "aggregate": aggregate,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously refresh Kraken radar and immediately test side-aware fillability.")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--sleep-seconds", type=float, default=10.0)
    parser.add_argument("--quotes", default="USD")
    parser.add_argument("--max-products", type=int, default=10000)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--keep-seconds", type=float, default=3900.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--use-websocket", action="store_true")
    parser.add_argument("--websocket-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--starting-cash", type=float, default=50.0)
    parser.add_argument("--deploy-pct", type=float, default=0.3)
    parser.add_argument("--maker-fee-bps", type=float, default=cfg.DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--hot-bps", type=float, default=25.0)
    parser.add_argument("--building-bps", type=float, default=10.0)
    parser.add_argument("--top-products", type=int, default=4)
    parser.add_argument("--max-radar-spread-bps", type=float, default=250.0)
    parser.add_argument("--min-best-short-bps", type=float, default=10.0)
    parser.add_argument("--min-radar-samples", type=int, default=3)
    parser.add_argument("--min-ask-down-bps", type=float, default=5.0)
    parser.add_argument("--min-bid-up-bps", type=float, default=5.0)
    parser.add_argument("--min-latest-ask-down-bps", type=float, default=0.0)
    parser.add_argument("--min-latest-bid-up-bps", type=float, default=0.0)
    parser.add_argument("--side-lookback-seconds", type=float, default=120.0)
    parser.add_argument("--side-mode", choices=["both", "entry", "exit", "either"], default="both")
    parser.add_argument("--trial-side", choices=["paired", "buy", "sell"], default="paired")
    parser.add_argument("--offsets", default="0.5")
    parser.add_argument("--tape-cycles", type=int, default=1)
    parser.add_argument("--ttl-seconds", type=float, default=10.0)
    parser.add_argument("--tape-poll-seconds", type=float, default=1.0)
    parser.add_argument("--ghost-penalty-bps", type=float, default=2.0)
    parser.add_argument("--radar-path", type=Path, default=DEFAULT_RADAR_PATH)
    parser.add_argument("--radar-cache-path", type=Path, default=DEFAULT_RADAR_CACHE_PATH)
    parser.add_argument("--radar-csv-path", type=Path, default=REPORTS / "kraken_spot_live_radar.csv")
    parser.add_argument("--radar-md-path", type=Path, default=REPORTS / "kraken_spot_live_radar.md")
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser.parse_args()


def main() -> int:
    summary = run(parse_args())
    print(json.dumps({"summary_path": str(summary["parameters"]["summary_path"]), "aggregate": summary["aggregate"]["leaders"][:5]}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
