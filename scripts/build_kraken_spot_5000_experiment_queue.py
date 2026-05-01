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
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_5000_experiment_queue.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_5000_experiment_queue.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_5000_experiment_queue.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a 5,000-experiment Kraken spot velocity queue.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--limit", type=int, default=5000)
    return parser.parse_args()


def experiment_axes() -> dict[str, list[Any]]:
    return {
        "mode": ["momentum", "dump_reclaim", "compression_pop", "pullback_after_hot", "anti_chase_reclaim"],
        "entry_window_set": ["last_30_60", "last_30", "30_60", "5m_only", "last_30_60_5m"],
        "min_edge_bps": [-999, 0, 25, 50, 75, 100, 150, 200],
        "max_spread_bps": [10, 20, 30, 50, 75, 100, 150],
        "max_chase_bps": [100, 150, 200, 250, 300, 450, 600],
        "min_rebound_bps": [10, 25, 50, 75, 100],
        "min_dump_5m_bps": [50, 75, 100, 150, 200, 300],
        "hold_horizon_seconds": [60, 180, 300, 600],
        "dedupe_seconds": [60, 180, 300, 600],
    }


def windows_for(name: str) -> list[str]:
    return {
        "last_30_60": ["last", "30s", "60s"],
        "last_30": ["last", "30s"],
        "30_60": ["30s", "60s"],
        "5m_only": ["5m"],
        "last_30_60_5m": ["last", "30s", "60s", "5m"],
    }.get(name, ["last", "30s", "60s"])


def build_experiments(limit: int) -> list[dict[str, Any]]:
    axes = experiment_axes()
    rows: list[dict[str, Any]] = []
    idx = 0
    mode_order = ["dump_reclaim", "anti_chase_reclaim", "compression_pop", "pullback_after_hot", "momentum"]
    spread_order = [10, 20, 30, 50, 75, 100, 150]
    chase_order = [100, 150, 200, 250, 300, 450, 600]
    horizon_order = [600, 300, 180, 60]
    per_mode_limit = max(1, int(limit) // len(mode_order))
    for mode in mode_order:
        mode_count = 0
        for max_spread_bps in spread_order:
            for max_chase_bps in chase_order:
                for hold_horizon_seconds in horizon_order:
                    for entry_window_set in axes["entry_window_set"]:
                        for min_edge_bps in axes["min_edge_bps"]:
                            for min_rebound_bps in axes["min_rebound_bps"]:
                                for min_dump_5m_bps in axes["min_dump_5m_bps"]:
                                    for dedupe_seconds in axes["dedupe_seconds"]:
                                        if len(rows) >= max(1, int(limit)):
                                            return rows
                                        if mode_count >= per_mode_limit and mode != mode_order[-1]:
                                            continue
                                        if mode == "momentum" and min_edge_bps < 0:
                                            continue
                                        if mode in {"dump_reclaim", "anti_chase_reclaim"} and min_dump_5m_bps < 75:
                                            continue
                                        if mode == "compression_pop" and max_spread_bps > 75:
                                            continue
                                        idx += 1
                                        raw = {
                                            "mode": mode,
                                            "entry_window_set": entry_window_set,
                                            "min_edge_bps": min_edge_bps,
                                            "max_spread_bps": max_spread_bps,
                                            "max_chase_bps": max_chase_bps,
                                            "min_rebound_bps": min_rebound_bps,
                                            "min_dump_5m_bps": min_dump_5m_bps,
                                            "hold_horizon_seconds": hold_horizon_seconds,
                                            "dedupe_seconds": dedupe_seconds,
                                        }
                                        raw["experiment_id"] = f"kraken_spot_exp_{idx:05d}"
                                        raw["windows"] = windows_for(str(raw["entry_window_set"]))
                                        raw["signal_states"] = ["live_hot"] if mode in {"momentum", "anti_chase_reclaim"} else ["live_hot", "building"]
                                        raw["fee_model"] = "ask_entry_bid_exit_80bps_round_trip"
                                        raw["data_surface"] = "kraken_full_usd_usdc_usdt_radar_cache"
                                        raw["promotion_gate"] = "positive_avg_net_at_horizon_and_positive_forward_tape_confirmation"
                                        raw["kill_condition"] = "negative_after_fee_avg_or_under_40pct_win_at_target_horizon"
                                        rows.append(raw)
                                        mode_count += 1
    return rows[: max(1, int(limit))]


def build(args: argparse.Namespace) -> dict[str, Any]:
    rows = build_experiments(int(args.limit))
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_5000_experiment_queue",
        "shadow_only": True,
        "count": len(rows),
        "read": [
            "This is the explicit 5,000-experiment Kraken spot queue.",
            "Experiments are spot-executable hypotheses over public bid/ask cache data; none imply live permission.",
            "Batch evaluation should prioritize dump/reclaim and anti-chase families because current momentum-cache evidence is negative.",
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
        "min_dump_5m_bps",
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
        "# Kraken Spot 5,000 Experiment Queue",
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
    lines.extend(["", "## First 25", "", "| ID | Mode | Windows | Spread | Chase | Horizon |", "| --- | --- | --- | ---: | ---: | ---: |"])
    for row in rows[:25]:
        lines.append(
            "| {experiment_id} | {mode} | {entry_window_set} | {max_spread_bps} | {max_chase_bps} | {hold_horizon_seconds} |".format(
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
