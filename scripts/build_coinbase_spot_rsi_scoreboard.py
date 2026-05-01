#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
REPORTS = ROOT / "reports"
CSV_PATH = REPORTS / "coinbase_spot_rsi_scoreboard.csv"
MD_PATH = REPORTS / "coinbase_spot_rsi_scoreboard.md"
READINESS_PATHS = [
    REPORTS / "coinbase_spot_rsi_readiness_extended.csv",
    REPORTS / "coinbase_spot_rsi_readiness.csv",
]
RSI_RUNNER_SCRIPTS = {
    "scripts/live_coinbase_rsi_shadow.py",
    "scripts/live_coinbase_rsi_bundle_shadow.py",
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
    results: list[dict[str, Any]] = []
    for lane in lanes:
        if not bool(lane.get("enabled", True)):
            continue
        if str(lane.get("kind") or "") != "shadow_coinbase_spot":
            continue
        restart_args = [str(item) for item in (lane.get("restart_args") or [])]
        if not any(any(script in item for script in RSI_RUNNER_SCRIPTS) for item in restart_args):
            continue
        results.append(lane)
    return results


def load_readiness_map(paths: list[Path]) -> dict[str, dict[str, str]]:
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return {str(row.get("product_id") or ""): row for row in rows}
    return {}


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


def product_id_from_lane(lane: dict[str, Any], state_payload: dict[str, Any]) -> str:
    state = state_payload.get("state") or {}
    product_id = str(state.get("product_id") or "").strip().upper()
    if product_id:
        return product_id
    restart_args = [str(item) for item in (lane.get("restart_args") or [])]
    if "--product-id" in restart_args:
        idx = restart_args.index("--product-id")
        if idx + 1 < len(restart_args):
            return restart_args[idx + 1].strip().upper()
    return ""


def lane_row(
    lane: dict[str, Any],
    *,
    state_payload: dict[str, Any],
    readiness: dict[str, dict[str, str]],
    now: datetime,
) -> dict[str, Any]:
    state = state_payload.get("state") or {}
    runner = state_payload.get("runner") or {}
    product_id = product_id_from_lane(lane, state_payload)
    readiness_row = readiness.get(product_id, {})
    heartbeat_age = heartbeat_age_seconds(state_payload, now)
    wf_positive = str(readiness_row.get("walkforward_positive_windows") or "").strip()
    wf_total = str(readiness_row.get("walkforward_windows") or "").strip()
    walkforward = f"{wf_positive}/{wf_total}" if wf_positive and wf_total else "-"
    return {
        "lane_name": str(lane.get("name") or ""),
        "product_id": product_id,
        "readiness_verdict": str(readiness_row.get("verdict") or "unrated"),
        "baseline_72h_net_usd": round(float(readiness_row.get("full_net_usd") or 0.0), 4),
        "walkforward": walkforward,
        "realized_net_usd": round(float(state.get("realized_net_usd") or 0.0), 4),
        "realized_closes": int(state.get("realized_closes") or 0),
        "in_position": int(bool(state.get("in_position"))),
        "cash_usd": round(float(state.get("cash_usd") or 0.0), 2),
        "total_fees": round(float(state.get("total_fees") or 0.0), 4),
        "signals_generated": int(state.get("signals_generated") or 0),
        "heartbeat_age_seconds": round(float(heartbeat_age), 1) if heartbeat_age is not None else "",
        "pid": int(runner.get("pid") or 0),
        "state_path": str(Path(str(lane.get("state_path") or ""))),
        "note": str(readiness_row.get("note") or ""),
    }


def build_rows(
    *,
    registry_path: Path = REGISTRY_PATH,
    readiness_paths: list[Path] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    repo_root = registry_path.resolve().parent.parent
    lanes = load_registry_lanes(registry_path)
    readiness = load_readiness_map(readiness_paths or READINESS_PATHS)
    now_utc = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        state_path = repo_root / str(lane.get("state_path") or "")
        if not state_path.exists():
            continue
        state_payload = load_json(state_path)
        rows.append(lane_row(lane, state_payload=state_payload, readiness=readiness, now=now_utc))

    verdict_rank = {"probationary": 0, "monitor_only": 1, "reject": 2, "unrated": 3}
    rows.sort(key=lambda row: (verdict_rank.get(str(row["readiness_verdict"]), 9), -float(row["realized_net_usd"])))

    total = {
        "lane_name": "TOTAL",
        "product_id": "TOTAL",
        "readiness_verdict": "supervised_pack",
        "baseline_72h_net_usd": round(sum(float(row["baseline_72h_net_usd"]) for row in rows), 4),
        "walkforward": "-",
        "realized_net_usd": round(sum(float(row["realized_net_usd"]) for row in rows), 4),
        "realized_closes": sum(int(row["realized_closes"]) for row in rows),
        "in_position": sum(int(row["in_position"]) for row in rows),
        "cash_usd": round(sum(float(row["cash_usd"]) for row in rows), 2),
        "total_fees": round(sum(float(row["total_fees"]) for row in rows), 4),
        "signals_generated": sum(int(row["signals_generated"]) for row in rows),
        "heartbeat_age_seconds": "",
        "pid": 0,
        "state_path": "",
        "note": f"lanes={len(rows)}",
    }
    rows.append(total)
    return rows


def write_reports(rows: list[dict[str, Any]], *, csv_path: Path = CSV_PATH, md_path: Path = MD_PATH) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Spot RSI Scoreboard",
        "",
        "| Lane | Product | Verdict | 72h Baseline $ | Realized $ | Closes | In Pos | Cash $ | Fees $ | WF | Heartbeat Age (s) | Note |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {lane_name} | {product_id} | {readiness_verdict} | {baseline_72h_net_usd:.4f} | "
            "{realized_net_usd:.4f} | {realized_closes} | {in_position} | {cash_usd:.2f} | "
            "{total_fees:.4f} | {walkforward} | {heartbeat_age_seconds} | {note} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows()
    if not rows:
        raise SystemExit("no supervised Coinbase RSI rows found")
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
