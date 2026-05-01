#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

ETH_CONTROL_LANE_NAME = "hungry_hippo_ethusd_m5_step14_control"
ETH_CONTROL_CONFIG_LANE_NAME = "hungry_hippo_ethusd_m5_step14_control"
ETH_CONTROL_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_step14_control_state.json"
RESET_ALERTS_PATH = REPORTS / "reset_rate_alerts.json"
ETH_COMPARISON_PATH = REPORTS / "eth_m5_first_pilot_comparison_board.json"
DEPLOYMENT_GATE_PATH = REPORTS / "hungry_hippo_deployment_safety_gate_board.json"
ETH_CONTROL_CONFIG_PATH = CONFIGS / "hungry_hippo_ethusd_m5_step14_control.json"
RUNNER_REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"

OUTPUT_JSON_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
OUTPUT_MD_PATH = REPORTS / "eth_m5_control_proof_gate_board.md"

TARGET_CLOSES = 25
RESET_RATE_LIMIT = 6.0
GEOMETRY_TOLERANCE_RATIO = 0.25
STALE_AFTER_SECONDS = 240


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def find_reset_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for key in ("alerts", "safe_lanes", "kills"):
        for row in list(payload.get(key) or []):
            if str((row or {}).get("lane") or "") == lane_name:
                return dict(row)
    return {}


def find_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if str((row or {}).get("name") or "") == lane_name:
            return dict(row)
    return {}


