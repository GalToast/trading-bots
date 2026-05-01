#!/usr/bin/env python3
"""Summarize runtime proof status for the structure-shapeshifter shadow lane."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_state.json"
EVENTS_PATH = ROOT / "reports" / "penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_events.jsonl"
READINESS_PATH = ROOT / "reports" / "structure_shapeshifter_readiness_audit.json"
OUTPUT_JSON = ROOT / "reports" / "structure_shapeshifter_proof_board.json"
OUTPUT_MD = ROOT / "reports" / "structure_shapeshifter_proof_board.md"
LANE_NAME = "shadow_ethusd_m5_structure_shapeshifter"
SYMBOL = "ETHUSD"
REFERENCE_CODE_PATHS = [
    ROOT / "scripts" / "tick_penetration_lattice_core.py",
    ROOT / "scripts" / "live_penetration_lattice_tick_crypto_shadow.py",
]


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def age_seconds_from_iso(value: str | None, *, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def file_mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def path_display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def event_rows_since(events: list[dict[str, Any]], started_at: str | None) -> list[dict[str, Any]]:
    started_dt = parse_iso(started_at)
    if started_dt is None:
        return list(events)
    filtered: list[dict[str, Any]] = []
    for event in events:
        ts = parse_iso(event.get("ts_utc"))
        if ts is None or ts >= started_dt:
            filtered.append(event)
    return filtered


def last_event(events: list[dict[str, Any]], action: str) -> dict[str, Any] | None:
    matches = [event for event in events if str(event.get("action") or "") == action]
    if not matches:
        return None
    return matches[-1]


def build_payload(
    *,
    now: datetime | None = None,
    state: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    state = state if state is not None else load_json(STATE_PATH)
    events = events if events is not None else load_jsonl(EVENTS_PATH)
    readiness = readiness if readiness is not None else load_json(READINESS_PATH)

    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    runner = state.get("runner") if isinstance(state.get("runner"), dict) else {}
    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    symbol_state = symbols.get(SYMBOL) if isinstance(symbols.get(SYMBOL), dict) else {}

    declared_step = float(metadata.get("declared_step_price_units") or metadata.get("step") or 0.0)
    declared_step_buy = float(metadata.get("declared_step_buy_price_units") or metadata.get("step_buy") or declared_step or 0.0)
    declared_step_sell = float(metadata.get("declared_step_sell_price_units") or metadata.get("step_sell") or declared_step or 0.0)
    base_step_px = float(symbol_state.get("base_step_px") or 0.0)
    base_step_buy_px = float(symbol_state.get("base_step_buy_px") or 0.0)
    base_step_sell_px = float(symbol_state.get("base_step_sell_px") or 0.0)

    runtime_mutation_detected = bool(
        base_step_buy_px
        and base_step_sell_px
        and (
            abs(base_step_buy_px - declared_step_buy) > 1e-9
            or abs(base_step_sell_px - declared_step_sell) > 1e-9
            or abs(base_step_buy_px - base_step_px) > 1e-9
            or abs(base_step_sell_px - base_step_px) > 1e-9
        )
    )
    asymmetric_runtime = bool(abs(base_step_buy_px - base_step_sell_px) > 1e-9)

    heartbeat_at = str(runner.get("heartbeat_at") or "")
    runner_started_at = str(runner.get("started_at") or "")
    heartbeat_age_seconds = age_seconds_from_iso(heartbeat_at, now=now)
    runner_fresh = heartbeat_age_seconds is not None and heartbeat_age_seconds <= 90.0
    runner_started_dt = parse_iso(runner_started_at)
    reference_paths = [path for path in REFERENCE_CODE_PATHS if path.exists()]
    latest_reference_path = max(reference_paths, key=lambda item: item.stat().st_mtime) if reference_paths else None
    reference_code_mtime = file_mtime_iso(latest_reference_path) if latest_reference_path is not None else ""
    reference_code_dt = parse_iso(reference_code_mtime)
    event_log_mtime = file_mtime_iso(EVENTS_PATH)
    event_log_mtime_dt = parse_iso(event_log_mtime)
    event_log_is_newer_than_reference_code = bool(
        event_log_mtime_dt is not None and reference_code_dt is not None and event_log_mtime_dt >= reference_code_dt
    )
    runner_started_after_reference_code = bool(
        runner_started_dt is not None and reference_code_dt is not None and runner_started_dt >= reference_code_dt
    )
    pre_enrichment_runtime_window = bool(
        reference_code_dt is not None and (
            not runner_started_after_reference_code or not event_log_is_newer_than_reference_code
        )
    )

    events_since_start = event_rows_since(events, runner_started_at)
    structure_flip_count = sum(1 for event in events if str(event.get("action") or "") == "structure_flip")
    structure_flip_count_since_start = sum(
        1 for event in events_since_start if str(event.get("action") or "") == "structure_flip"
    )
    box_adjust_count = sum(1 for event in events if str(event.get("action") or "") == "box_geometry_adjust")
    box_adjust_count_since_start = sum(
        1 for event in events_since_start if str(event.get("action") or "") == "box_geometry_adjust"
    )
    latest_structure_flip = last_event(events, "structure_flip")
    latest_structure_flip_since_start = last_event(events_since_start, "structure_flip")
    latest_box_adjust = last_event(events, "box_geometry_adjust")
    latest_box_adjust_since_start = last_event(events_since_start, "box_geometry_adjust")

    if not runner_fresh:
        proof_status = "stale_runtime"
    elif structure_flip_count_since_start > 0:
        proof_status = "structure_flip_observed"
    elif runtime_mutation_detected and box_adjust_count_since_start > 0:
        proof_status = "box_only_runtime_mutation"
    elif structure_flip_count > 0:
        proof_status = "historical_structure_flip_only"
    elif runtime_mutation_detected and box_adjust_count > 0:
        proof_status = "historical_box_only"
    elif runtime_mutation_detected:
        proof_status = "runtime_mutation_without_event_evidence"
    else:
        proof_status = "waiting_first_runtime_mutation"

    readiness_verdict = str(readiness.get("verdict") or "")

    return {
        "generated_at": now.isoformat(),
        "lane_name": LANE_NAME,
        "symbol": SYMBOL,
        "proof_status": proof_status,
        "readiness_verdict": readiness_verdict,
        "runner": {
            "pid": int(runner.get("pid") or 0),
            "started_at": runner_started_at,
            "heartbeat_at": heartbeat_at,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "fresh": runner_fresh,
            "tick_history_source_last": str(runner.get("tick_history_source_last") or ""),
            "latest_tick_source_last": str(runner.get("latest_tick_source_last") or ""),
        },
        "deployment_context": {
            "reference_code_path": path_display(latest_reference_path) if latest_reference_path is not None else "",
            "reference_code_mtime": reference_code_mtime,
            "event_log_path": path_display(EVENTS_PATH),
            "event_log_mtime": event_log_mtime,
            "event_log_is_newer_than_reference_code": event_log_is_newer_than_reference_code,
            "runner_started_after_reference_code": runner_started_after_reference_code,
            "pre_enrichment_runtime_window": pre_enrichment_runtime_window,
        },
        "geometry": {
            "declared_step_px": declared_step,
            "declared_step_buy_px": declared_step_buy,
            "declared_step_sell_px": declared_step_sell,
            "base_step_px": base_step_px,
            "base_step_buy_px": base_step_buy_px,
            "base_step_sell_px": base_step_sell_px,
            "runtime_mutation_detected": runtime_mutation_detected,
            "asymmetric_runtime": asymmetric_runtime,
        },
        "events": {
            "total": len(events),
            "total_since_runner_start": len(events_since_start),
            "structure_flip_count": structure_flip_count,
            "structure_flip_count_since_runner_start": structure_flip_count_since_start,
            "box_geometry_adjust_count": box_adjust_count,
            "box_geometry_adjust_count_since_runner_start": box_adjust_count_since_start,
            "latest_structure_flip": latest_structure_flip or {},
            "latest_structure_flip_since_runner_start": latest_structure_flip_since_start or {},
            "latest_box_geometry_adjust": latest_box_adjust or {},
            "latest_box_geometry_adjust_since_runner_start": latest_box_adjust_since_start or {},
        },
        "economics": {
            "realized_closes": int(symbol_state.get("realized_closes") or 0),
            "realized_net_usd": float(symbol_state.get("realized_net_usd") or 0.0),
            "anchor_resets": int(symbol_state.get("anchor_resets") or 0),
            "open_ticket_count": len(list(symbol_state.get("open_tickets") or [])),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    runner = payload.get("runner") or {}
    deployment = payload.get("deployment_context") or {}
    geometry = payload.get("geometry") or {}
    events = payload.get("events") or {}
    economics = payload.get("economics") or {}
    latest_flip = events.get("latest_structure_flip") or {}
    latest_flip_since_start = events.get("latest_structure_flip_since_runner_start") or {}
    latest_box = events.get("latest_box_geometry_adjust") or {}
    latest_box_since_start = events.get("latest_box_geometry_adjust_since_runner_start") or {}
    heartbeat_age = runner.get("heartbeat_age_seconds")
    heartbeat_text = "missing" if heartbeat_age is None else f"{float(heartbeat_age):.1f}s"
    lines = [
        "# Structure Shapeshifter Proof Board",
        "",
        f"- lane: `{payload.get('lane_name', '')}`",
        f"- symbol: `{payload.get('symbol', '')}`",
        f"- readiness_verdict: `{payload.get('readiness_verdict', '') or 'unknown'}`",
        f"- proof_status: `{payload.get('proof_status', '')}`",
        f"- runner_fresh: `{bool(runner.get('fresh'))}`",
        f"- runner_started_at: `{runner.get('started_at', '') or '-'}`",
        f"- heartbeat_age: `{heartbeat_text}`",
        f"- runner_pid: `{int(runner.get('pid', 0) or 0)}`",
        f"- tick_history_source_last: `{runner.get('tick_history_source_last', '') or '-'}`",
        f"- latest_tick_source_last: `{runner.get('latest_tick_source_last', '') or '-'}`",
        f"- reference_code_path: `{deployment.get('reference_code_path', '') or '-'}`",
        f"- reference_code_mtime: `{deployment.get('reference_code_mtime', '') or '-'}`",
        f"- event_log_mtime: `{deployment.get('event_log_mtime', '') or '-'}`",
        f"- event_log_is_newer_than_reference_code: `{bool(deployment.get('event_log_is_newer_than_reference_code'))}`",
        f"- runner_started_after_reference_code: `{bool(deployment.get('runner_started_after_reference_code'))}`",
        f"- pre_enrichment_runtime_window: `{bool(deployment.get('pre_enrichment_runtime_window'))}`",
        f"- runtime_mutation_detected: `{bool(geometry.get('runtime_mutation_detected'))}`",
        f"- asymmetric_runtime: `{bool(geometry.get('asymmetric_runtime'))}`",
        f"- structure_flip_count: `{int(events.get('structure_flip_count', 0) or 0)}` total / `{int(events.get('structure_flip_count_since_runner_start', 0) or 0)}` since current runner",
        f"- box_geometry_adjust_count: `{int(events.get('box_geometry_adjust_count', 0) or 0)}` total / `{int(events.get('box_geometry_adjust_count_since_runner_start', 0) or 0)}` since current runner",
        f"- realized_closes: `{int(economics.get('realized_closes', 0) or 0)}`",
        f"- realized_net_usd: `{float(economics.get('realized_net_usd', 0.0) or 0.0):.2f}`",
        f"- anchor_resets: `{int(economics.get('anchor_resets', 0) or 0)}`",
        "",
        "## Geometry",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| declared_step_px | `{float(geometry.get('declared_step_px', 0.0) or 0.0):.6f}` |",
        f"| declared_step_buy_px | `{float(geometry.get('declared_step_buy_px', 0.0) or 0.0):.6f}` |",
        f"| declared_step_sell_px | `{float(geometry.get('declared_step_sell_px', 0.0) or 0.0):.6f}` |",
        f"| base_step_px | `{float(geometry.get('base_step_px', 0.0) or 0.0):.6f}` |",
        f"| base_step_buy_px | `{float(geometry.get('base_step_buy_px', 0.0) or 0.0):.6f}` |",
        f"| base_step_sell_px | `{float(geometry.get('base_step_sell_px', 0.0) or 0.0):.6f}` |",
        "",
        "## Latest Events",
        "",
        f"- latest_structure_flip_at: `{latest_flip.get('ts_utc', '') or 'none'}`",
        f"- latest_structure_flip_reason: `{latest_flip.get('reason', '') or '-'}`",
        f"- latest_structure_flip_since_runner_start_at: `{latest_flip_since_start.get('ts_utc', '') or 'none'}`",
        f"- latest_structure_flip_since_runner_start_reason: `{latest_flip_since_start.get('reason', '') or '-'}`",
        f"- latest_box_geometry_adjust_at: `{latest_box.get('ts_utc', '') or 'none'}`",
        f"- latest_box_geometry_adjust_reason: `{latest_box.get('reason', '') or '-'}`",
        f"- latest_box_geometry_adjust_since_runner_start_at: `{latest_box_since_start.get('ts_utc', '') or 'none'}`",
        f"- latest_box_geometry_adjust_since_runner_start_reason: `{latest_box_since_start.get('reason', '') or '-'}`",
        "",
        "## Interpretation",
        "",
    ]

    proof_status = str(payload.get("proof_status") or "")
    if proof_status == "structure_flip_observed":
        lines.append("Structure-driven proof has been observed in the current runner window. Keep the lane shadow-only until the economics and restore sample are still clean, but the path is no longer waiting on its first post-repair `structure_flip`.")
    elif proof_status == "box_only_runtime_mutation":
        lines.append("Runtime geometry mutation is real in the current runner window, but the checked-in proof is still box-aware only. Treat the path as shadow proof in progress until the event stream shows explicit post-repair `structure_flip` entries.")
    elif proof_status == "historical_structure_flip_only":
        lines.append("Historical `structure_flip` evidence exists in the journal, but none appears in the current runner window yet. Do not borrow old proof across a restart boundary.")
    elif proof_status == "historical_box_only":
        lines.append("Runtime geometry mutation is real, but the checked-in proof is historical box-only. The current runner window has no `structure_flip` or `box_geometry_adjust` evidence yet.")
        if bool(deployment.get("pre_enrichment_runtime_window")):
            lines.append("Operational caveat: the watched runner or event journal predates the latest telemetry-bearing code, so fresh enriched events likely require a restart or a new journal window before path-quality proof can appear here.")
    elif proof_status == "runtime_mutation_without_event_evidence":
        lines.append("Runtime geometry differs from the declared baseline, but the checked-in event stream does not yet show enough explicit adaptation evidence to call the path proven.")
    elif proof_status == "stale_runtime":
        lines.append("The lane is not writing fresh runtime state. Do not draw adaptive-proof conclusions until the supervised state is fresh again.")
    else:
        lines.append("No convincing runtime mutation proof is visible yet. Keep the lane in shadow-only observation mode.")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
