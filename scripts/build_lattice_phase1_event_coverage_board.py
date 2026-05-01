#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENT_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_events.jsonl"
DEFAULT_REFERENCE_CODE_PATH = ROOT / "scripts" / "tick_penetration_lattice_core.py"
DEFAULT_OUTPUT_JSON = REPORTS / "lattice_phase1_event_coverage_board.json"
DEFAULT_OUTPUT_MD = REPORTS / "lattice_phase1_event_coverage_board.md"
GAP_BOARD_JSON = REPORTS / "lattice_telemetry_gap_board.json"


def _is_close_like(event: dict[str, Any]) -> bool:
    action = str(event.get("action") or "")
    return action == "close_ticket" or action.startswith("escape_")


SECTION_SPECS: list[dict[str, Any]] = [
    {
        "id": "open_context",
        "label": "Open Ticket Context",
        "selector": lambda event: str(event.get("action") or "") == "open_ticket",
        "fields": [
            "spread_at_entry",
            "entry_context",
            "session_bucket",
            "base_step_px_at_open",
            "same_tick_open_burst_count",
            "same_bar_open_burst_count",
            "anchor_distance_px_at_open",
        ],
    },
    {
        "id": "close_path",
        "label": "Close And Escape Path Metrics",
        "selector": _is_close_like,
        "fields": [
            "time_to_first_green_seconds",
            "max_favorable_excursion_pnl",
            "max_adverse_excursion_pnl",
            "peak_pnl_before_exit",
            "hold_seconds",
            "first_green_before_fail",
            "reclaimed_trigger_level_seen",
            "retraced_0_25x_step_seen",
            "retraced_0_5x_step_seen",
        ],
    },
    {
        "id": "rearm_timing",
        "label": "Rearm Timing Fields",
        "selector": lambda event: str(event.get("action") or "") == "open_ticket" and bool(event.get("rearm_open")),
        "fields": [
            "token_age_at_fire_seconds",
            "armed_duration_seconds",
        ],
    },
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def file_mtime_iso(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def value_present(value: Any) -> bool:
    return value not in (None, "")


def first_present_value(events: list[dict[str, Any]], field: str) -> Any:
    for event in events:
        value = event.get(field)
        if value_present(value):
            return value
    return None


def numeric_values(events: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for event in events:
        value = event.get(field)
        if not value_present(value):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def latest_ts_utc(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        text = str(event.get("ts_utc") or "").strip()
        if text:
            return text
    return ""


def summarize_section(spec: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    selector: Callable[[dict[str, Any]], bool] = spec["selector"]
    matched = [event for event in events if selector(event)]
    fields = []
    for field in spec["fields"]:
        coverage_count = sum(1 for event in matched if value_present(event.get(field)))
        event_count = len(matched)
        coverage_pct = (coverage_count / event_count * 100.0) if event_count else 0.0
        fields.append(
            {
                "name": field,
                "coverage_count": coverage_count,
                "event_count": event_count,
                "coverage_pct": round(coverage_pct, 1),
                "sample_value": first_present_value(matched, field),
            }
        )
    covered_fields = sum(1 for field in fields if field["coverage_count"] > 0)
    return {
        "id": spec["id"],
        "label": spec["label"],
        "event_count": len(matched),
        "field_count": len(fields),
        "covered_field_count": covered_fields,
        "zero_coverage_field_count": len(fields) - covered_fields,
        "fields": fields,
    }


def summarize_same_tick_bursts(events: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if str(event.get("action") or "") != "open_ticket":
            continue
        time_msc = event.get("time_msc")
        if time_msc in (None, ""):
            continue
        try:
            time_msc_int = int(time_msc)
        except (TypeError, ValueError):
            continue
        key = (
            str(event.get("symbol") or ""),
            str(event.get("direction") or ""),
            time_msc_int,
        )
        groups[key].append(event)

    clusters = []
    for (symbol, direction, time_msc), clustered in groups.items():
        if len(clustered) < 2:
            continue
        clusters.append(
            {
                "symbol": symbol,
                "direction": direction,
                "time_msc": time_msc,
                "ts_utc": str(clustered[0].get("ts_utc") or ""),
                "open_count": len(clustered),
            }
        )

    clusters.sort(key=lambda item: (-int(item["open_count"]), str(item["ts_utc"])))
    largest = clusters[0] if clusters else None
    return {
        "cluster_count_ge_2": len(clusters),
        "max_open_count": int(largest["open_count"]) if largest else 0,
        "largest_cluster": largest,
    }


def summarize_close_path_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    close_like_events = [event for event in events if _is_close_like(event)]
    phase1_fields = set(SECTION_SPECS[1]["fields"])
    phase1_events = [
        event
        for event in close_like_events
        if any(value_present(event.get(field)) for field in phase1_fields)
    ]
    ttfg_values = numeric_values(phase1_events, "time_to_first_green_seconds")
    hold_values = numeric_values(phase1_events, "hold_seconds")
    peak_values = numeric_values(phase1_events, "peak_pnl_before_exit")
    mfe_values = numeric_values(phase1_events, "max_favorable_excursion_pnl")
    mae_values = numeric_values(phase1_events, "max_adverse_excursion_pnl")
    loss_with_first_green_count = 0
    loss_without_first_green_count = 0
    reclaimed_trigger_level_count = 0
    retraced_half_step_count = 0
    for event in phase1_events:
        try:
            realized_pnl = float(event.get("realized_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        saw_green = value_present(event.get("time_to_first_green_seconds")) or bool(event.get("first_green_before_fail"))
        if realized_pnl < 0.0 and saw_green:
            loss_with_first_green_count += 1
        elif realized_pnl < 0.0 and not saw_green:
            loss_without_first_green_count += 1
        if bool(event.get("reclaimed_trigger_level_seen")):
            reclaimed_trigger_level_count += 1
        if bool(event.get("retraced_0_5x_step_seen")):
            retraced_half_step_count += 1
    return {
        "phase1_close_metric_event_count": len(phase1_events),
        "close_like_event_count": len(close_like_events),
        "latest_close_like_ts_utc": latest_ts_utc(close_like_events),
        "ttfg_present_count": len(ttfg_values),
        "ttfg_missing_count": max(0, len(phase1_events) - len(ttfg_values)),
        "loss_with_first_green_count": loss_with_first_green_count,
        "loss_without_first_green_count": loss_without_first_green_count,
        "avg_hold_seconds": round(sum(hold_values) / len(hold_values), 1) if hold_values else None,
        "median_time_to_first_green_seconds": round(median(ttfg_values), 1) if ttfg_values else None,
        "avg_peak_pnl_before_exit": round(sum(peak_values) / len(peak_values), 3) if peak_values else None,
        "avg_max_favorable_excursion_pnl": round(sum(mfe_values) / len(mfe_values), 3) if mfe_values else None,
        "avg_max_adverse_excursion_pnl": round(sum(mae_values) / len(mae_values), 3) if mae_values else None,
        "reclaimed_trigger_level_count": reclaimed_trigger_level_count,
        "retraced_half_step_count": retraced_half_step_count,
    }


def summarize_first_path_triage(events: list[dict[str, Any]]) -> dict[str, Any]:
    open_fields = set(SECTION_SPECS[0]["fields"])
    close_fields = set(SECTION_SPECS[1]["fields"])
    phase1_open_events = [
        event
        for event in events
        if str(event.get("action") or "") == "open_ticket"
        and any(value_present(event.get(field)) for field in open_fields)
    ]
    phase1_close_events = [
        event
        for event in events
        if _is_close_like(event) and any(value_present(event.get(field)) for field in close_fields)
    ]
    first_open = phase1_open_events[0] if phase1_open_events else {}
    first_close = phase1_close_events[0] if phase1_close_events else {}

    if not phase1_open_events and not phase1_close_events:
        verdict = "awaiting_first_trade_path_event"
        rationale = "No fresh Phase 1 open_ticket or close-like event exists yet in the inspected log."
    elif phase1_open_events and not phase1_close_events:
        verdict = "first_path_opened_waiting_close"
        rationale = "A fresh Phase 1 open_ticket exists, but no close-like event has completed the first path yet."
    else:
        try:
            realized_pnl = float(first_close.get("realized_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        saw_green = value_present(first_close.get("time_to_first_green_seconds")) or bool(
            first_close.get("first_green_before_fail")
        )
        if realized_pnl < 0.0 and not saw_green:
            verdict = "never_green_toxic_continuation"
            rationale = "The first close-like event realized a loss without ever recording first green."
        elif realized_pnl < 0.0 and saw_green:
            verdict = "went_green_failed_monetization"
            rationale = "The first close-like event went green before exit but still realized a loss."
        elif realized_pnl >= 0.0 and saw_green:
            verdict = "green_and_monetized"
            rationale = "The first close-like event reached first green and exited non-negative."
        else:
            verdict = "closed_without_recorded_green"
            rationale = "The first close-like event exited non-negative without a recorded first-green transition."

    return {
        "verdict": verdict,
        "rationale": rationale,
        "first_open_ts_utc": str(first_open.get("ts_utc") or ""),
        "first_open_direction": str(first_open.get("direction") or ""),
        "first_open_entry_context": str(first_open.get("entry_context") or ""),
        "first_close_ts_utc": str(first_close.get("ts_utc") or ""),
        "first_close_action": str(first_close.get("action") or ""),
        "first_close_direction": str(first_close.get("direction") or ""),
        "first_close_realized_pnl": first_close.get("realized_pnl"),
        "first_close_time_to_first_green_seconds": first_close.get("time_to_first_green_seconds"),
        "first_close_peak_pnl_before_exit": first_close.get("peak_pnl_before_exit"),
        "first_close_reclaimed_trigger_level_seen": bool(first_close.get("reclaimed_trigger_level_seen", False)),
        "first_close_retraced_0_5x_step_seen": bool(first_close.get("retraced_0_5x_step_seen", False)),
    }


def summarize_market_state_hypothesis(
    burst_summary: dict[str, Any],
    close_path_summary: dict[str, Any],
    first_path_triage: dict[str, Any],
) -> dict[str, Any]:
    verdict = str(first_path_triage.get("verdict") or "")
    burst_cluster_count = int(burst_summary.get("cluster_count_ge_2", 0) or 0)
    burst_max_open_count = int(burst_summary.get("max_open_count", 0) or 0)
    reclaimed_trigger_level_count = int(close_path_summary.get("reclaimed_trigger_level_count", 0) or 0)
    retraced_half_step_count = int(close_path_summary.get("retraced_half_step_count", 0) or 0)
    loss_without_first_green_count = int(close_path_summary.get("loss_without_first_green_count", 0) or 0)
    loss_with_first_green_count = int(close_path_summary.get("loss_with_first_green_count", 0) or 0)
    phase1_close_metric_event_count = int(close_path_summary.get("phase1_close_metric_event_count", 0) or 0)

    if verdict in {"awaiting_first_trade_path_event", "first_path_opened_waiting_close"}:
        return {
            "verdict": "insufficient_fresh_path_evidence",
            "confidence": "low",
            "rationale": "The lattice has not seen a completed fresh enriched trade path yet, so any temporary-impact vs repricing call would still be guesswork.",
            "operator_question": "Wait for the first enriched close-like event, then ask whether the path ever went green and whether price reclaimed trigger structure before exit.",
        }

    if verdict == "never_green_toxic_continuation":
        if burst_cluster_count > 0 or burst_max_open_count >= 2:
            rationale = "The first enriched path never reached green and arrived inside an open-burst cluster, which is more consistent with toxic one-way flow or repricing pressure than with temporary impact decay."
        else:
            rationale = "The first enriched path never reached green before exit, which is more consistent with repricing or toxic continuation than with a repayable temporary displacement."
        return {
            "verdict": "repricing_or_toxic_flow_risk",
            "confidence": "high",
            "rationale": rationale,
            "operator_question": "Should the lattice stand down or widen hazard posture instead of assuming the anchor will be repaid on the current regime?",
        }

    if verdict == "went_green_failed_monetization":
        return {
            "verdict": "temporary_impact_but_poor_monetization",
            "confidence": "medium",
            "rationale": "The path did go green before exit, so temporary impact decay existed, but the lattice still failed to realize it cleanly before the move turned back hostile.",
            "operator_question": "Is the problem close sequencing / rearm timing rather than the absence of repayable temporary impact?",
        }

    if verdict == "green_and_monetized":
        if reclaimed_trigger_level_count > 0 or retraced_half_step_count > 0:
            rationale = "The first enriched path reclaimed trigger structure and monetized after going green, which is the cleanest current signature of temporary-impact decay."
        else:
            rationale = "The first enriched path monetized after going green, which is still more consistent with temporary-impact decay than with one-way repricing."
        return {
            "verdict": "temporary_impact_decay_present",
            "confidence": "medium",
            "rationale": rationale,
            "operator_question": "Can the lattice now tighten around this regime without overpaying churn for the same decay pattern?",
        }

    if verdict == "closed_without_recorded_green":
        return {
            "verdict": "shallow_recycle_or_incomplete_green_trace",
            "confidence": "low",
            "rationale": "The path exited non-negative without a recorded first-green transition, which suggests either shallow recycle behavior or incomplete path instrumentation on that first sample.",
            "operator_question": "Do later enriched paths confirm real temporary-impact repayment, or is the first sample too thin to classify honestly?",
        }

    if phase1_close_metric_event_count > 0 and loss_without_first_green_count > loss_with_first_green_count:
        return {
            "verdict": "repricing_bias_in_sample",
            "confidence": "low",
            "rationale": "The enriched sample skews toward losses that never reached green, which is weak evidence of repricing pressure but not yet a decisive regime call.",
            "operator_question": "Does the next path reinforce cold-loss behavior, or was this just one bad sample?",
        }

    return {
        "verdict": "mixed_or_unclear_path_regime",
        "confidence": "low",
        "rationale": "The current enriched sample is too mixed to classify as clean temporary-impact decay or clean repricing risk yet.",
        "operator_question": "Collect more enriched path events before changing lattice posture from this signal alone.",
    }


def summarize_rearm_timing(events: list[dict[str, Any]]) -> dict[str, Any]:
    rearm_events = [
        event
        for event in events
        if str(event.get("action") or "") == "open_ticket" and bool(event.get("rearm_open"))
    ]
    token_age_values = numeric_values(rearm_events, "token_age_at_fire_seconds")
    armed_duration_values = numeric_values(rearm_events, "armed_duration_seconds")
    return {
        "rearm_open_count": len(rearm_events),
        "token_age_present_count": len(token_age_values),
        "armed_duration_present_count": len(armed_duration_values),
        "avg_token_age_at_fire_seconds": round(sum(token_age_values) / len(token_age_values), 1) if token_age_values else None,
        "avg_armed_duration_seconds": round(sum(armed_duration_values) / len(armed_duration_values), 1) if armed_duration_values else None,
    }


def build_payload(
    *,
    events: list[dict[str, Any]],
    event_path: Path,
    lane_label: str,
    now: datetime | None = None,
    gap_payload: dict[str, Any] | None = None,
    reference_code_path: Path | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    gap_payload = gap_payload if gap_payload is not None else load_json(GAP_BOARD_JSON)
    reference_code_path = reference_code_path if reference_code_path is not None else DEFAULT_REFERENCE_CODE_PATH
    sections = [summarize_section(spec, events) for spec in SECTION_SPECS]
    total_fields = sum(int(section["field_count"]) for section in sections)
    covered_fields = sum(int(section["covered_field_count"]) for section in sections)
    burst_summary = summarize_same_tick_bursts(events)
    close_path_summary = summarize_close_path_metrics(events)
    first_path_triage = summarize_first_path_triage(events)
    market_state_hypothesis = summarize_market_state_hypothesis(
        burst_summary,
        close_path_summary,
        first_path_triage,
    )
    rearm_timing_summary = summarize_rearm_timing(events)
    gap_summary = gap_payload.get("summary") if isinstance(gap_payload.get("summary"), dict) else {}
    event_log_mtime = file_mtime_iso(event_path)
    reference_code_mtime = file_mtime_iso(reference_code_path)
    event_log_is_newer_than_reference_code = bool(event_log_mtime and reference_code_mtime and event_log_mtime >= reference_code_mtime)
    gap_surface_present = (
        str(gap_payload.get("readiness") or "") == "telemetry_surface_present"
        or (
            int(gap_summary.get("required_missing_count", 0) or 0) == 0
            and int(gap_summary.get("required_partial_count", 0) or 0) == 0
            and int(gap_summary.get("required_spec_gap_count", 0) or 0) == 0
            and int(gap_summary.get("required_present_count", 0) or 0) > 0
        )
    )

    if not events:
        readiness = "no_runtime_events"
        next_action = "Wait for runtime events, then rebuild the board to validate Phase 1 field coverage."
    elif covered_fields == 0:
        if gap_surface_present:
            readiness = "stale_or_pre_enrichment_log"
            next_action = "The current event log predates the telemetry surface that is already present in code. Rebuild this board against a fresh post-enrichment runtime log before reading path quality from it."
        else:
            readiness = "awaiting_phase1_patch"
            next_action = "The inspected event log still predates the Phase 1 telemetry surface. Land the runtime event enrichment, then rebuild this board against a fresh post-patch log."
    elif covered_fields < total_fields:
        readiness = "phase1_partial"
        next_action = "The telemetry port is partially visible; finish the remaining zero-coverage Phase 1 fields before trusting the enriched event surface."
    else:
        readiness = "phase1_fields_present"
        next_action = "Phase 1 event fields are present in the current event log; review field values and then decide whether a compact runtime summary board is worth adding."

    return {
        "generated_at": now.isoformat(),
        "lane_label": lane_label,
        "source_event_path": display_path(event_path),
        "reference_code_path": display_path(reference_code_path) if reference_code_path else "",
        "readiness": readiness,
        "next_action": next_action,
        "deployment_context": {
            "event_log_mtime": event_log_mtime,
            "reference_code_mtime": reference_code_mtime,
            "event_log_is_newer_than_reference_code": event_log_is_newer_than_reference_code,
        },
        "summary": {
            "events_total": len(events),
            "open_ticket_count": sum(1 for event in events if str(event.get("action") or "") == "open_ticket"),
            "close_like_count": sum(1 for event in events if _is_close_like(event)),
            "rearm_open_count": sum(
                1
                for event in events
                if str(event.get("action") or "") == "open_ticket" and bool(event.get("rearm_open"))
            ),
            "field_count": total_fields,
            "covered_field_count": covered_fields,
            "zero_coverage_field_count": total_fields - covered_fields,
        },
        "same_tick_burst_summary": burst_summary,
        "close_path_summary": close_path_summary,
        "first_path_triage": first_path_triage,
        "market_state_hypothesis": market_state_hypothesis,
        "rearm_timing_summary": rearm_timing_summary,
        "sections": sections,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    burst = payload.get("same_tick_burst_summary") if isinstance(payload.get("same_tick_burst_summary"), dict) else {}
    close_path_summary = payload.get("close_path_summary") if isinstance(payload.get("close_path_summary"), dict) else {}
    first_path_triage = payload.get("first_path_triage") if isinstance(payload.get("first_path_triage"), dict) else {}
    market_state_hypothesis = payload.get("market_state_hypothesis") if isinstance(payload.get("market_state_hypothesis"), dict) else {}
    rearm_timing_summary = payload.get("rearm_timing_summary") if isinstance(payload.get("rearm_timing_summary"), dict) else {}
    deployment_context = payload.get("deployment_context") if isinstance(payload.get("deployment_context"), dict) else {}
    largest_cluster = burst.get("largest_cluster") if isinstance(burst.get("largest_cluster"), dict) else None

    lines = [
        "# Lattice Phase 1 Event Coverage Board",
        "",
        "> Current runtime generated board.",
        "> Use this to validate whether the Phase 1 lattice telemetry fields are actually appearing in the event log, without hand-scanning raw JSONL.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- lane_label: `{payload.get('lane_label', '')}`",
        f"- source_event_path: `{payload.get('source_event_path', '')}`",
        f"- reference_code_path: `{payload.get('reference_code_path', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## Summary",
        "",
        f"- events_total: `{int(summary.get('events_total', 0) or 0)}`",
        f"- open_ticket_count: `{int(summary.get('open_ticket_count', 0) or 0)}`",
        f"- close_like_count: `{int(summary.get('close_like_count', 0) or 0)}`",
        f"- rearm_open_count: `{int(summary.get('rearm_open_count', 0) or 0)}`",
        f"- covered_field_count: `{int(summary.get('covered_field_count', 0) or 0)}` / `{int(summary.get('field_count', 0) or 0)}`",
        f"- zero_coverage_field_count: `{int(summary.get('zero_coverage_field_count', 0) or 0)}`",
        "",
        "## Deployment Context",
        "",
        f"- event_log_mtime: `{deployment_context.get('event_log_mtime', '') or 'missing'}`",
        f"- reference_code_mtime: `{deployment_context.get('reference_code_mtime', '') or 'missing'}`",
        f"- event_log_is_newer_than_reference_code: `{bool(deployment_context.get('event_log_is_newer_than_reference_code', False))}`",
        "",
        "## Same-Tick Burst Summary",
        "",
        f"- cluster_count_ge_2: `{int(burst.get('cluster_count_ge_2', 0) or 0)}`",
        f"- max_open_count: `{int(burst.get('max_open_count', 0) or 0)}`",
    ]

    if largest_cluster:
        lines.extend(
            [
                f"- largest_cluster_symbol: `{largest_cluster.get('symbol', '')}`",
                f"- largest_cluster_direction: `{largest_cluster.get('direction', '')}`",
                f"- largest_cluster_ts_utc: `{largest_cluster.get('ts_utc', '')}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Close-Path Summary",
            "",
            f"- phase1_close_metric_event_count: `{int(close_path_summary.get('phase1_close_metric_event_count', 0) or 0)}`",
            f"- close_like_event_count: `{int(close_path_summary.get('close_like_event_count', 0) or 0)}`",
            f"- latest_close_like_ts_utc: `{close_path_summary.get('latest_close_like_ts_utc', '') or 'missing'}`",
            f"- ttfg_present_count: `{int(close_path_summary.get('ttfg_present_count', 0) or 0)}`",
            f"- ttfg_missing_count: `{int(close_path_summary.get('ttfg_missing_count', 0) or 0)}`",
            f"- loss_with_first_green_count: `{int(close_path_summary.get('loss_with_first_green_count', 0) or 0)}`",
            f"- loss_without_first_green_count: `{int(close_path_summary.get('loss_without_first_green_count', 0) or 0)}`",
            f"- avg_hold_seconds: `{close_path_summary.get('avg_hold_seconds', 'missing')}`",
            f"- median_time_to_first_green_seconds: `{close_path_summary.get('median_time_to_first_green_seconds', 'missing')}`",
            f"- avg_peak_pnl_before_exit: `{close_path_summary.get('avg_peak_pnl_before_exit', 'missing')}`",
            f"- avg_max_favorable_excursion_pnl: `{close_path_summary.get('avg_max_favorable_excursion_pnl', 'missing')}`",
            f"- avg_max_adverse_excursion_pnl: `{close_path_summary.get('avg_max_adverse_excursion_pnl', 'missing')}`",
            f"- reclaimed_trigger_level_count: `{int(close_path_summary.get('reclaimed_trigger_level_count', 0) or 0)}`",
            f"- retraced_half_step_count: `{int(close_path_summary.get('retraced_half_step_count', 0) or 0)}`",
            "",
            "## First-Path Triage",
            "",
            f"- verdict: `{first_path_triage.get('verdict', '') or 'missing'}`",
            f"- rationale: `{first_path_triage.get('rationale', '') or 'missing'}`",
            f"- first_open_ts_utc: `{first_path_triage.get('first_open_ts_utc', '') or 'missing'}`",
            f"- first_open_direction: `{first_path_triage.get('first_open_direction', '') or 'missing'}`",
            f"- first_open_entry_context: `{first_path_triage.get('first_open_entry_context', '') or 'missing'}`",
            f"- first_close_ts_utc: `{first_path_triage.get('first_close_ts_utc', '') or 'missing'}`",
            f"- first_close_action: `{first_path_triage.get('first_close_action', '') or 'missing'}`",
            f"- first_close_direction: `{first_path_triage.get('first_close_direction', '') or 'missing'}`",
            f"- first_close_realized_pnl: `{first_path_triage.get('first_close_realized_pnl', 'missing')}`",
            f"- first_close_time_to_first_green_seconds: `{first_path_triage.get('first_close_time_to_first_green_seconds', 'missing')}`",
            f"- first_close_peak_pnl_before_exit: `{first_path_triage.get('first_close_peak_pnl_before_exit', 'missing')}`",
            f"- first_close_reclaimed_trigger_level_seen: `{bool(first_path_triage.get('first_close_reclaimed_trigger_level_seen', False))}`",
            f"- first_close_retraced_0_5x_step_seen: `{bool(first_path_triage.get('first_close_retraced_0_5x_step_seen', False))}`",
            "",
            "## Market-State Hypothesis",
            "",
            f"- verdict: `{market_state_hypothesis.get('verdict', '') or 'missing'}`",
            f"- confidence: `{market_state_hypothesis.get('confidence', '') or 'missing'}`",
            f"- rationale: `{market_state_hypothesis.get('rationale', '') or 'missing'}`",
            f"- operator_question: `{market_state_hypothesis.get('operator_question', '') or 'missing'}`",
            "",
            "## Rearm Timing Summary",
            "",
            f"- rearm_open_count: `{int(rearm_timing_summary.get('rearm_open_count', 0) or 0)}`",
            f"- token_age_present_count: `{int(rearm_timing_summary.get('token_age_present_count', 0) or 0)}`",
            f"- armed_duration_present_count: `{int(rearm_timing_summary.get('armed_duration_present_count', 0) or 0)}`",
            f"- avg_token_age_at_fire_seconds: `{rearm_timing_summary.get('avg_token_age_at_fire_seconds', 'missing')}`",
            f"- avg_armed_duration_seconds: `{rearm_timing_summary.get('avg_armed_duration_seconds', 'missing')}`",
        ]
    )

    for section in sections:
        lines.extend(
            [
                "",
                f"## {section.get('label', '')}",
                "",
                f"- event_count: `{int(section.get('event_count', 0) or 0)}`",
                f"- covered_field_count: `{int(section.get('covered_field_count', 0) or 0)}` / `{int(section.get('field_count', 0) or 0)}`",
                "",
                "| Field | Coverage | Sample value |",
                "| --- | ---: | --- |",
            ]
        )
        for field in section.get("fields") or []:
            sample = field.get("sample_value")
            sample_text = json.dumps(sample) if sample is not None else ""
            lines.append(
                f"| `{field.get('name', '')}` | "
                f"`{int(field.get('coverage_count', 0) or 0)}/{int(field.get('event_count', 0) or 0)}` "
                f"({float(field.get('coverage_pct', 0.0) or 0.0):.1f}%) | "
                f"`{sample_text}` |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This board is about field visibility first, then compact path-shape legibility once those fields start appearing.",
            "A zero-coverage field means the current event log still cannot answer that causal question directly.",
            "The same-tick burst summary is derived from raw `open_ticket` clustering, so it stays useful even when the inspected log predates the new telemetry fields.",
            "The first-path triage verdict is the shortest honest read of the first enriched path event: waiting, opened-but-not-closed, never-green loss, went-green-but-lost, or monetized.",
            "The market-state hypothesis is the next layer up: it turns the first-path evidence into a compact temporary-impact vs repricing-risk read without pretending one path sample is a full regime model.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Phase 1 lattice event-coverage board.")
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH), help="Path to the lattice event log to inspect.")
    parser.add_argument("--lane-label", default="shadow_ethusd_m5_structure_shapeshifter", help="Label for the inspected lane.")
    parser.add_argument(
        "--reference-code-path",
        default=str(DEFAULT_REFERENCE_CODE_PATH),
        help="Reference telemetry code path used to show whether the inspected event log is post-enrichment.",
    )
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON), help="Output JSON path.")
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD), help="Output Markdown path.")
    args = parser.parse_args()

    event_path = Path(args.event_path)
    reference_code_path = Path(args.reference_code_path)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    events = load_jsonl(event_path)
    payload = build_payload(
        events=events,
        event_path=event_path,
        lane_label=str(args.lane_label or ""),
        reference_code_path=reference_code_path,
    )
    output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {output_json}")
    print(f"wrote {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
