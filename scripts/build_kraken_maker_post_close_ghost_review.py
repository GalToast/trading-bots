#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_post_close_ghost_review.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_post_close_ghost_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "marks": 0,
            "avg_delta_net": 0.0,
            "avg_delta_net_pct": 0.0,
            "improved_marks": 0,
            "worsened_marks": 0,
            "flat_marks": 0,
            "improved_rate": 0.0,
            "best_delta_net": 0.0,
            "worst_delta_net": 0.0,
        }
    deltas = [to_float(row.get("delta_net_vs_actual")) for row in rows]
    pct_deltas = [to_float(row.get("delta_net_pct_vs_actual")) for row in rows]
    improved = [delta for delta in deltas if delta > 0.000001]
    worsened = [delta for delta in deltas if delta < -0.000001]
    flat = len(rows) - len(improved) - len(worsened)
    return {
        "marks": len(rows),
        "avg_delta_net": round(sum(deltas) / len(deltas), 6),
        "avg_delta_net_pct": round(sum(pct_deltas) / len(pct_deltas), 6),
        "improved_marks": len(improved),
        "worsened_marks": len(worsened),
        "flat_marks": flat,
        "improved_rate": round(len(improved) / len(rows), 6),
        "best_delta_net": round(max(deltas), 6),
        "worst_delta_net": round(min(deltas), 6),
    }


def build_payload(events_path: Path) -> dict[str, Any]:
    events = load_events(events_path)
    marks = [event for event in events if event.get("action") == "post_close_ghost_mark"]
    misses = [event for event in events if event.get("action") == "post_close_ghost_miss"]
    by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mark in marks:
        by_horizon[int(to_float(mark.get("horizon_seconds")))].append(mark)
        by_reason[str(mark.get("close_reason") or "")].append(mark)
    horizon_summaries = [
        {"horizon_seconds": horizon, **summarize_rows(rows)}
        for horizon, rows in sorted(by_horizon.items())
    ]
    reason_summaries = [
        {"close_reason": reason, **summarize_rows(rows)}
        for reason, rows in sorted(by_reason.items())
    ]
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_post_close_ghost_review",
        "parameters": {
            "events_path": str(events_path),
            "mark_basis": "Conservative post-close ghost marks use bid exit and 40bps taker fee to compare held-to-horizon net against actual close net.",
        },
        "summary": {
            "ghost_marks": len(marks),
            "ghost_misses": len(misses),
            **summarize_rows(marks),
        },
        "by_horizon": horizon_summaries,
        "by_close_reason": reason_summaries,
        "recent_marks": marks[-20:],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Kraken Maker Post-Close Ghost Review",
        "",
        "## Summary",
        "",
        f"- Ghost marks: `{payload['summary']['ghost_marks']}`",
        f"- Ghost misses: `{payload['summary']['ghost_misses']}`",
        f"- Avg delta vs actual: `${payload['summary']['avg_delta_net']:.6f}`",
        f"- Improved marks: `{payload['summary']['improved_marks']}`",
        f"- Worsened marks: `{payload['summary']['worsened_marks']}`",
        f"- Improved rate: `{payload['summary']['improved_rate']:.2%}`",
        "",
        "## By Horizon",
        "",
        "| Horizon | Marks | Avg Delta $ | Avg Delta % | Improved | Worsened | Best $ | Worst $ |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["by_horizon"]:
        lines.append(
            "| {horizon_seconds} | {marks} | {avg_delta_net:.6f} | {avg_delta_net_pct:.4f} | {improved_marks} | {worsened_marks} | {best_delta_net:.6f} | {worst_delta_net:.6f} |".format(
                **row
            )
        )
    lines.extend(["", "## By Close Reason", ""])
    lines.extend(
        [
            "| Reason | Marks | Avg Delta $ | Improved | Worsened |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["by_close_reason"]:
        lines.append(
            "| {close_reason} | {marks} | {avg_delta_net:.6f} | {improved_marks} | {worsened_marks} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Kraken maker post-close ghost horizon marks.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(Path(args.events_path))
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": args.md_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
