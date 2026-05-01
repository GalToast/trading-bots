#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_prebreak_experiment_queue.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_prebreak_experiment_queue.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_prebreak_experiment_queue.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Kraken pre-break compression experiment queue.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--limit", type=int, default=5000)
    return parser.parse_args()


def windows_for(name: str) -> list[str]:
    return {
        "last_30": ["last", "30s"],
        "30_60": ["30s", "60s"],
        "last_30_60": ["last", "30s", "60s"],
    }.get(str(name), ["last", "30s"])


def build_experiments(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    idx = 0
    modes = ["prebreak_compression", "first_lift_after_flat", "compression_pop"]
    window_sets = ["last_30", "30_60", "last_30_60"]
    max_spreads = [10, 15, 20, 30, 40, 50]
    max_chases = [40, 60, 80, 100, 125, 150, 200]
    min_lifts = [10, 15, 20, 25, 35, 50]
    max_abs_5m_values = [15, 25, 35, 50, 75, 100]
    max_abs_15m_values = [50, 100, 150, 250, 999999]
    min_edges = [-250, -200, -150, -100, -50, 0, 25, 50]
    horizons = [180, 300, 600]
    dedupes = [180, 300, 600, 900]
    min_samples_values = [30, 60, 120, 200]
    min_sample_index_values = [5, 10, 20, 30]
    per_mode_limit = max(1, int(limit) // len(modes))
    for mode_index, mode in enumerate(modes):
        mode_count = 0
        for max_spread_bps in max_spreads:
            for max_chase_bps in max_chases:
                for min_rebound_bps in min_lifts:
                    if min_rebound_bps >= max_chase_bps:
                        continue
                    for max_abs_5m_bps in max_abs_5m_values:
                        for max_abs_15m_bps in max_abs_15m_values:
                            for entry_window_set in window_sets:
                                for min_edge_bps in min_edges:
                                    for hold_horizon_seconds in horizons:
                                        for dedupe_seconds in dedupes:
                                            for min_samples in min_samples_values:
                                                for min_sample_index in min_sample_index_values:
                                                    if len(rows) >= max(1, int(limit)):
                                                        return rows
                                                    if mode_index < len(modes) - 1 and mode_count >= per_mode_limit:
                                                        continue
                                                    idx += 1
                                                    row = {
                                                        "experiment_id": f"kraken_spot_prebreak_{idx:05d}",
                                                        "mode": mode,
                                                        "entry_window_set": entry_window_set,
                                                        "windows": windows_for(entry_window_set),
                                                        "signal_states": ["building", "live_hot"],
                                                        "min_edge_bps": min_edge_bps,
                                                        "max_spread_bps": max_spread_bps,
                                                        "max_chase_bps": max_chase_bps,
                                                        "min_rebound_bps": min_rebound_bps,
                                                        "min_dump_5m_bps": max_abs_5m_bps,
                                                        "max_abs_5m_bps": max_abs_5m_bps,
                                                        "max_abs_15m_bps": max_abs_15m_bps,
                                                        "min_samples": min_samples,
                                                        "min_sample_index": min_sample_index,
                                                        "hold_horizon_seconds": hold_horizon_seconds,
                                                        "dedupe_seconds": dedupe_seconds,
                                                        "fee_model": "ask_entry_bid_exit_80bps_round_trip",
                                                        "data_surface": "kraken_full_usd_usdc_usdt_radar_cache",
                                                        "promotion_gate": ">=30_entries_>=5_products_>=50pct_win_positive_fixed_horizon_avg",
                                                        "kill_condition": "negative_fixed_horizon_avg_or_under_50pct_win_or_under_30_marked_entries",
                                                    }
                                                    rows.append(row)
                                                    mode_count += 1
    return rows[: max(1, int(limit))]


def build(args: argparse.Namespace) -> dict[str, Any]:
    rows = build_experiments(int(args.limit))
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_prebreak_experiment_queue",
        "shadow_only": True,
        "count": len(rows),
        "read": [
            "This queue targets pre-break compression plus first lift instead of already-hot chase entries.",
            "It is designed after the guarded forward-tape autopsy showed oracle-best exits are still negative on the existing hot filter.",
            "Promotion requires broad fixed-horizon evidence; positive average from tiny low-win samples is not enough.",
        ],
        "rows": rows,
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = payload.get("rows") or []
    columns = [
        "experiment_id",
        "mode",
        "entry_window_set",
        "windows",
        "signal_states",
        "min_edge_bps",
        "max_spread_bps",
        "max_chase_bps",
        "min_rebound_bps",
        "max_abs_5m_bps",
        "max_abs_15m_bps",
        "min_samples",
        "min_sample_index",
        "hold_horizon_seconds",
        "dedupe_seconds",
        "promotion_gate",
        "kill_condition",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: json.dumps(row.get(column)) if isinstance(row.get(column), list) else row.get(column, "") for column in columns})
    by_mode: dict[str, int] = {}
    for row in rows:
        by_mode[str(row.get("mode"))] = by_mode.get(str(row.get("mode")), 0) + 1
    lines = [
        "# Kraken Spot Pre-Break Experiment Queue",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Experiments: `{payload.get('count')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(["", "## Mode Mix", "", "| Mode | Count |", "| --- | ---: |"])
    for mode, count in sorted(by_mode.items()):
        lines.append(f"| {mode} | {count} |")
    lines.extend(["", "## First 25", "", "| ID | Mode | Windows | Spread | Chase | 5m Abs | Horizon |", "| --- | --- | --- | ---: | ---: | ---: | ---: |"])
    for row in rows[:25]:
        lines.append(
            "| {experiment_id} | {mode} | {entry_window_set} | {max_spread_bps} | {max_chase_bps} | {max_abs_5m_bps} | {hold_horizon_seconds} |".format(
                **row
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build(parse_args())
    print(json.dumps({"json_path": str(DEFAULT_JSON_PATH.resolve()), "md_path": str(DEFAULT_MD_PATH.resolve()), "count": payload["count"]}, indent=2))


if __name__ == "__main__":
    main()
