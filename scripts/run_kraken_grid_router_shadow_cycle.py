#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"


DEFAULT_EVENT_PATH = REPORTS / "kraken_grid_router_shadow_cycle_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_grid_router_shadow_cycle_summary.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def build_router_command(args: argparse.Namespace, *, json_path: Path, md_path: Path) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS / "build_kraken_grid_exit_first_router.py"),
        "--top-n-volume",
        str(args.top_n_volume),
        "--lookback-seconds",
        str(args.lookback_seconds),
        "--trade-count",
        str(args.trade_count),
        "--spacing-bps",
        str(args.spacing_bps),
        "--levels",
        str(args.levels),
        "--entry-offset-mult",
        str(args.entry_offset_mult),
        "--initial-capital",
        str(args.initial_capital),
        "--max-spread-bps",
        str(args.max_spread_bps),
        "--min-recent-trades",
        str(args.min_recent_trades),
        "--max-roundtrip-seconds",
        str(args.max_roundtrip_seconds),
        "--max-signal-age-seconds",
        str(args.max_signal_age_seconds),
        "--trade-volume-participation",
        str(args.trade_volume_participation),
        "--json-path",
        str(json_path),
        "--md-path",
        str(md_path),
    ]


def build_shadow_command(args: argparse.Namespace, *, product_id: str, event_path: Path, summary_path: Path) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS / "run_kraken_grid_shadow_tape.py"),
        "--products",
        product_id,
        "--spacing-bps",
        str(args.spacing_bps),
        "--entry-offset-mult",
        str(args.entry_offset_mult),
        "--levels",
        str(args.levels),
        "--initial-capital",
        str(args.initial_capital),
        "--max-spread-bps",
        str(args.max_spread_bps),
        "--min-depth-usd",
        str(args.min_depth_usd),
        "--fill-source",
        "trade_tape",
        "--trade-volume-participation",
        str(args.trade_volume_participation),
        "--trade-lookback-seconds",
        str(args.trade_lookback_seconds),
        "--duration-seconds",
        str(args.shadow_duration_seconds),
        "--poll-seconds",
        str(args.poll_seconds),
        "--depth-count",
        str(args.depth_count),
        "--event-path",
        str(event_path),
        "--summary-path",
        str(summary_path),
    ]


def best_fire_candidate(router_payload: dict[str, Any]) -> dict[str, Any] | None:
    for row in router_payload.get("rows") or []:
        if row.get("roundtrip_exit_ok") and not row.get("blockers"):
            return row
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously route Kraken grid shadow to current exit-first fire candidates.")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--sleep-between-cycles", type=float, default=5.0)
    parser.add_argument("--top-n-volume", type=int, default=80)
    parser.add_argument("--lookback-seconds", type=float, default=900.0)
    parser.add_argument("--trade-count", type=int, default=1000)
    parser.add_argument("--spacing-bps", type=float, default=60.0)
    parser.add_argument("--levels", type=int, default=5)
    parser.add_argument("--entry-offset-mult", type=float, default=0.0)
    parser.add_argument("--initial-capital", type=float, default=50.0)
    parser.add_argument("--max-spread-bps", type=float, default=30.0)
    parser.add_argument("--min-recent-trades", type=int, default=3)
    parser.add_argument("--max-roundtrip-seconds", type=float, default=180.0)
    parser.add_argument("--max-signal-age-seconds", type=float, default=90.0)
    parser.add_argument("--min-depth-usd", type=float, default=1.0)
    parser.add_argument("--trade-volume-participation", type=float, default=1.0)
    parser.add_argument("--trade-lookback-seconds", type=float, default=5.0)
    parser.add_argument("--shadow-duration-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--depth-count", type=int, default=20)
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--summary-path", default=str(DEFAULT_SUMMARY_PATH))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    event_path = Path(args.event_path)
    summary_path = Path(args.summary_path)
    summary: dict[str, Any] = {
        "started_at": utc_now_iso(),
        "cycles_requested": int(args.cycles),
        "cycles_completed": 0,
        "fire_cycles": 0,
        "shadow_runs": [],
        "dry_run": bool(args.dry_run),
    }
    append_jsonl(event_path, {"event": "router_shadow_cycle_start", "ts": utc_now_iso(), "dry_run": bool(args.dry_run)})

    for cycle in range(1, max(1, int(args.cycles)) + 1):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        router_json = REPORTS / f"kraken_grid_router_cycle_{stamp}_{cycle}.json"
        router_md = REPORTS / f"kraken_grid_router_cycle_{stamp}_{cycle}.md"
        router_cmd = build_router_command(args, json_path=router_json, md_path=router_md)
        append_jsonl(event_path, {"event": "router_scan_start", "ts": utc_now_iso(), "cycle": cycle, "router_json": str(router_json)})
        if args.dry_run:
            append_jsonl(event_path, {"event": "router_scan_dry_run", "ts": utc_now_iso(), "cycle": cycle, "cmd": router_cmd})
            break
        subprocess.run(router_cmd, cwd=str(ROOT), check=True)
        router_payload = json.loads(router_json.read_text(encoding="utf-8"))
        candidate = best_fire_candidate(router_payload)
        if candidate is None:
            append_jsonl(
                event_path,
                {
                    "event": "router_no_fire_candidate",
                    "ts": utc_now_iso(),
                    "cycle": cycle,
                    "rows_scored": router_payload.get("rows_scored"),
                    "best_product": router_payload.get("best_product"),
                },
            )
            summary["cycles_completed"] = cycle
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            time.sleep(max(0.0, float(args.sleep_between_cycles)))
            continue

        summary["fire_cycles"] += 1
        product_id = str(candidate["product_id"])
        shadow_events = REPORTS / f"kraken_grid_router_shadow_{product_id.replace('-', '_').lower()}_{stamp}_{cycle}_events.jsonl"
        shadow_summary = REPORTS / f"kraken_grid_router_shadow_{product_id.replace('-', '_').lower()}_{stamp}_{cycle}_summary.json"
        shadow_cmd = build_shadow_command(args, product_id=product_id, event_path=shadow_events, summary_path=shadow_summary)
        append_jsonl(
            event_path,
            {
                "event": "router_fire_candidate",
                "ts": utc_now_iso(),
                "cycle": cycle,
                "product_id": product_id,
                "spread_bps": candidate.get("spread_bps"),
                "roundtrip_seconds_to_exit": candidate.get("roundtrip_seconds_to_exit"),
                "shadow_summary": str(shadow_summary),
            },
        )
        subprocess.run(shadow_cmd, cwd=str(ROOT), check=True)
        run_summary = json.loads(shadow_summary.read_text(encoding="utf-8")) if shadow_summary.exists() else {}
        summary["shadow_runs"].append(
            {
                "cycle": cycle,
                "product_id": product_id,
                "router_candidate": candidate,
                "shadow_summary_path": str(shadow_summary),
                "shadow_event_path": str(shadow_events),
                "shadow_summary": run_summary,
            }
        )
        summary["cycles_completed"] = cycle
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        if cycle < int(args.cycles):
            time.sleep(max(0.0, float(args.sleep_between_cycles)))

    append_jsonl(event_path, {"event": "router_shadow_cycle_stop", "ts": utc_now_iso()})
    summary["finished_at"] = utc_now_iso()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
