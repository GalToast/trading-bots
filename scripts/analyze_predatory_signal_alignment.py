#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVENT_PATH = ROOT / "reports" / "predatory_shadow_monitor_events.jsonl"
DEFAULT_SYNC_PATH = ROOT / "reports" / "spot_microstructure_sync.jsonl"
DEFAULT_JSON_PATH = ROOT / "reports" / "predatory_signal_alignment.json"
DEFAULT_MD_PATH = ROOT / "reports" / "predatory_signal_alignment.md"
TRACKED_ACTIONS = {
    "iceberg_buy_reload_detected": 1,
    "iceberg_sell_reload_detected": -1,
    "fake_floor_pull_detected": -1,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze predatory-signal alignment against sync capture data")
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--sync-path", default=str(DEFAULT_SYNC_PATH))
    parser.add_argument("--follow-seconds", type=float, default=8.0)
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def ts_seconds(ts: str | None) -> float | None:
    parsed = parse_iso(ts)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).timestamp()


def sync_mid(row: dict[str, Any], product_id: str) -> float | None:
    coinbase = row.get("coinbase") or {}
    product = coinbase.get(product_id) or {}
    mid = product.get("mid")
    if mid is None:
        return None
    try:
        return float(mid)
    except Exception:
        return None


def event_direction(action: str) -> int:
    return int(TRACKED_ACTIONS.get(action) or 0)


