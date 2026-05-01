#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUTPUT_JSON = REPORTS / "lattice_telemetry_gap_board.json"
OUTPUT_MD = REPORTS / "lattice_telemetry_gap_board.md"

BEHAVIOR_SPEC = ROOT / "docs" / "behavior-based-entry-spec.md"
LATTICE_CORE = ROOT / "scripts" / "tick_penetration_lattice_core.py"
LATTICE_RUNTIME = ROOT / "scripts" / "live_penetration_lattice_tick_crypto_shadow.py"

TARGET_METRICS: list[dict[str, Any]] = [
    {
        "id": "time_to_first_green",
        "label": "Time To First Green",
        "category": "per_ticket_lifecycle",
        "phase": "phase1",
        "target_fields": ["time_to_first_green_seconds"],
        "behavior_patterns": [r"\btime_to_first_green_seconds\b"],
        "lattice_patterns": [r"\btime_to_first_green_seconds\b"],
    },
    {
        "id": "mfe",
        "label": "Max Favorable Excursion",
        "category": "per_ticket_lifecycle",
        "phase": "phase1",
        "target_fields": ["max_favorable_excursion_pnl"],
        "behavior_patterns": [r"\bmax_favorable_excursion_(?:pnl|atr)\b"],
        "lattice_patterns": [r"\bmax_favorable_excursion_pnl\b"],
        "deferred_fields": ["max_favorable_excursion_atr"],
    },
    {
        "id": "mae",
        "label": "Max Adverse Excursion",
        "category": "per_ticket_lifecycle",
        "phase": "phase1",
        "target_fields": ["max_adverse_excursion_pnl"],
        "behavior_patterns": [r"\bmax_adverse_excursion_(?:pnl|atr)\b"],
        "lattice_patterns": [r"\bmax_adverse_excursion_pnl\b"],
        "deferred_fields": ["max_adverse_excursion_atr"],
    },
    {
        "id": "peak_pnl_before_exit",
        "label": "Peak PnL Before Exit",
        "category": "per_ticket_lifecycle",
        "phase": "phase1",
        "target_fields": ["peak_pnl_before_exit"],
        "behavior_patterns": [r"\bpeak_pnl_before_exit\b"],
        "lattice_patterns": [r"\bpeak_pnl_before_exit\b"],
    },
    {
        "id": "hold_seconds",
        "label": "Hold Seconds",
        "category": "per_ticket_lifecycle",
        "phase": "phase1",
        "target_fields": ["hold_seconds"],
        "behavior_patterns": [r"\bhold_seconds\b"],
        "lattice_patterns": [r"\bhold_seconds\b"],
    },
    {
        "id": "first_green_before_fail",
        "label": "First Green Before Fail",
        "category": "per_ticket_lifecycle",
        "phase": "phase1",
        "target_fields": ["first_green_before_fail"],
        "behavior_patterns": [r"\bfirst_green_before_fail\b"],
        "lattice_patterns": [r"\bfirst_green_before_fail\b"],
    },
    {
        "id": "spread_at_entry",
        "label": "Spread At Entry",
        "category": "entry_context",
        "phase": "phase1",
        "target_fields": ["spread_at_entry"],
        "behavior_patterns": [r"\bspread_at_entry\b"],
        "lattice_patterns": [r"\bspread_at_entry\b"],
    },
    {
        "id": "entry_context",
        "label": "Entry Context",
        "category": "entry_context",
        "phase": "phase1",
        "target_fields": ["entry_context"],
        "behavior_patterns": [r"\bentry_context\b"],
        "lattice_patterns": [r"\bentry_context\b"],
    },
    {
        "id": "regime_at_entry",
        "label": "Regime At Entry",
        "category": "entry_context",
        "phase": "phase2",
        "required_for_readiness": False,
        "target_fields": ["regime_at_entry"],
        "behavior_patterns": [r"\bregime_at_entry\b"],
        "lattice_patterns": [r"\bregime_at_entry\b"],
    },
    {
        "id": "tick_source_context",
        "label": "Tick Source And Session Context",
        "category": "entry_context",
        "phase": "phase1",
        "target_fields": ["latest_tick_source_last", "tick_history_source_last", "session_bucket"],
        "behavior_patterns": [],
        "lattice_patterns": [
            r"\blatest_tick_source_last\b",
            r"\btick_history_source_last\b",
            r"\bshared_price_cache\b",
            r"\bsession_bucket\b",
        ],
    },
    {
        "id": "rearm_token_age",
        "label": "Rearm Token Age At Fire",
        "category": "rearm_specific",
        "phase": "phase1",
        "target_fields": ["token_age_at_fire", "armed_duration_seconds"],
        "behavior_patterns": [],
        "lattice_patterns": [r"\btoken_age_at_fire_seconds\b", r"\barmed_duration_seconds\b", r"\bcreated_time\b", r"\barmed_at_time\b"],
    },
    {
        "id": "rearm_outcome_metrics",
        "label": "Rearm Outcome Metrics",
        "category": "rearm_specific",
        "phase": "phase1",
        "target_fields": ["rearm_to_first_green_seconds", "rearm_to_fail_seconds"],
        "behavior_patterns": [],
        "lattice_patterns": [r"\brearm_to_first_green_seconds\b", r"\brearm_to_fail_seconds\b"],
    },
    {
        "id": "inventory_pressure_summary",
        "label": "Inventory Pressure Summary",
        "category": "inventory_summary",
        "phase": "phase1",
        "target_fields": ["inventory_age_skew", "realized_to_floating_conversion_efficiency"],
        "behavior_patterns": [],
        "lattice_patterns": [r"\bopen_tickets\b", r"\brealized_net_usd\b", r"\brealized_closes\b"],
    },
    {
        "id": "penetration_quality_summary",
        "label": "Penetration Quality Summary",
        "category": "inventory_summary",
        "phase": "phase1",
        "target_fields": ["same_bar_round_trip_rate", "no_retrace_after_penetration_count"],
        "behavior_patterns": [],
        "lattice_patterns": [r"\breclaimed_trigger_level_seen\b", r"\bretraced_0_25x_step_seen\b", r"\bretraced_0_5x_step_seen\b"],
    },
]


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def count_matches(text: str, patterns: list[str]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text))


