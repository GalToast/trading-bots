#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import build_coinbase_burst_shadow_scoreboard as burst_scoreboard


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
REPORTS = ROOT / "reports"
CSV_PATH = REPORTS / "coinbase_burst_product_contributions.csv"
MD_PATH = REPORTS / "coinbase_burst_product_contributions.md"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_event_records(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text or not text.startswith("{"):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_lanes(registry_path: Path) -> list[dict[str, Any]]:
    payload = load_json(registry_path)
    lanes = payload if isinstance(payload, list) else payload.get("lanes", [])
    results: list[dict[str, Any]] = []
    for lane in lanes:
        if str(lane.get("kind") or "") != "shadow_coinbase_spot":
            continue
        name = str(lane.get("name") or "")
        if not name.startswith("shadow_coinbase_burst_"):
            continue
        event_path = str(lane.get("event_path") or "").strip()
        if not event_path:
            continue
        results.append(lane)
    return results


def aggregate_rows(*, registry_path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    repo_root = registry_path.resolve().parent.parent
    lanes = load_lanes(registry_path)
    aggregates: dict[tuple[str, str], dict[str, Any]] = {}

    for lane in lanes:
        lane_name = str(lane.get("name") or "")
        style = burst_scoreboard.lane_style(lane_name)
        event_path = repo_root / str(lane.get("event_path") or "")
        for record in iter_event_records(event_path):
            action = str(record.get("action") or "")
            if action not in {"close_target", "close_stop"}:
                continue
            product_id = str(record.get("product") or "").strip().upper()
            if not product_id:
                continue
            key = (lane_name, product_id)
            row = aggregates.setdefault(
                key,
                {
                    "lane_name": lane_name,
                    "style": style,
                    "product_id": product_id,
                    "close_events": 0,
                    "wins": 0,
                    "losses": 0,
                    "realized_net_usd": 0.0,
                    "fees_usd": 0.0,
                    "avg_burst_range_pct": 0.0,
                    "burst_samples": 0,
                },
            )
            row["close_events"] += 1
            row["wins"] += 1 if action == "close_target" else 0
            row["losses"] += 1 if action == "close_stop" else 0
            row["realized_net_usd"] += to_float(record.get("net"))
            row["fees_usd"] += to_float(record.get("fees"))
            burst_range = record.get("burst_range", record.get("range_pct"))
            if burst_range not in (None, ""):
                row["avg_burst_range_pct"] += to_float(burst_range)
                row["burst_samples"] += 1

    rows: list[dict[str, Any]] = []
    product_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "lane_name": "ALL_BURST_LANES",
            "style": "total",
            "product_id": "",
            "close_events": 0,
            "wins": 0,
            "losses": 0,
            "realized_net_usd": 0.0,
            "fees_usd": 0.0,
            "avg_burst_range_pct": 0.0,
            "burst_samples": 0,
        }
    )

    for row in aggregates.values():
        burst_samples = int(row["burst_samples"])
        avg_burst = row["avg_burst_range_pct"] / burst_samples if burst_samples else 0.0
        close_events = int(row["close_events"])
        wins = int(row["wins"])
        normalized = {
            "lane_name": str(row["lane_name"]),
            "style": str(row["style"]),
            "product_id": str(row["product_id"]),
            "close_events": close_events,
            "wins": wins,
            "losses": int(row["losses"]),
            "win_rate": round((wins / close_events) * 100.0, 2) if close_events else 0.0,
            "realized_net_usd": round(float(row["realized_net_usd"]), 4),
            "avg_pnl_per_close": round(float(row["realized_net_usd"]) / close_events, 4) if close_events else 0.0,
            "fees_usd": round(float(row["fees_usd"]), 4),
            "avg_burst_range_pct": round(avg_burst, 4),
        }
        rows.append(normalized)

        total = product_totals[normalized["product_id"]]
        total["product_id"] = normalized["product_id"]
        total["close_events"] += normalized["close_events"]
        total["wins"] += normalized["wins"]
        total["losses"] += normalized["losses"]
        total["realized_net_usd"] += normalized["realized_net_usd"]
        total["fees_usd"] += normalized["fees_usd"]
        total["avg_burst_range_pct"] += normalized["avg_burst_range_pct"] * close_events
        total["burst_samples"] += close_events

    total_rows: list[dict[str, Any]] = []
    for product_id, row in product_totals.items():
        close_events = int(row["close_events"])
        wins = int(row["wins"])
        total_rows.append(
            {
                "lane_name": "ALL_BURST_LANES",
                "style": "total",
                "product_id": product_id,
                "close_events": close_events,
                "wins": wins,
                "losses": int(row["losses"]),
                "win_rate": round((wins / close_events) * 100.0, 2) if close_events else 0.0,
                "realized_net_usd": round(float(row["realized_net_usd"]), 4),
                "avg_pnl_per_close": round(float(row["realized_net_usd"]) / close_events, 4) if close_events else 0.0,
                "fees_usd": round(float(row["fees_usd"]), 4),
                "avg_burst_range_pct": round(float(row["avg_burst_range_pct"]) / close_events, 4) if close_events else 0.0,
            }
        )

    total_rows.sort(key=lambda row: (-float(row["realized_net_usd"]), -int(row["close_events"]), str(row["product_id"])))
    rows.sort(key=lambda row: (-float(row["realized_net_usd"]), -int(row["close_events"]), str(row["lane_name"]), str(row["product_id"])))
    return total_rows + rows


def write_reports(rows: list[dict[str, Any]], *, csv_path: Path = CSV_PATH, md_path: Path = MD_PATH) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Burst Product Contributions",
        "",
        "| Lane | Style | Product | Closes | Wins | Losses | WR % | Net $ | Avg/Close $ | Fees $ | Avg Burst % |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {lane_name} | {style} | {product_id} | {close_events} | {wins} | {losses} | {win_rate:.2f} | "
            "{realized_net_usd:.4f} | {avg_pnl_per_close:.4f} | {fees_usd:.4f} | {avg_burst_range_pct:.4f} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = aggregate_rows()
    if not rows:
        raise SystemExit("no burst close events found")
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows[:20]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
