#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clean_forward_baselines import load_reset_baselines, reset_baseline_for_lane


ROOT = Path(__file__).resolve().parent.parent
SCOREBOARD_CSV = ROOT / "reports" / "coinbase_burst_shadow_scoreboard.csv"
BASELINE_JSON = ROOT / "reports" / "coinbase_burst_forward_baseline.json"
CSV_PATH = ROOT / "reports" / "coinbase_burst_forward_review.csv"
MD_PATH = ROOT / "reports" / "coinbase_burst_forward_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_baseline(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("lanes") if isinstance(payload, dict) else {}
    return rows if isinstance(rows, dict) else {}


def save_baseline(path: Path, baseline: dict[str, dict[str, Any]]) -> None:
    payload = {"updated_at": utc_now_iso(), "lanes": baseline}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def seed_missing_baselines(scoreboard_rows: list[dict[str, str]], baseline: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    updated = dict(baseline)
    seeded_at = utc_now_iso()
    for row in scoreboard_rows:
        lane_name = str(row.get("lane_name") or "")
        if not lane_name or lane_name == "TOTAL":
            continue
        if lane_name in updated:
            continue
        updated[lane_name] = {
            "seeded_at": seeded_at,
            "realized_net_usd": float(row.get("realized_net_usd") or 0.0),
            "closes": int(float(row.get("closes") or 0)),
            "wins": int(float(row.get("wins") or 0)),
            "losses": int(float(row.get("losses") or 0)),
        }
    return updated


def classify_forward_row(
    row: dict[str, str],
    baseline_row: dict[str, Any] | None,
    *,
    baseline_source: str = "seeded",
) -> tuple[str, str, float, int]:
    lane_name = str(row.get("lane_name") or "")
    if lane_name == "TOTAL":
        baseline_realized = float(baseline_row.get("realized_net_usd") or 0.0) if baseline_row else 0.0
        delta_realized = float(row.get("realized_net_usd") or 0.0) - baseline_realized
        return "pack_total", str(row.get("note") or ""), delta_realized, 0

    realized = float(row.get("realized_net_usd") or 0.0)
    closes = int(float(row.get("closes") or 0))
    open_count = int(float(row.get("open_count") or 0))
    if baseline_row is None:
        return "unseeded", "missing baseline snapshot", 0.0, 0

    baseline_note_prefix = "clean forward since stale-tick repair; " if baseline_source == "stale_tick_repair" else ""
    delta_realized = realized - float(baseline_row.get("realized_net_usd") or 0.0)
    delta_closes = closes - int(baseline_row.get("closes") or 0)

    if delta_closes < 5:
        if delta_realized > 0:
            return "seeded_positive", f"{baseline_note_prefix}too few new closes for a forward verdict", delta_realized, delta_closes
        if delta_realized < 0:
            return "seeded_negative", f"{baseline_note_prefix}too few new closes for a forward verdict", delta_realized, delta_closes
        return "seeded_flat", f"{baseline_note_prefix}too few new closes for a forward verdict", delta_realized, delta_closes

    if delta_realized > 0:
        if open_count:
            return "holding_up_in_position", f"{baseline_note_prefix}enough new closes and positive realized delta since supervised baseline", delta_realized, delta_closes
        return "holding_up", f"{baseline_note_prefix}enough new closes and positive realized delta since supervised baseline", delta_realized, delta_closes
    if delta_realized < 0:
        if open_count:
            return "lagging_in_position", f"{baseline_note_prefix}enough new closes and negative realized delta since supervised baseline", delta_realized, delta_closes
        return "lagging", f"{baseline_note_prefix}enough new closes and negative realized delta since supervised baseline", delta_realized, delta_closes
    return "flat", f"{baseline_note_prefix}enough new closes but no realized delta since supervised baseline", delta_realized, delta_closes


def build_rows(
    scoreboard_rows: list[dict[str, str]],
    baseline: dict[str, dict[str, Any]],
    *,
    reset_baselines: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    reset_baselines = reset_baselines or {}
    total_baseline_realized = 0.0
    total_baseline_closes = 0
    for row in scoreboard_rows:
        lane_name = str(row.get("lane_name") or "")
        if lane_name == "TOTAL":
            continue
        base, _base_source = reset_baseline_for_lane(lane_name, baseline.get(lane_name), reset_baselines)
        base = base or {}
        total_baseline_realized += float(base.get("realized_net_usd") or 0.0)
        total_baseline_closes += int(base.get("closes") or 0)

    rows: list[dict[str, Any]] = []
    for row in scoreboard_rows:
        lane_name = str(row.get("lane_name") or "")
        if lane_name == "TOTAL":
            base = {"realized_net_usd": total_baseline_realized, "closes": total_baseline_closes}
            baseline_source = "seeded"
        else:
            base, baseline_source = reset_baseline_for_lane(lane_name, baseline.get(lane_name), reset_baselines)
        forward_status, forward_note, delta_realized, delta_closes = classify_forward_row(row, base, baseline_source=baseline_source)
        realized = float(row.get("realized_net_usd") or 0.0)
        baseline_realized = float((base or {}).get("realized_net_usd") or 0.0)
        ratio: float | str = ""
        if baseline_realized > 0:
            ratio = round(realized / baseline_realized, 4)
        rows.append(
            {
                "lane_name": lane_name,
                "style": str(row.get("style") or ""),
                "forward_status": forward_status,
                "baseline_realized_usd": round(baseline_realized, 4),
                "realized_net_usd": round(realized, 4),
                "realized_delta_usd": round(delta_realized, 4),
                "realized_to_baseline_ratio": ratio,
                "baseline_closes": int((base or {}).get("closes") or 0),
                "baseline_source": baseline_source,
                "baseline_at": str((base or {}).get("reset_at") or (base or {}).get("seeded_at") or ""),
                "closes": int(float(row.get("closes") or 0)),
                "realized_closes": int(float(row.get("closes") or 0)),
                "new_closes": int(delta_closes),
                "open_count": int(float(row.get("open_count") or 0)),
                "cash_usd": round(float(row.get("cash_usd") or 0.0), 4),
                "heartbeat_age_seconds": row.get("heartbeat_age_seconds") or "",
                "forward_note": forward_note,
            }
        )

    status_rank = {
        "holding_up": 0,
        "holding_up_in_position": 1,
        "seeded_positive": 2,
        "seeded_flat": 3,
        "seeded_negative": 4,
        "lagging": 5,
        "lagging_in_position": 6,
        "flat": 7,
        "unseeded": 8,
        "pack_total": 9,
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
        "# Coinbase Burst Forward Review",
        "",
        "| Lane | Style | Forward Status | Baseline Source | Baseline At | Baseline $ | Realized $ | Delta $ | Ratio | Baseline Closes | Closes | New Closes | Open | Cash $ | Heartbeat Age (s) | Note |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        ratio = row["realized_to_baseline_ratio"] if row["realized_to_baseline_ratio"] != "" else "-"
        lines.append(
            "| {lane_name} | {style} | {forward_status} | {baseline_source} | {baseline_at} | "
            "{baseline_realized_usd:.4f} | {realized_net_usd:.4f} | {realized_delta_usd:.4f} | {ratio} | "
            "{baseline_closes} | {closes} | {new_closes} | {open_count} | {cash_usd:.4f} | "
            "{heartbeat_age_seconds} | {forward_note} |".format(ratio=ratio, **row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    scoreboard_rows = load_rows(SCOREBOARD_CSV)
    baseline = load_baseline(BASELINE_JSON)
    updated_baseline = seed_missing_baselines(scoreboard_rows, baseline)
    if updated_baseline != baseline:
        save_baseline(BASELINE_JSON, updated_baseline)
    rows = build_rows(scoreboard_rows, updated_baseline, reset_baselines=load_reset_baselines())
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
