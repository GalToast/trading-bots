#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
FORWARD_REVIEW_CSV = REPORTS / "coinbase_ratio_forward_review.csv"
CSV_PATH = REPORTS / "coinbase_ratio_proof_readiness.csv"
MD_PATH = REPORTS / "coinbase_ratio_proof_readiness.md"

ROLE_MAP = {
    "CFG/ETH": "first_proof",
    "CFG/BTC": "scale_up",
}


def parse_iso_utc(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_registry_lanes(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    lanes = payload if isinstance(payload, list) else payload.get("lanes", [])
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        if str(lane.get("kind") or "") != "shadow_coinbase_spot":
            continue
        name = str(lane.get("name") or "")
        if not name.endswith("_ratio_sleeve"):
            continue
        rows.append(lane)
    rows.sort(key=lambda row: str(row.get("name") or ""))
    return rows


def load_forward_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {str(row.get("lane_name") or ""): row for row in csv.DictReader(handle)}


def heartbeat_age_seconds(state_payload: dict[str, Any], now: datetime) -> float | None:
    runner = state_payload.get("runner") or {}
    candidates = [
        parse_iso_utc(runner.get("heartbeat_at")),
        parse_iso_utc(runner.get("last_successful_run_at")),
        parse_iso_utc(state_payload.get("updated_at")),
    ]
    for candidate in candidates:
        if candidate is not None:
            return max(0.0, (now - candidate).total_seconds())
    return None


def classify_gate(*, forward_status: str, open_count: int, closes: int) -> str:
    if closes == 0 and open_count == 0:
        return "waiting_first_entry"
    if closes == 0 and open_count > 0:
        return "waiting_first_close"
    if forward_status.startswith("seeded_"):
        return "seeded_evidence"
    if forward_status.startswith("bootstrap_"):
        return "bootstrap_evidence"
    if forward_status.startswith("holding_up"):
        return "mature_positive"
    if forward_status.startswith("lagging"):
        return "mature_negative"
    return "under_review"


def classify_posture(*, pair: str, current_gate: str) -> str:
    role = ROLE_MAP.get(pair, "research")
    if role == "first_proof":
        if current_gate in {"waiting_first_entry", "waiting_first_close", "seeded_evidence", "bootstrap_evidence"}:
            return "keep_shadowing_first_proof"
        if current_gate == "mature_positive":
            return "candidate_for_promotion_review"
        if current_gate == "mature_negative":
            return "hold_shadow_only"
    if role == "scale_up":
        if current_gate in {"waiting_first_entry", "waiting_first_close", "seeded_evidence", "bootstrap_evidence"}:
            return "shadow_only_scale_up"
        if current_gate == "mature_positive":
            return "candidate_after_first_proof"
        if current_gate == "mature_negative":
            return "hold_shadow_only"
    return "research_only"


def build_rows(
    *,
    registry_path: Path = REGISTRY_PATH,
    forward_review_csv: Path = FORWARD_REVIEW_CSV,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    repo_root = registry_path.resolve().parent.parent
    lanes = load_registry_lanes(registry_path)
    forward_rows = load_forward_rows(forward_review_csv)
    now_utc = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        state_path = repo_root / str(lane.get("state_path") or "")
        if not state_path.exists():
            continue
        state_payload = load_json(state_path)
        lane_name = str(lane.get("name") or "")
        pair = str(state_payload.get("pair") or "")
        forward_row = forward_rows.get(lane_name, {})
        heartbeat_age = heartbeat_age_seconds(state_payload, now_utc)
        stale_after = int(lane.get("stale_after_seconds") or 0)
        open_count = int(float(forward_row.get("open_count") or 0))
        closes = int(float(forward_row.get("realized_closes") or 0))
        forward_status = str(forward_row.get("forward_status") or "unreviewed")
        current_gate = classify_gate(forward_status=forward_status, open_count=open_count, closes=closes)
        posture = classify_posture(pair=pair, current_gate=current_gate)
        runner = state_payload.get("runner") or {}
        metadata = state_payload.get("metadata") or {}
        rows.append(
            {
                "pair": pair,
                "lane_name": lane_name,
                "role": ROLE_MAP.get(pair, "research"),
                "route": f"{metadata.get('denominator_product', '?')} -> USD -> {metadata.get('numerator_product', '?')}",
                "watchdog": "ok" if heartbeat_age is not None and stale_after > 0 and heartbeat_age <= stale_after else "stale",
                "forward_status": forward_status,
                "current_gate": current_gate,
                "deployment_posture": posture,
                "realized_closes": closes,
                "open_count": open_count,
                "realized_net_usd": float(forward_row.get("realized_net_usd") or 0.0),
                "equity_usd_mark": float(forward_row.get("equity_usd_mark") or 0.0),
                "heartbeat_age_seconds": round(float(heartbeat_age), 1) if heartbeat_age is not None else "",
                "pid": int(runner.get("pid") or 0),
                "note": str(forward_row.get("forward_note") or ""),
            }
        )

    role_rank = {"first_proof": 0, "scale_up": 1, "research": 9}
    rows.sort(key=lambda row: (role_rank.get(str(row["role"]), 9), str(row["pair"])))
    return rows


def write_reports(rows: list[dict[str, Any]], *, csv_path: Path = CSV_PATH, md_path: Path = MD_PATH) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Ratio Proof Readiness",
        "",
        "| Pair | Lane | Role | Watchdog | Forward | Current Gate | Posture | Closes | Open | Realized $ | Equity $ | Heartbeat Age (s) | Route | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {pair} | {lane_name} | {role} | {watchdog} | {forward_status} | {current_gate} | "
            "{deployment_posture} | {realized_closes} | {open_count} | {realized_net_usd:.4f} | "
            "{equity_usd_mark:.4f} | {heartbeat_age_seconds} | {route} | {note} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows()
    if not rows:
        raise SystemExit("no supervised Coinbase ratio proof rows found")
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
