#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
CSV_PATH = REPORTS / "coinbase_ratio_forward_review.csv"
MD_PATH = REPORTS / "coinbase_ratio_forward_review.md"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify_forward_row(row: dict[str, Any]) -> tuple[str, str]:
    closes = to_int(row.get("realized_closes"))
    realized = to_float(row.get("realized_net_usd"))
    open_count = to_int(row.get("open_count"))

    if closes < 3:
        if realized > 0:
            return "seeded_positive", "too few closes for a forward verdict"
        if realized < 0:
            return "seeded_negative", "too few closes for a forward verdict"
        if open_count > 0:
            return "seeded_in_position", "too few closes for a forward verdict"
        return "seeded_flat", "too few closes for a forward verdict"

    if closes < 10:
        if realized > 0:
            return "bootstrap_positive", "early positive forward evidence, still low sample"
        if realized < 0:
            return "bootstrap_negative", "early negative forward evidence, still low sample"
        if open_count > 0:
            return "bootstrap_in_position", "low sample and currently in position"
        return "bootstrap_flat", "low sample and no realized edge yet"

    if realized > 0:
        if open_count > 0:
            return "holding_up_in_position", "enough closes and positive realized with live sleeve inventory"
        return "holding_up", "enough closes and positive realized forward evidence"
    if realized < 0:
        if open_count > 0:
            return "lagging_in_position", "enough closes and negative realized with live sleeve inventory"
        return "lagging", "enough closes and negative realized forward evidence"
    return "flat", "enough closes but no realized edge yet"


def iter_ratio_lanes(registry_payload: dict[str, Any]) -> list[dict[str, str]]:
    lanes = registry_payload.get("lanes") or []
    rows: list[dict[str, str]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        name = str(lane.get("name") or "")
        kind = str(lane.get("kind") or "")
        state_path = str(lane.get("state_path") or "")
        if kind != "shadow_coinbase_spot":
            continue
        if not name.endswith("_ratio_sleeve"):
            continue
        if not state_path:
            continue
        rows.append({"lane_name": name, "state_path": state_path})
    rows.sort(key=lambda row: row["lane_name"])
    return rows


def build_row(payload: dict[str, Any], *, lane_name: str) -> dict[str, Any]:
    stats = payload.get("stats") or {}
    account = payload.get("account") or {}
    market = payload.get("market") or {}
    runner = payload.get("runner") or {}
    row = {
        "pair": str(payload.get("pair") or ""),
        "lane_name": lane_name,
        "forward_status": "",
        "realized_net_den": round(to_float(stats.get("realized_pnl_den")), 8),
        "realized_net_usd": round(to_float(stats.get("realized_pnl_usd_mark")), 4),
        "realized_closes": to_int(stats.get("total_closes")),
        "open_count": len(payload.get("positions") or []),
        "wins": to_int(stats.get("wins")),
        "losses": to_int(stats.get("losses")),
        "parked_den_units": round(to_float(account.get("parked_den_units")), 8),
        "equity_usd_mark": round(to_float(account.get("total_equity_usd_mark")), 4),
        "last_ratio": round(to_float(market.get("last_ratio")), 10),
        "heartbeat_age_seconds": "",
        "forward_note": "",
    }
    status, note = classify_forward_row(row)
    row["forward_status"] = status
    row["forward_note"] = note
    heartbeat_at = str(runner.get("heartbeat_at") or payload.get("updated_at") or "")
    row["heartbeat_at"] = heartbeat_at
    return row


def build_rows(registry_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane in registry_rows:
        payload = load_json(ROOT / lane["state_path"])
        if not payload:
            continue
        rows.append(build_row(payload, lane_name=lane["lane_name"]))
    rows.sort(key=lambda row: (row["pair"], row["lane_name"]))
    return rows


def write_reports(rows: list[dict[str, Any]], *, csv_path: Path = CSV_PATH, md_path: Path = MD_PATH) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pair",
        "lane_name",
        "forward_status",
        "realized_net_den",
        "realized_net_usd",
        "realized_closes",
        "open_count",
        "wins",
        "losses",
        "parked_den_units",
        "equity_usd_mark",
        "last_ratio",
        "heartbeat_age_seconds",
        "forward_note",
        "heartbeat_at",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Ratio Forward Review",
        "",
        "| Pair | Lane | Forward Status | Realized Net Den | Realized Net USD | Closes | Open | Wins | Losses | Parked Den | Equity USD | Last Ratio | Note |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {pair} | {lane_name} | {forward_status} | {realized_net_den:.8f} | {realized_net_usd:.4f} | "
            "{realized_closes} | {open_count} | {wins} | {losses} | {parked_den_units:.8f} | {equity_usd_mark:.4f} | "
            "{last_ratio:.10f} | {forward_note} |".format(**row)
        )
    if not rows:
        lines.append("| - | - | no_ratio_lanes | 0.00000000 | 0.0000 | 0 | 0 | 0 | 0 | 0.00000000 | 0.0000 | 0.0000000000 | no supervised ratio sleeve states found |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    registry_payload = load_json(REGISTRY_PATH)
    registry_rows = iter_ratio_lanes(registry_payload)
    rows = build_rows(registry_rows)
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
