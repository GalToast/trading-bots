#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCOREBOARD_CSV = ROOT / "reports" / "coinbase_spot_rsi_scoreboard.csv"
CSV_PATH = ROOT / "reports" / "coinbase_spot_rsi_forward_review.csv"
MD_PATH = ROOT / "reports" / "coinbase_spot_rsi_forward_review.md"


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def classify_forward_row(row: dict[str, str]) -> tuple[str, str]:
    product = str(row.get("product_id") or "")
    if product == "TOTAL":
        return "pack_total", str(row.get("note") or "")

    closes = int(float(row.get("realized_closes") or 0))
    realized = float(row.get("realized_net_usd") or 0.0)
    in_position = int(float(row.get("in_position") or 0))

    if closes < 5:
        if realized > 0:
            return "bootstrap_positive", "too few closes for a forward verdict"
        if realized < 0:
            return "bootstrap_negative", "too few closes for a forward verdict"
        return "bootstrap_flat", "too few closes for a forward verdict"

    if realized > 0:
        if in_position:
            return "holding_up_in_position", "enough closes and currently positive with live inventory"
        return "holding_up", "enough closes and forward realized is positive"
    if realized < 0:
        if in_position:
            return "lagging_in_position", "enough closes and forward realized is negative with live inventory"
        return "lagging", "enough closes and forward realized is negative"
    return "flat", "enough closes but no net realized edge yet"


def build_rows(scoreboard_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in scoreboard_rows:
        forward_status, forward_note = classify_forward_row(row)
        baseline = float(row.get("baseline_72h_net_usd") or 0.0)
        realized = float(row.get("realized_net_usd") or 0.0)
        gap = realized - baseline if row.get("product_id") != "TOTAL" else realized - baseline
        ratio = ""
        if baseline > 0:
            ratio = round(realized / baseline, 4)
        rows.append(
            {
                "product_id": str(row.get("product_id") or ""),
                "lane_name": str(row.get("lane_name") or ""),
                "readiness_verdict": str(row.get("readiness_verdict") or ""),
                "forward_status": forward_status,
                "baseline_72h_net_usd": round(baseline, 4),
                "realized_net_usd": round(realized, 4),
                "forward_vs_baseline_usd": round(gap, 4),
                "realized_to_baseline_ratio": ratio,
                "realized_closes": int(float(row.get("realized_closes") or 0)),
                "in_position": int(float(row.get("in_position") or 0)),
                "cash_usd": round(float(row.get("cash_usd") or 0.0), 2),
                "heartbeat_age_seconds": row.get("heartbeat_age_seconds") or "",
                "forward_note": forward_note,
            }
        )

    status_rank = {
        "holding_up": 0,
        "holding_up_in_position": 1,
        "bootstrap_positive": 2,
        "bootstrap_flat": 3,
        "bootstrap_negative": 4,
        "lagging": 5,
        "lagging_in_position": 6,
        "flat": 7,
        "pack_total": 8,
    }
    rows.sort(key=lambda row: (status_rank.get(str(row["forward_status"]), 99), -float(row["realized_net_usd"])))
    return rows


def write_reports(rows: list[dict[str, Any]], *, csv_path: Path = CSV_PATH, md_path: Path = MD_PATH) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Spot RSI Forward Review",
        "",
        "| Product | Lane | Readiness | Forward Status | Baseline 72h $ | Realized $ | Delta vs Baseline $ | Ratio | Closes | In Pos | Cash $ | Heartbeat Age (s) | Note |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        ratio = row["realized_to_baseline_ratio"] if row["realized_to_baseline_ratio"] != "" else "-"
        lines.append(
            "| {product_id} | {lane_name} | {readiness_verdict} | {forward_status} | "
            "{baseline_72h_net_usd:.4f} | {realized_net_usd:.4f} | {forward_vs_baseline_usd:.4f} | "
            "{ratio} | {realized_closes} | {in_position} | {cash_usd:.2f} | {heartbeat_age_seconds} | {forward_note} |".format(
                ratio=ratio,
                **row,
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows(load_rows(SCOREBOARD_CSV))
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