def metric_status(metric: dict[str, Any], *, behavior_text: str, lattice_text: str) -> dict[str, Any]:
    behavior_match_count = count_matches(behavior_text, list(metric.get("behavior_patterns") or []))
    lattice_match_count = count_matches(lattice_text, list(metric.get("lattice_patterns") or []))
    target_fields = list(metric.get("target_fields") or [])
    deferred_fields = list(metric.get("deferred_fields") or [])
    behavior_expected = len(list(metric.get("behavior_patterns") or []))
    lattice_expected = len(list(metric.get("lattice_patterns") or []))
    required_for_readiness = bool(metric.get("required_for_readiness", True))
    phase = str(metric.get("phase") or "phase1")

    if not required_for_readiness and lattice_match_count < lattice_expected:
        status = "deferred"
    elif behavior_expected > 0 and behavior_match_count == 0:
        status = "spec_gap"
    elif lattice_expected == 0:
        status = "missing"
    elif lattice_match_count >= lattice_expected:
        status = "present"
    elif lattice_match_count > 0:
        status = "partial"
    else:
        status = "missing"

    return {
        "id": str(metric.get("id") or ""),
        "label": str(metric.get("label") or ""),
        "category": str(metric.get("category") or ""),
        "phase": phase,
        "required_for_readiness": required_for_readiness,
        "target_fields": target_fields,
        "deferred_fields": deferred_fields,
        "status": status,
        "behavior_match_count": behavior_match_count,
        "behavior_expected_count": behavior_expected,
        "lattice_match_count": lattice_match_count,
        "lattice_expected_count": lattice_expected,
    }


