#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clean_forward_baselines import load_reset_baselines, reset_baseline_for_lane


ROOT = Path(__file__).resolve().parent.parent
SCOREBOARD_CSV = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"
BASELINE_JSON = ROOT / "reports" / "btcusd_h1_step_forward_baseline.json"
CSV_PATH = ROOT / "reports" / "btcusd_h1_step_forward_review.csv"
MD_PATH = ROOT / "reports" / "btcusd_h1_step_forward_review.md"
LIVE_STATE_JSON = ROOT / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"

STATIC_LANE_META = {
    "shadow_btcusd_h1_step30": {"label": "shadow_step30", "step": "30", "role": "shadow_candidate"},
    "shadow_btcusd_h1_step50": {"label": "shadow_step50", "step": "50", "role": "shadow_candidate"},
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
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


def load_live_step() -> float:
    if not LIVE_STATE_JSON.exists():
        return 45.0
    try:
        payload = json.loads(LIVE_STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return 45.0
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    symbols = payload.get("symbols") if isinstance(payload, dict) else {}
    btc_state = symbols.get("BTCUSD") if isinstance(symbols, dict) else {}
    step = (
        (metadata.get("step") if isinstance(metadata, dict) else None)
        or (btc_state.get("base_step_px") if isinstance(btc_state, dict) else None)
        or 45.0
    )
    try:
        return float(step)
    except (TypeError, ValueError):
        return 45.0


def format_step(step: float) -> str:
    return str(int(step)) if float(step).is_integer() else f"{float(step):g}"


def lane_meta(live_step: float | None = None) -> dict[str, dict[str, str]]:
    active_live_step = load_live_step() if live_step is None else float(live_step)
    return {
        "live_btcusd_exc2_tight_941779": {
            "label": f"live_step{format_step(active_live_step)}",
            "step": format_step(active_live_step),
            "role": "live_baseline",
        },
        **STATIC_LANE_META,
    }


def scoreboard_rows() -> list[dict[str, str]]:
    meta = lane_meta()
    rows: list[dict[str, str]] = []
    for row in load_rows(SCOREBOARD_CSV):
        lane_id = str(row.get("lane_id") or "")
        symbol = str(row.get("symbol") or "").upper()
        if lane_id not in meta:
            continue
        if symbol != "TOTAL":
            continue
        rows.append(row)
    return rows


def seed_missing_baselines(scoreboard: list[dict[str, str]], baseline: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    updated = dict(baseline)
    seeded_at = utc_now_iso()
    for row in scoreboard:
        lane_name = str(row.get("lane_id") or "")
        if not lane_name or lane_name in updated:
            continue
        updated[lane_name] = {
            "seeded_at": seeded_at,
            "realized_net_usd": float(row.get("realized_usd") or 0.0),
            "closes": int(float(row.get("closes") or 0)),
        }
    return updated


def classify_forward_row(
    row: dict[str, str],
    baseline_row: dict[str, Any] | None,
    *,
    baseline_source: str = "seeded",
    meta: dict[str, dict[str, str]] | None = None,
) -> tuple[str, str, float, int]:
    meta = meta or lane_meta()
    lane_name = str(row.get("lane_id") or "")
    role = str(meta.get(lane_name, {}).get("role") or "")
    realized = float(row.get("realized_usd") or 0.0)
    closes = int(float(row.get("closes") or 0))
    open_count = int(float(row.get("open_count") or 0))
    if role == "live_baseline":
        return "live_reference", "broker-authoritative live reference row", 0.0, 0
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
    scoreboard: list[dict[str, str]],
    baseline: dict[str, dict[str, Any]],
    *,
    reset_baselines: dict[str, dict[str, Any]] | None = None,
    live_step: float | None = None,
) -> list[dict[str, Any]]:
    reset_baselines = reset_baselines or {}
    meta = lane_meta(live_step)
    rows: list[dict[str, Any]] = []
    for row in scoreboard:
        lane_name = str(row.get("lane_id") or "")
        base, baseline_source = reset_baseline_for_lane(lane_name, baseline.get(lane_name), reset_baselines)
        forward_status, forward_note, delta_realized, delta_closes = classify_forward_row(
            row,
            base,
            baseline_source=baseline_source,
            meta=meta,
        )
        lane_meta_row = meta.get(lane_name, {})
        realized = float(row.get("realized_usd") or 0.0)
        baseline_realized = float((base or {}).get("realized_net_usd") or 0.0)
        rows.append(
            {
                "lane_name": lane_name,
                "label": str(lane_meta_row.get("label") or lane_name),
                "step": str(lane_meta_row.get("step") or ""),
                "role": str(lane_meta_row.get("role") or ""),
                "forward_status": forward_status,
                "baseline_realized_usd": round(baseline_realized, 4),
                "realized_net_usd": round(realized, 4),
                "realized_delta_usd": round(delta_realized, 4),
                "baseline_closes": int((base or {}).get("closes") or 0),
                "baseline_source": baseline_source,
                "baseline_at": str((base or {}).get("reset_at") or (base or {}).get("seeded_at") or ""),
                "closes": int(float(row.get("closes") or 0)),
                "new_closes": int(delta_closes),
                "open_count": int(float(row.get("open_count") or 0)),
                "floating_usd": round(float(row.get("floating_usd") or 0.0), 4),
                "net_usd": round(float(row.get("net_usd") or 0.0), 4),
                "updated_at": str(row.get("updated_at") or ""),
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
        "live_reference": 9,
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
        "# BTCUSD H1 Step Forward Review",
        "",
        "| Lane | Label | Step | Role | Forward Status | Baseline Source | Baseline At | Baseline $ | Realized $ | Delta $ | Baseline Closes | Closes | New Closes | Open | Floating $ | Net $ | Updated At | Note |",
        "| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {lane_name} | {label} | {step} | {role} | {forward_status} | {baseline_source} | {baseline_at} | "
            "{baseline_realized_usd:.4f} | {realized_net_usd:.4f} | {realized_delta_usd:.4f} | {baseline_closes} | "
            "{closes} | {new_closes} | {open_count} | {floating_usd:.4f} | {net_usd:.4f} | {updated_at} | {forward_note} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    scoreboard = scoreboard_rows()
    baseline = load_baseline(BASELINE_JSON)
    updated_baseline = seed_missing_baselines(scoreboard, baseline)
    if updated_baseline != baseline:
        save_baseline(BASELINE_JSON, updated_baseline)
    rows = build_rows(scoreboard, updated_baseline, reset_baselines=load_reset_baselines(), live_step=load_live_step())
    if rows:
        write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
