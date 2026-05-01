#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_kraken_maker_post_close_ghost_review as ghost_review

DEFAULT_JSON_PATH = REPORTS / "kraken_maker_ab_ghost_giveback_board.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_ab_ghost_giveback_board.md"

LANES = [
    {
        "lane": "baseline",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl",
    },
    {
        "lane": "cooldown_only",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ab_events.jsonl",
    },
    {
        "lane": "parallel_only",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ab_events.jsonl",
    },
    {
        "lane": "parallel_cooldown",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_cooldown_ab_events.jsonl",
    },
    {
        "lane": "cooldown_size12",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_size12_ab_events.jsonl",
    },
    {
        "lane": "cooldown_ratio50",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ratio50_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25_hold45",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_hold45_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds50_fastbank",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds50_fastbank_ab_events.jsonl",
    },
    {
        "lane": "cooldown_ratio50_size12",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ratio50_size12_ab_events.jsonl",
    },
]


def lane_verdict(summary: dict[str, Any]) -> str:
    marks = int(summary.get("ghost_marks") or summary.get("marks") or 0)
    avg_delta = float(summary.get("avg_delta_net") or 0.0)
    improved_rate = float(summary.get("improved_rate") or 0.0)
    if marks <= 0:
        return "collect_no_ghost_marks"
    if avg_delta < 0 and improved_rate <= 0.25:
        return "banking_supported"
    if avg_delta > 0 and improved_rate >= 0.50:
        return "hold_longer_candidate"
    return "mixed_collect_more"


def build_payload(lanes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = []
    for lane in (LANES if lanes is None else lanes):
        payload = ghost_review.build_payload(Path(lane["events_path"]))
        summary = payload.get("summary") or {}
        rows.append(
            {
                "lane": str(lane["lane"]),
                "events_path": str(lane["events_path"]),
                "ghost_marks": int(summary.get("ghost_marks") or 0),
                "ghost_misses": int(summary.get("ghost_misses") or 0),
                "avg_delta_net": float(summary.get("avg_delta_net") or 0.0),
                "avg_delta_net_pct": float(summary.get("avg_delta_net_pct") or 0.0),
                "improved_marks": int(summary.get("improved_marks") or 0),
                "worsened_marks": int(summary.get("worsened_marks") or 0),
                "improved_rate": float(summary.get("improved_rate") or 0.0),
                "best_delta_net": float(summary.get("best_delta_net") or 0.0),
                "worst_delta_net": float(summary.get("worst_delta_net") or 0.0),
                "verdict": lane_verdict(summary),
                "by_horizon": payload.get("by_horizon") or [],
            }
        )
    return {
        "generated_at": ghost_review.utc_now_iso(),
        "mode": "kraken_maker_ab_ghost_giveback_board",
        "summary": {
            "lanes": len(rows),
            "lanes_with_marks": sum(1 for row in rows if int(row["ghost_marks"]) > 0),
            "banking_supported_lanes": [
                row["lane"] for row in rows if row["verdict"] == "banking_supported"
            ],
            "hold_longer_candidate_lanes": [
                row["lane"] for row in rows if row["verdict"] == "hold_longer_candidate"
            ],
            "read": (
                "Negative ghost delta means holding after the actual close would have been worse under "
                "bid/taker liquidation; positive ghost delta means the exit may have banked too early."
            ),
        },
        "lanes": rows,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Maker A/B Ghost Giveback Board",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Lanes with marks: `{summary.get('lanes_with_marks')}` / `{summary.get('lanes')}`",
        f"- Banking-supported lanes: `{summary.get('banking_supported_lanes')}`",
        f"- Hold-longer candidates: `{summary.get('hold_longer_candidate_lanes')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Lanes",
        "",
        "| Lane | Verdict | Marks | Misses | Avg Delta $ | Avg Delta % | Improved | Worsened | Improved Rate | Best $ | Worst $ |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload.get("lanes") or []:
        lines.append(
            "| {lane} | {verdict} | {ghost_marks} | {ghost_misses} | {avg_delta_net:.6f} | {avg_delta_net_pct:.4f} | {improved_marks} | {worsened_marks} | {improved_rate:.2%} | {best_delta_net:.6f} | {worst_delta_net:.6f} |".format(
                **row
            )
        )
    lines.extend(["", "## By Horizon", ""])
    for row in payload.get("lanes") or []:
        lines.append(f"### {row['lane']}")
        if not row.get("by_horizon"):
            lines.append("")
            lines.append("- No ghost marks yet.")
            lines.append("")
            continue
        lines.extend(
            [
                "",
                "| Horizon | Marks | Avg Delta $ | Avg Delta % | Improved | Worsened |",
                "| ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for horizon in row.get("by_horizon") or []:
            lines.append(
                "| {horizon_seconds} | {marks} | {avg_delta_net:.6f} | {avg_delta_net_pct:.4f} | {improved_marks} | {worsened_marks} |".format(
                    **horizon
                )
            )
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare post-close ghost giveback across Kraken maker A/B lanes.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload()
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": str(Path(args.md_path).resolve())}, indent=2))


if __name__ == "__main__":
    main()