def build_payload(
    *,
    now: datetime | None = None,
    behavior_text: str | None = None,
    lattice_core_text: str | None = None,
    lattice_runtime_text: str | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    behavior_text = behavior_text if behavior_text is not None else load_text(BEHAVIOR_SPEC)
    lattice_core_text = lattice_core_text if lattice_core_text is not None else load_text(LATTICE_CORE)
    lattice_runtime_text = lattice_runtime_text if lattice_runtime_text is not None else load_text(LATTICE_RUNTIME)
    lattice_text = "\n".join(part for part in (lattice_core_text, lattice_runtime_text) if part)

    metrics = [metric_status(metric, behavior_text=behavior_text, lattice_text=lattice_text) for metric in TARGET_METRICS]
    required_metrics = [metric for metric in metrics if metric.get("required_for_readiness", True)]
    summary = {
        "total_metrics": len(metrics),
        "present_count": sum(1 for metric in metrics if metric["status"] == "present"),
        "partial_count": sum(1 for metric in metrics if metric["status"] == "partial"),
        "missing_count": sum(1 for metric in metrics if metric["status"] == "missing"),
        "spec_gap_count": sum(1 for metric in metrics if metric["status"] == "spec_gap"),
        "deferred_count": sum(1 for metric in metrics if metric["status"] == "deferred"),
        "required_metric_count": len(required_metrics),
        "required_present_count": sum(1 for metric in required_metrics if metric["status"] == "present"),
        "required_partial_count": sum(1 for metric in required_metrics if metric["status"] == "partial"),
        "required_missing_count": sum(1 for metric in required_metrics if metric["status"] == "missing"),
        "required_spec_gap_count": sum(1 for metric in required_metrics if metric["status"] == "spec_gap"),
    }

    if summary["required_missing_count"] > 0 or summary["required_spec_gap_count"] > 0:
        readiness = "telemetry_port_needed"
        next_action = "Port the missing path-shape metrics into the tick-native lattice runtime before trusting more adaptive geometry or rearm changes."
    elif summary["required_partial_count"] > 0:
        readiness = "partial_port_present"
        next_action = "Tighten the partially-present lattice telemetry into explicit per-ticket/path-shape metrics before expanding adaptive logic."
    else:
        readiness = "telemetry_surface_present"
        next_action = "The minimum telemetry surface appears present; validate that it is persisted and reviewable before using it for control changes."

    return {
        "generated_at": now.isoformat(),
        "readiness": readiness,
        "next_action": next_action,
        "sources": {
            "behavior_spec": str(BEHAVIOR_SPEC.relative_to(ROOT)),
            "lattice_core": str(LATTICE_CORE.relative_to(ROOT)),
            "lattice_runtime": str(LATTICE_RUNTIME.relative_to(ROOT)),
        },
        "summary": summary,
        "metrics": metrics,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), list) else []
    lines = [
        "# Lattice Telemetry Gap Board",
        "",
        "> Current runtime generated board.",
        "> Use this as the concrete checklist for Task 28: port behavior/path-shape telemetry into the tick-native lattice family before trusting more adaptive geometry or rearm changes.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## Summary",
        "",
        f"- total_metrics: `{int(summary.get('total_metrics', 0) or 0)}`",
        f"- present_count: `{int(summary.get('present_count', 0) or 0)}`",
        f"- partial_count: `{int(summary.get('partial_count', 0) or 0)}`",
        f"- missing_count: `{int(summary.get('missing_count', 0) or 0)}`",
        f"- spec_gap_count: `{int(summary.get('spec_gap_count', 0) or 0)}`",
        f"- deferred_count: `{int(summary.get('deferred_count', 0) or 0)}`",
        f"- required_metric_count: `{int(summary.get('required_metric_count', 0) or 0)}`",
        f"- required_present_count: `{int(summary.get('required_present_count', 0) or 0)}`",
        f"- required_partial_count: `{int(summary.get('required_partial_count', 0) or 0)}`",
        f"- required_missing_count: `{int(summary.get('required_missing_count', 0) or 0)}`",
        f"- required_spec_gap_count: `{int(summary.get('required_spec_gap_count', 0) or 0)}`",
        "",
        "## Metric Matrix",
        "",
        "| Metric | Category | Phase | Status | Target fields | Deferred fields | Behavior spec | Lattice runtime |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]

    for metric in metrics:
        lines.append(
            f"| `{metric.get('label', '')}` | `{metric.get('category', '')}` | `{metric.get('phase', '')}` | `{metric.get('status', '')}` | "
            f"`{', '.join(metric.get('target_fields') or [])}` | "
            f"`{', '.join(metric.get('deferred_fields') or [])}` | "
            f"`{int(metric.get('behavior_match_count', 0) or 0)}/{int(metric.get('behavior_expected_count', 0) or 0)}` | "
            f"`{int(metric.get('lattice_match_count', 0) or 0)}/{int(metric.get('lattice_expected_count', 0) or 0)}` |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A `present` row means the lattice runtime already exposes the expected vocabulary strongly enough to support a real implementation pass.",
            "A `partial` row means related plumbing exists, but the task-specific metric still needs explicit names or persisted fields.",
            "A `missing` row means the metric is part of the requested Task 28 telemetry surface but does not currently appear in the tick-native lattice code path.",
            "A `deferred` row means the metric is intentionally outside the Phase 1 readiness bar and should not block the minimum telemetry port.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
