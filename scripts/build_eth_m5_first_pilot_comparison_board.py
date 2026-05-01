#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

SALVAGE_BOARD_PATH = REPORTS / "m5_warp_salvage_board.json"
ETH_PROBE_PATH = REPORTS / "eth_m5_hungry_hippo_probe.json"
ETH_CONTROL_CONFIG_PATH = CONFIGS / "hungry_hippo_ethusd_m5_step14_control.json"
ETH_CONTROL_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_step14_control_state.json"
OFFENSIVE_BOARD_PATH = REPORTS / "offensive_extreme_closure_shadow_board.json"
SPREAD_ROBUSTNESS_PATH = REPORTS / "spread_robustness.json"

OUTPUT_JSON_PATH = REPORTS / "eth_m5_first_pilot_comparison_board.json"
OUTPUT_MD_PATH = REPORTS / "eth_m5_first_pilot_comparison_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def salvage_row(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if isinstance(row, dict) and str(row.get("lane") or "") == lane_name:
            return row
    raise KeyError(f"salvage row not found: {lane_name}")


def board_row(payload: dict[str, Any], pilot_name: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("pilot") or "") == pilot_name:
            return row
    raise KeyError(f"pilot row not found: {pilot_name}")


def first_pilot_row(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    first_pilot = str(summary.get("first_pilot") or "")
    if first_pilot:
        return board_row(payload, first_pilot)
    rows = list(payload.get("rows") or [])
    if rows:
        return dict(rows[0])
    raise KeyError("offensive board has no pilot rows")


def restart_arg_value(config: dict[str, Any], flag: str, default: str = "") -> str:
    args = list(config.get("restart_args") or [])
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return str(args[idx + 1])
    return default


def build_payload(
    salvage_board: dict[str, Any],
    eth_probe: dict[str, Any],
    current_config: dict[str, Any],
    running_state: dict[str, Any],
    offensive_board: dict[str, Any],
    spread_robustness: dict[str, Any],
) -> dict[str, Any]:
    historical_shadow = salvage_row(salvage_board, "shadow_ethusd_m5_warp_5")
    failed_live = salvage_row(salvage_board, "live_ethusd_m5_warp")
    offensive_pilot = first_pilot_row(offensive_board)

    current_symbol = dict((running_state.get("symbols") or {}).get("ETHUSD") or {})
    current_runner = dict(running_state.get("runner") or {})
    current_meta = dict(running_state.get("metadata") or {})

    historical_probe_cfg = dict(eth_probe.get("probe_config") or {})
    historical_probe_meta = dict(eth_probe.get("shadow_baseline") or {})

    config_step = restart_arg_value(current_config, "--step")
    config_timeframe = restart_arg_value(current_config, "--timeframe")

    current_base_step = float(current_meta.get("step") or 0.0)
    current_step_buy = float(current_symbol.get("base_step_buy_px") or 0.0)
    current_step_sell = float(current_symbol.get("base_step_sell_px") or 0.0)
    declared_step_buy_price_units = float(current_meta.get("declared_step_buy_price_units") or 0.0)
    declared_step_sell_price_units = float(current_meta.get("declared_step_sell_price_units") or 0.0)
    dynamic_geometry_enabled = bool(current_meta.get("dynamic_geometry_enabled", True))
    runtime_shape_drift = (
        (declared_step_buy_price_units > 0 and abs(current_step_buy - declared_step_buy_price_units) / declared_step_buy_price_units > 0.25)
        or (declared_step_sell_price_units > 0 and abs(current_step_sell - declared_step_sell_price_units) / declared_step_sell_price_units > 0.25)
    )

    archival_vs_current_conflict = (
        config_step != restart_arg_value(historical_probe_cfg, "--step")
        or bool(current_config.get("enabled")) != bool(historical_probe_cfg.get("enabled"))
    )
    config_vs_runtime_conflict = abs(float(config_step or 0.0) - current_base_step) > 1e-9

    comparison_status = (
        "blocked_until_control_normalized"
        if (config_vs_runtime_conflict or dynamic_geometry_enabled or runtime_shape_drift)
        else "ready_for_clean_control_vs_variant"
    )
    eth_spread = dict(spread_robustness.get("ETHUSD") or {})
    min_viable_step = float(eth_spread.get("min_viable_step") or 0.0)
    config_step_float = float(config_step or 0.0)
    recommended_control_step = max(config_step_float, min_viable_step) if (config_step_float or min_viable_step) else 0.0
    runtime_read = "current runtime is not yet a clean fixed-step control because dynamic geometry widening is still enabled"
    if not dynamic_geometry_enabled and runtime_shape_drift:
        runtime_read = "current runtime is frozen, but the live ladder still drifts away from the declared fixed-step control"
    elif not dynamic_geometry_enabled and not runtime_shape_drift:
        runtime_read = "current runtime is a frozen fixed-step control candidate on the declared price-unit ladder"

    blocked_by = []
    if config_vs_runtime_conflict:
        blocked_by.append("running state still reports a different base step than the checked-in control config")
    if dynamic_geometry_enabled:
        blocked_by.append("running state still has dynamic geometry enabled on what should be a fixed-step control")
    elif runtime_shape_drift:
        blocked_by.append("running state still drifts away from the declared fixed-step buy/sell price ladder")

    normalization_recommendation = {
        "recommended_control_step": round(recommended_control_step, 4),
        "recommended_control_reason": (
            "Current config step already clears the latest min-viable spread floor; freeze this as the comparison control and disable dynamic geometry changes for the first A/B."
            if config_step_float >= min_viable_step and min_viable_step > 0
            else "Current config step does not clear the latest min-viable spread floor; widen to at least the spread-safe floor before any A/B."
        ),
        "spread_status": str(eth_spread.get("status") or ""),
        "spread_floor_context": (
            "Historical step5 shelf is below the recorded spread-safe floor and must remain archival context only."
            if min_viable_step > 0
            else "No spread-floor context available."
        ),
    }
    control_options = [
        {
            "option": "A_restore_step5_as_control",
            "verdict": "reject_as_current_control",
            "why": "Historical step5 shelf was positive, but the archived spread gate says step5 is below the min-viable spread floor and therefore cannot be treated as today's honest control.",
        },
        {
            "option": "B_use_step14_as_control",
            "verdict": "recommended_current_control",
            "why": "Step14 is the current config truth and clears the recorded min-viable spread floor; if geometry adaptation is frozen, it is the cleanest first control for OFF vs ON comparison.",
        },
        {
            "option": "C_run_step5_and_step14_parallel_controls",
            "verdict": "too_many_variables_for_first_ab",
            "why": "Useful later for shape research, but it contaminates the first offensive-closure judgment by changing both step and closure mechanic at once.",
        },
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SALVAGE_BOARD_PATH.relative_to(ROOT)),
            str(ETH_PROBE_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_CONFIG_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_STATE_PATH.relative_to(ROOT)),
            str(OFFENSIVE_BOARD_PATH.relative_to(ROOT)),
            str(SPREAD_ROBUSTNESS_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "ETH M5 remains the right first offensive-closure pilot, but archival step5 shelf evidence must stay archival context only while the actual comparison arm is the current spread-safe step14 control.",
            "No one can honestly claim that offensive closure helped or hurt ETH M5 unless the control arm is a frozen same-shape baseline. Once that hygiene is clean, the remaining blocker is proof quality, not shelf history.",
            "The first honest job is therefore not promotion but comparison hygiene: freeze one spread-safe ETH M5 control, then compare offensive-closure-on versus offensive-closure-off on that same shape.",
        ],
        "comparison_status": comparison_status,
        "historical_baseline": {
            "lane": "shadow_ethusd_m5_warp_5",
            "step": float(historical_shadow.get("step") or 0.0),
            "avg_per_close": float(historical_shadow.get("avg_per_close") or 0.0),
            "realized_closes": int(historical_shadow.get("realized_closes") or 0),
            "realized_net_usd": float(historical_shadow.get("realized_net_usd") or 0.0),
            "resets": int(historical_shadow.get("total_resets") or 0),
            "archival_probe_step": restart_arg_value(historical_probe_cfg, "--step"),
            "archival_probe_enabled": bool(historical_probe_cfg.get("enabled")),
            "archival_vs_current_conflict": archival_vs_current_conflict,
            "archival_probe_read": "historical shelf only; do not treat as current control truth",
        },
        "failed_live_reference": {
            "step": float(failed_live.get("step") or 0.0),
            "avg_per_close": float(failed_live.get("avg_per_close") or 0.0),
            "realized_closes": int(failed_live.get("realized_closes") or 0),
            "realized_net_usd": float(failed_live.get("realized_net_usd") or 0.0),
        },
        "current_control_candidate": {
            "config_step": config_step,
            "config_timeframe": config_timeframe,
            "config_enabled": bool(current_config.get("enabled")),
            "runtime_base_step": current_base_step,
            "runtime_step_buy": current_step_buy,
            "runtime_step_sell": current_step_sell,
            "runtime_realized_closes": int(current_symbol.get("realized_closes") or 0),
            "runtime_realized_net_usd": float(current_symbol.get("realized_net_usd") or 0.0),
            "runtime_pid": int(current_runner.get("pid") or 0),
            "runtime_heartbeat_at": str(current_runner.get("heartbeat_at") or ""),
            "runtime_read": runtime_read,
        },
        "offensive_variant_hypothesis": {
            "pilot_rank": str(offensive_pilot.get("status") or ""),
            "close_scope": str((offensive_pilot.get("proposed_shadow_spec") or {}).get("close_scope") or ""),
            "close_window": str((offensive_pilot.get("proposed_shadow_spec") or {}).get("close_window") or ""),
            "funding_rule": str((offensive_pilot.get("proposed_shadow_spec") or {}).get("funding_rule") or ""),
            "graduation_gate": str(offensive_pilot.get("graduation_gate") or ""),
        },
        "spread_gate": {
            "archival_spread_status": str(eth_spread.get("status") or ""),
            "archival_effective_step": float(eth_spread.get("effective_step") or 0.0),
            "archival_min_viable_step": float(eth_spread.get("min_viable_step") or 0.0),
            "archival_ratio": float(eth_spread.get("ratio") or 0.0),
            "archival_verdict": str(eth_spread.get("verdict") or ""),
        },
        "normalization_recommendation": normalization_recommendation,
        "control_options": control_options,
        "comparison_protocol": {
            "control_arm": "ETH M5 normalized spread-safe shadow shape with offensive closure OFF",
            "variant_arm": "same ETH M5 shape with offensive closure ON",
            "must_hold_constant": [
                "same symbol",
                "same timeframe",
                "same step/shape family",
                "same escape-hatch stack",
                "same no-session-gate posture",
                "same geometry adaptation posture",
            ],
            "blocked_by": [
                *blocked_by,
            ],
            "success_conditions": [
                "carry drag falls without making avg_per_close worse than the normalized control",
                "cuts stay near flat extremes instead of acting like hidden stop loss",
                "realized inner harvest clearly subsidizes the cuts",
            ],
            "invalid_conclusions": [
                "step5 historical shelf beats step14 control therefore offensive closure works",
                "current runtime drift can be mixed with archival shelf evidence and still count as one lane",
                "pilot profitability alone proves the closure mechanic",
            ],
        },
        "next_actions": [
            f"Choose one current ETH M5 control truth and freeze it for comparison. Current best candidate is step {round(recommended_control_step, 4)} with dynamic geometry held constant.",
            "Keep the archival step5 shelf separate from the current step14 control; it is context for why step14 was chosen, not a live blocker on comparison hygiene.",
            "Do not interpret the first pilot until control-vs-variant uses the same normalized ETH M5 shape.",
            "Once normalized, compare offensive closure ON vs OFF on ETH M5 before any live discussion.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# ETH M5 First Pilot Comparison Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: keep the first offensive-extreme-closure pilot honest by separating historical ETH M5 shelf evidence from the current control arm and blocking contaminated comparisons.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Status", ""])
    lines.append(f"- Comparison status: `{payload.get('comparison_status', '')}`")

    historical = dict(payload.get("historical_baseline") or {})
    lines.extend(["", "## Historical Baseline", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in historical.items())}`")

    failed = dict(payload.get("failed_live_reference") or {})
    lines.extend(["", "## Failed Live Reference", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in failed.items())}`")

    current = dict(payload.get("current_control_candidate") or {})
    lines.extend(["", "## Current Control Candidate", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in current.items())}`")

    variant = dict(payload.get("offensive_variant_hypothesis") or {})
    lines.extend(["", "## Offensive Variant Hypothesis", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in variant.items())}`")

    spread_gate = dict(payload.get("spread_gate") or {})
    lines.extend(["", "## Spread Gate", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in spread_gate.items())}`")

    normalization = dict(payload.get("normalization_recommendation") or {})
    lines.extend(["", "## Normalization Recommendation", ""])
    lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in normalization.items())}`")

    lines.extend(["", "## Control Choice", ""])
    for row in list(payload.get("control_options") or []):
        lines.append(f"- `{row.get('option', '')}` -> `{row.get('verdict', '')}`: {row.get('why', '')}")

    protocol = dict(payload.get("comparison_protocol") or {})
    lines.extend(["", "## Comparison Protocol", ""])
    lines.append(f"- Control arm: `{protocol.get('control_arm', '')}`")
    lines.append(f"- Variant arm: `{protocol.get('variant_arm', '')}`")
    must_hold_constant = list(protocol.get("must_hold_constant") or [])
    if must_hold_constant:
        lines.append(f"- Must hold constant: `{'; '.join(must_hold_constant)}`")
    blocked_by = list(protocol.get("blocked_by") or [])
    if blocked_by:
        lines.append(f"- Blocked by: `{'; '.join(blocked_by)}`")
    success_conditions = list(protocol.get("success_conditions") or [])
    if success_conditions:
        lines.append(f"- Success conditions: `{'; '.join(success_conditions)}`")
    invalid_conclusions = list(protocol.get("invalid_conclusions") or [])
    if invalid_conclusions:
        lines.append(f"- Invalid conclusions: `{'; '.join(invalid_conclusions)}`")

    lines.extend(["", "## Next Actions", ""])
    for item in list(payload.get("next_actions") or []):
        lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(SALVAGE_BOARD_PATH),
        load_json(ETH_PROBE_PATH),
        load_json(ETH_CONTROL_CONFIG_PATH),
        load_json(ETH_CONTROL_STATE_PATH),
        load_json(OFFENSIVE_BOARD_PATH),
        load_json(SPREAD_ROBUSTNESS_PATH),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