def align_events(
    events: list[dict[str, Any]],
    sync_rows: list[dict[str, Any]],
    *,
    follow_seconds: float,
) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    tracked_products: set[str] = set()
    for row in sync_rows:
        ts = float(row.get("ts_epoch") or 0.0)
        if ts <= 0:
            ts = float(ts_seconds(str(row.get("ts_utc") or "")) or 0.0)
        if ts <= 0:
            continue
        coinbase = row.get("coinbase") or {}
        if isinstance(coinbase, dict):
            tracked_products.update(str(product_id).upper() for product_id in coinbase)
        normalized_rows.append({"ts": ts, "row": row})
    normalized_rows.sort(key=lambda item: item["ts"])

    alignments: list[dict[str, Any]] = []
    skipped_untracked = 0
    skipped_unaligned = 0

    for event in events:
        action = str(event.get("action") or "")
        if action not in TRACKED_ACTIONS:
            continue
        product_id = str(event.get("product_id") or "").upper()
        event_ts = float(ts_seconds(str(event.get("ts_utc") or "")) or 0.0)
        if not product_id or event_ts <= 0:
            skipped_unaligned += 1
            continue
        if product_id not in tracked_products:
            skipped_untracked += 1
            continue

        base: dict[str, Any] | None = None
        follow: dict[str, Any] | None = None
        for item in normalized_rows:
            row_ts = float(item["ts"])
            row = item["row"]
            if row_ts <= event_ts and sync_mid(row, product_id) is not None:
                base = item
            if row_ts >= event_ts and row_ts <= event_ts + follow_seconds and sync_mid(row, product_id) is not None:
                follow = item

        if base is None or follow is None:
            skipped_unaligned += 1
            continue

        base_row = base["row"]
        follow_row = follow["row"]
        base_mid = float(sync_mid(base_row, product_id) or 0.0)
        follow_mid = float(sync_mid(follow_row, product_id) or 0.0)
        if base_mid <= 0 or follow_mid <= 0:
            skipped_unaligned += 1
            continue

        delta = follow_mid - base_mid
        delta_bps = (delta / base_mid * 10000.0) if base_mid else 0.0
        expected = event_direction(action)
        direction_match = (delta > 0 and expected > 0) or (delta < 0 and expected < 0)
        btc_base_mid = sync_mid(base_row, "BTC-USD")
        btc_follow_mid = sync_mid(follow_row, "BTC-USD")

        alignments.append(
            {
                "ts_utc": str(event.get("ts_utc") or ""),
                "action": action,
                "product_id": product_id,
                "expected_direction": expected,
                "base_ts_utc": str(base_row.get("ts_utc") or ""),
                "follow_ts_utc": str(follow_row.get("ts_utc") or ""),
                "follow_seconds": round(float(follow["ts"]) - float(base["ts"]), 3),
                "base_mid": round(base_mid, 10),
                "follow_mid": round(follow_mid, 10),
                "delta_mid": round(delta, 10),
                "delta_bps": round(delta_bps, 4),
                "btc_delta_usd": round(float((btc_follow_mid or 0.0) - (btc_base_mid or 0.0)), 4),
                "direction_match": direction_match,
            }
        )

    by_action: dict[str, dict[str, Any]] = {}
    by_product_action: dict[str, dict[str, Any]] = {}

    grouped_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_product_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in alignments:
        grouped_action[row["action"]].append(row)
        grouped_product_action[f"{row['product_id']}::{row['action']}"].append(row)

    for action, rows in sorted(grouped_action.items()):
        matches = sum(1 for row in rows if row["direction_match"])
        by_action[action] = {
            "count": len(rows),
            "matches": matches,
            "match_rate_pct": round(matches / len(rows) * 100.0, 2) if rows else 0.0,
            "avg_delta_bps": round(mean(float(row["delta_bps"]) for row in rows), 4) if rows else 0.0,
            "avg_btc_delta_usd": round(mean(float(row["btc_delta_usd"]) for row in rows), 4) if rows else 0.0,
        }

    for key, rows in sorted(grouped_product_action.items()):
        matches = sum(1 for row in rows if row["direction_match"])
        product_id, action = key.split("::", 1)
        by_product_action[key] = {
            "product_id": product_id,
            "action": action,
            "count": len(rows),
            "matches": matches,
            "match_rate_pct": round(matches / len(rows) * 100.0, 2) if rows else 0.0,
            "avg_delta_bps": round(mean(float(row["delta_bps"]) for row in rows), 4) if rows else 0.0,
        }

    return {
        "follow_seconds": float(follow_seconds),
        "tracked_actions": sorted(TRACKED_ACTIONS),
        "tracked_products_in_sync": sorted(tracked_products),
        "input_event_rows": len(events),
        "signal_event_rows": sum(1 for row in events if str(row.get("action") or "") in TRACKED_ACTIONS),
        "aligned_event_rows": len(alignments),
        "skipped_untracked_product_rows": skipped_untracked,
        "skipped_unaligned_rows": skipped_unaligned,
        "by_action": by_action,
        "by_product_action": by_product_action,
        "recent_alignments": alignments[-15:],
    }


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Predatory Signal Alignment",
        "",
        f"- Event input: `{payload['event_path']}`",
        f"- Sync input: `{payload['sync_path']}`",
        f"- Follow window seconds: `{payload['analysis']['follow_seconds']}`",
        "",
        "## Summary",
        "",
        f"- Signal event rows: `{payload['analysis']['signal_event_rows']}`",
        f"- Aligned rows: `{payload['analysis']['aligned_event_rows']}`",
        f"- Skipped untracked-product rows: `{payload['analysis']['skipped_untracked_product_rows']}`",
        f"- Skipped unaligned rows: `{payload['analysis']['skipped_unaligned_rows']}`",
        "",
        "## By Action",
        "",
        "| Action | Count | Matches | Match Rate % | Avg Delta bps | Avg BTC Delta USD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for action, summary in payload["analysis"]["by_action"].items():
        lines.append(
            f"| {action} | {summary['count']} | {summary['matches']} | {summary['match_rate_pct']} | {summary['avg_delta_bps']} | {summary['avg_btc_delta_usd']} |"
        )
    lines.extend(
        [
            "",
            "## By Product/Action",
            "",
            "| Product | Action | Count | Matches | Match Rate % | Avg Delta bps |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for summary in payload["analysis"]["by_product_action"].values():
        lines.append(
            f"| {summary['product_id']} | {summary['action']} | {summary['count']} | {summary['matches']} | {summary['match_rate_pct']} | {summary['avg_delta_bps']} |"
        )
    lines.extend(
        [
            "",
            "## Recent Alignments",
            "",
            "| TS UTC | Product | Action | Delta bps | Match | BTC Delta USD |",
            "|---|---|---|---:|---|---:|",
        ]
    )
    for row in payload["analysis"]["recent_alignments"]:
        lines.append(
            f"| {row['ts_utc']} | {row['product_id']} | {row['action']} | {row['delta_bps']} | {row['direction_match']} | {row['btc_delta_usd']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    event_path = Path(args.event_path)
    sync_path = Path(args.sync_path)
    payload = {
        "event_path": str(event_path),
        "sync_path": str(sync_path),
        "analysis": align_events(load_jsonl(event_path), load_jsonl(sync_path), follow_seconds=float(args.follow_seconds)),
    }
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    Path(args.md_path).write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(payload["analysis"]["by_action"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