def find_symbol_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str((row or {}).get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    return {}


def parse_iso_datetime(text: str) -> datetime | None:
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def ratio_delta(observed: float, expected: float) -> float:
    if expected <= 0:
        return 0.0
    return abs(observed - expected) / expected


def normalize_path_text(value: Any) -> str:
    return str(value or "").replace("\\", "/")


def relative_path_text(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def restart_arg_value(config: dict[str, Any], flag: str) -> str:
    args = list(config.get("restart_args") or [])
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return str(args[idx + 1] or "")
    return ""


def build_payload(
    control_state: dict[str, Any],
    reset_alerts: dict[str, Any],
    comparison_board: dict[str, Any],
    deployment_gate: dict[str, Any],
    control_config: dict[str, Any],
    runner_registry: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(control_state.get("metadata") or {})
    runner = dict(control_state.get("runner") or {})
    symbol_state = dict((control_state.get("symbols") or {}).get("ETHUSD") or {})
    gate_row = find_symbol_row(deployment_gate, "ETHUSD")
    reset_row = find_reset_lane(reset_alerts, ETH_CONTROL_LANE_NAME)
    expected_registry_lane_name = str(control_config.get("name") or ETH_CONTROL_CONFIG_LANE_NAME)
    launch_lane = find_lane(runner_registry, expected_registry_lane_name)

    now_dt = datetime.now(timezone.utc)
    declared_step = safe_float(metadata.get("step"))
    declared_step_buy = safe_float(metadata.get("step_buy"))
    declared_step_sell = safe_float(metadata.get("step_sell"))
    declared_step_price_units = safe_float(metadata.get("declared_step_price_units")) or declared_step
    declared_step_buy_price_units = safe_float(metadata.get("declared_step_buy_price_units")) or declared_step_buy or declared_step_price_units
    declared_step_sell_price_units = safe_float(metadata.get("declared_step_sell_price_units")) or declared_step_sell or declared_step_price_units
    runtime_base_step_buy_px = safe_float(symbol_state.get("base_step_buy_px"))
    runtime_base_step_sell_px = safe_float(symbol_state.get("base_step_sell_px"))
    anchor = safe_float(symbol_state.get("anchor"))
    next_buy_level = safe_float(symbol_state.get("next_buy_level"))
    next_sell_level = safe_float(symbol_state.get("next_sell_level"))
    effective_buy_distance = abs(anchor - next_buy_level) if anchor and next_buy_level else 0.0
    effective_sell_distance = abs(next_sell_level - anchor) if anchor and next_sell_level else 0.0
    realized_closes = safe_int(symbol_state.get("realized_closes"))
    realized_net_usd = safe_float(symbol_state.get("realized_net_usd"))
    avg_per_close = realized_net_usd / realized_closes if realized_closes > 0 else 0.0
    closes_remaining = max(0, TARGET_CLOSES - realized_closes)
    reset_rate = safe_float(reset_row.get("reset_rate_per_hour"))
    resets = safe_int(reset_row.get("resets"))
    heartbeat_at = str(runner.get("heartbeat_at") or "")
    heartbeat_dt = parse_iso_datetime(heartbeat_at)
    heartbeat_age_seconds = (now_dt - heartbeat_dt).total_seconds() if heartbeat_dt else 0.0
    runtime_stale = heartbeat_dt is None or heartbeat_age_seconds > STALE_AFTER_SECONDS

    reasons: list[str] = []
    registry_lane_found = bool(launch_lane)
    config_name = str(control_config.get("name") or "")
    registry_name = str(launch_lane.get("name") or "")
    name_alignment_ok = registry_lane_found and config_name == registry_name
    config_enabled = bool(control_config.get("enabled"))
    registry_enabled = bool(launch_lane.get("enabled"))
    enabled_alignment_ok = registry_lane_found and config_enabled == registry_enabled
    config_state_path = normalize_path_text(control_config.get("state_path"))
    registry_state_path = normalize_path_text(launch_lane.get("state_path"))
    control_board_state_path = relative_path_text(ETH_CONTROL_STATE_PATH)
    config_event_path = normalize_path_text(control_config.get("event_path"))
    registry_event_path = normalize_path_text(launch_lane.get("event_path"))
    launch_surface_paths_aligned = (
        registry_lane_found
        and bool(config_state_path)
        and bool(config_event_path)
        and config_state_path == registry_state_path
        and config_event_path == registry_event_path
    )
    config_declared_step = safe_float(restart_arg_value(control_config, "--step"))
    control_board_matches_launch_surface = bool(config_state_path) and normalize_path_text(control_board_state_path) == config_state_path
    control_state_registered_launch_lane = registry_lane_found and bool(registry_state_path) and normalize_path_text(control_board_state_path) == registry_state_path
    control_state_orphaned_from_registry = bool(control_board_state_path) and not control_state_registered_launch_lane
    declared_step_alignment_ok = config_declared_step <= 0 or config_declared_step == declared_step
    surface_alignment_blocked = (
        not registry_lane_found
        or not name_alignment_ok
        or not enabled_alignment_ok
        or not launch_surface_paths_aligned
        or not control_board_matches_launch_surface
        or not declared_step_alignment_ok
    )

    if not registry_lane_found:
        reasons.append("control_launch_registry_lane_missing")
    if registry_lane_found and not name_alignment_ok:
        reasons.append("control_launch_surface_name_mismatch")
    if not enabled_alignment_ok:
        reasons.append("control_launch_surface_enabled_mismatch")
    if not launch_surface_paths_aligned:
        reasons.append("control_launch_surface_path_mismatch")
    if not control_board_matches_launch_surface:
        reasons.append("control_board_and_launch_surface_split")
    if control_state_orphaned_from_registry:
        reasons.append("control_state_is_not_a_registered_launch_lane")
    if not declared_step_alignment_ok:
        reasons.append("control_board_and_launch_surface_declare_different_steps")

    declared_symmetric = declared_step > 0 and declared_step == declared_step_buy == declared_step_sell
    if not declared_symmetric:
        reasons.append("declared_control_not_symmetric")

    buy_drift_ratio = ratio_delta(
        runtime_base_step_buy_px if runtime_base_step_buy_px > 0 else effective_buy_distance,
        declared_step_buy_price_units,
    )
    sell_drift_ratio = ratio_delta(
        runtime_base_step_sell_px if runtime_base_step_sell_px > 0 else effective_sell_distance,
        declared_step_sell_price_units,
    )
    geometry_normalized = (
        declared_symmetric
        and declared_step_buy_price_units > 0
        and declared_step_sell_price_units > 0
        and runtime_base_step_buy_px > 0
        and runtime_base_step_sell_px > 0
        and not bool(metadata.get("dynamic_geometry_enabled", True))
        and buy_drift_ratio <= GEOMETRY_TOLERANCE_RATIO
        and sell_drift_ratio <= GEOMETRY_TOLERANCE_RATIO
    )
    if not geometry_normalized:
        reasons.append("runtime_ladder_not_matching_declared_step")
    if runtime_stale:
        reasons.append("runtime_heartbeat_stale")

    if reset_rate >= RESET_RATE_LIMIT:
        reasons.append("reset_rate_above_kill_limit")
    if realized_net_usd <= 0:
        reasons.append("realized_net_not_positive")
    if realized_closes < TARGET_CLOSES:
        reasons.append("proof_sample_below_target")

    comparison_status = str(comparison_board.get("comparison_status") or "")
    if comparison_status != "ready_for_clean_control_vs_variant":
        reasons.append("comparison_board_not_ready")

    if surface_alignment_blocked:
        verdict = "blocked_by_surface_alignment"
    elif runtime_stale:
        verdict = "blocked_by_stale_runtime"
    elif not geometry_normalized:
        verdict = "blocked_by_control_normalization"
    elif reset_rate >= RESET_RATE_LIMIT:
        verdict = "blocked_by_reset_safety"
    elif realized_net_usd <= 0:
        verdict = "blocked_by_negative_expectancy"
    elif realized_closes < TARGET_CLOSES:
        verdict = "continue_observation"
    elif comparison_status != "ready_for_clean_control_vs_variant":
        verdict = "ready_for_proof_but_not_clean_ab"
    else:
        verdict = "ready_for_offensive_ab"

    leadership_read = [
        "ETH M5 step14 can only be judged honestly if launch-surface alignment, runtime ladder shape, reset safety, and proof sample live in one board instead of being inferred across multiple files.",
        "The ETH proof board should point at one canonical step14 control surface, not a mixture of operator-facing step14 configs, stale registry entries, and orphaned state files.",
        "Positive closes are necessary but not sufficient; the room still cannot run an honest offensive-closure A/B until the judged board and the launch surface refer to the same control.",
    ]
    advance_when = [
        "the step14 proof artifact is either replaced by or promoted into one canonical configured launch lane",
        "step5 config and registry agree on the same enabled state and path surface",
        "the proof board and the launch surface point at the same ETH control lineage",
        "runtime heartbeat is fresh again and the judged control lane is demonstrably alive",
        "runtime ladder distances stay close to the declared step14 control instead of collapsing back toward step5 geometry",
        "realized closes reach at least 25 with positive realized net",
        "reset rate stays below 6/hour",
        "comparison status is clean enough to run OFF vs ON without mixed control truth",
    ]
    kill_when = [
        "config, registry, and the proof board keep disagreeing about which ETH lane is real while the room treats one of them as current proof",
        "the room treats the orphan step14 state artifact as if it were already a configured restorable lane",
        "runtime heartbeat stays stale or the judged lane silently dies while the room still treats the old snapshot as current proof",
        "runtime ladder keeps diverging materially from the declared control step",
        "reset rate breaches the kill-switch threshold",
        "realized net turns negative over the proof window",
        "the room starts using this control as live-promotion evidence before normalization is clean",
    ]

    if not surface_alignment_blocked:
        leadership_read[1] = (
            "The ETH proof board is now aligned to the registered step14 control surface; the remaining question is whether the live runtime behaves like an honest fixed-step baseline."
        )
        advance_when = [
            "runtime heartbeat stays fresh on the judged registered step14 lane",
            "runtime ladder distances stay close to the declared step14 control instead of collapsing into mixed geometry",
            "realized closes reach at least 25 with positive realized net",
            "reset rate stays below 6/hour",
            "comparison status is clean enough to run OFF vs ON without mixed control truth",
        ]
        kill_when = [
            "runtime heartbeat goes stale or the judged lane silently dies while the room still treats the old sample as current proof",
            "runtime ladder keeps diverging materially from the declared control step",
            "reset rate breaches the kill-switch threshold",
            "realized net stays negative over the proof window",
            "the room starts using this control as live-promotion evidence before normalization is clean",
        ]
        leadership_read[2] = (
            "Fresh alignment is not enough by itself; the room still cannot run an honest offensive-closure A/B until the ladder stays near declared step14 geometry and the control sample turns into honest positive proof."
            if not runtime_stale
            else "Fresh alignment is not enough by itself; the room still cannot run an honest offensive-closure A/B until the judged lane is alive again and the control sample is current."
        )
    leadership_read.append(
        "Control-runtime machine truth distinguishes runner step arguments (`14.0`) from converted quote-price geometry (`0.14`) so the room does not mistake unit conversion for a control mismatch."
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            relative_path_text(ETH_CONTROL_STATE_PATH),
            relative_path_text(ETH_CONTROL_CONFIG_PATH),
            relative_path_text(RUNNER_REGISTRY_PATH),
            relative_path_text(RESET_ALERTS_PATH),
            relative_path_text(ETH_COMPARISON_PATH),
            relative_path_text(DEPLOYMENT_GATE_PATH),
        ],
        "leadership_read": leadership_read,
        "summary": {
            "verdict": verdict,
            "surface_alignment_blocked": surface_alignment_blocked,
            "target_closes": TARGET_CLOSES,
            "realized_closes": realized_closes,
            "closes_remaining": closes_remaining,
            "realized_net_usd": round(realized_net_usd, 2),
            "avg_per_close": round(avg_per_close, 4),
            "reset_rate_per_hour": round(reset_rate, 4),
            "comparison_status": comparison_status,
        },
        "infra_alignment": {
            "launch_lane_name": config_name or ETH_CONTROL_CONFIG_LANE_NAME,
            "registry_lane_name": registry_name,
            "registry_lane_found": registry_lane_found,
            "config_enabled": config_enabled,
            "registry_enabled": registry_enabled,
            "enabled_alignment_ok": enabled_alignment_ok,
            "config_state_path": config_state_path,
            "registry_state_path": registry_state_path,
            "control_board_state_path": control_board_state_path,
            "config_event_path": config_event_path,
            "registry_event_path": registry_event_path,
            "launch_surface_paths_aligned": launch_surface_paths_aligned,
            "control_board_matches_launch_surface": control_board_matches_launch_surface,
            "control_state_registered_launch_lane": control_state_registered_launch_lane,
            "control_state_orphaned_from_registry": control_state_orphaned_from_registry,
            "config_declared_step": config_declared_step,
            "control_board_declared_step": declared_step,
            "declared_step_alignment_ok": declared_step_alignment_ok,
            "surface_alignment_blocked": surface_alignment_blocked,
        },
        "control_runtime": {
            "declared_step_runner_units": declared_step,
            "declared_step_buy_runner_units": declared_step_buy,
            "declared_step_sell_runner_units": declared_step_sell,
            "declared_step_quote_price_units": round(declared_step_price_units, 6),
            "declared_step_buy_quote_price_units": round(declared_step_buy_price_units, 6),
            "declared_step_sell_quote_price_units": round(declared_step_sell_price_units, 6),
            "runtime_base_step_buy_px": round(runtime_base_step_buy_px, 6),
            "runtime_base_step_sell_px": round(runtime_base_step_sell_px, 6),
            "raw_close_alpha": safe_float(metadata.get("raw_close_alpha")),
            "dynamic_geometry_enabled": bool(metadata.get("dynamic_geometry_enabled", True)),
            "heartbeat_at": heartbeat_at,
            "heartbeat_age_seconds": round(heartbeat_age_seconds, 1),
            "runtime_stale": runtime_stale,
            "pid": safe_int(runner.get("pid")),
            "anchor": anchor,
            "next_buy_level": next_buy_level,
            "next_sell_level": next_sell_level,
            "effective_buy_distance": round(effective_buy_distance, 6),
            "effective_sell_distance": round(effective_sell_distance, 6),
            "buy_drift_ratio": round(buy_drift_ratio, 4),
            "sell_drift_ratio": round(sell_drift_ratio, 4),
            "geometry_normalized": geometry_normalized,
            "open_ticket_count": len(list(symbol_state.get("open_tickets") or [])),
        },
        "proof_progress": {
            "target_closes": TARGET_CLOSES,
            "realized_closes": realized_closes,
            "closes_remaining": closes_remaining,
            "realized_net_usd": round(realized_net_usd, 2),
            "avg_per_close": round(avg_per_close, 4),
        },
        "reset_gate": {
            "lane": str(reset_row.get("lane") or ETH_CONTROL_LANE_NAME),
            "status": str(reset_row.get("status") or "UNKNOWN"),
            "resets": resets,
            "reset_rate_per_hour": round(reset_rate, 4),
            "limit": RESET_RATE_LIMIT,
            "safe": reset_rate < RESET_RATE_LIMIT,
        },
        "comparison_gate": {
            "comparison_status": comparison_status,
            "recommended_control_step": safe_float((comparison_board.get("normalization_recommendation") or {}).get("recommended_control_step")),
            "recommended_control_reason": str((comparison_board.get("normalization_recommendation") or {}).get("recommended_control_reason") or ""),
            "blocked_by": list((comparison_board.get("comparison_protocol") or {}).get("blocked_by") or []),
        },
        "deployment_gate_context": {
            "deployment_verdict": str(gate_row.get("deployment_verdict") or ""),
            "effective_spread_status": str(gate_row.get("effective_spread_status") or ""),
            "proof_closes": safe_int(gate_row.get("proof_closes")),
            "guardrail_status": str(gate_row.get("guardrail_status") or ""),
        },
        "blocking_reasons": reasons,
        "advance_when": advance_when,
        "kill_when": kill_when,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    infra_alignment = dict(payload.get("infra_alignment") or {})
    runtime = dict(payload.get("control_runtime") or {})
    proof = dict(payload.get("proof_progress") or {})
    reset_gate = dict(payload.get("reset_gate") or {})
    comparison_gate = dict(payload.get("comparison_gate") or {})
    deployment_context = dict(payload.get("deployment_gate_context") or {})

    lines = [
        "# ETH M5 Control Proof Gate Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: put ETH M5 launch-surface alignment, control truth, proof progress, reset safety, and A/B cleanliness into one gate so advancement decisions stop depending on scattered context.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Summary", ""])
    lines.append(f"- Verdict: `{summary.get('verdict', '')}`")
    lines.append(f"- Realized closes: `{summary.get('realized_closes', 0)}` / `{summary.get('target_closes', 0)}`")
    lines.append(f"- Closes remaining: `{summary.get('closes_remaining', 0)}`")
    lines.append(f"- Realized net USD: `{summary.get('realized_net_usd', 0)}`")
    lines.append(f"- Avg per close: `{summary.get('avg_per_close', 0)}`")
    lines.append(f"- Reset rate per hour: `{summary.get('reset_rate_per_hour', 0)}`")
    lines.append(f"- Comparison status: `{summary.get('comparison_status', '')}`")

    lines.extend(["", "## Infra Alignment", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in infra_alignment.items())}`")

    lines.extend(["", "## Control Runtime", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in runtime.items())}`")

    lines.extend(["", "## Proof Progress", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in proof.items())}`")

    lines.extend(["", "## Reset Gate", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in reset_gate.items())}`")

    lines.extend(["", "## Comparison Gate", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in comparison_gate.items() if k != 'blocked_by')}`")
    blocked_by = list(comparison_gate.get("blocked_by") or [])
    if blocked_by:
        lines.append(f"- Blocked by: `{'; '.join(blocked_by)}`")

    lines.extend(["", "## Deployment Gate Context", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in deployment_context.items())}`")

    blocking_reasons = list(payload.get("blocking_reasons") or [])
    lines.extend(["", "## Blocking Reasons", ""])
    lines.append(f"- Reasons: `{'; '.join(blocking_reasons) if blocking_reasons else 'none'}`")

    lines.extend(["", "## Advance When", ""])
    for item in list(payload.get("advance_when") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Kill When", ""])
    for item in list(payload.get("kill_when") or []):
        lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(ETH_CONTROL_STATE_PATH),
        load_json(RESET_ALERTS_PATH),
        load_json(ETH_COMPARISON_PATH),
        load_json(DEPLOYMENT_GATE_PATH),
        load_json(ETH_CONTROL_CONFIG_PATH),
        load_json(RUNNER_REGISTRY_PATH),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
