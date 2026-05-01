#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_hungry_hippo_next_action_board import parse_eth_step14_coefficient_analysis


ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
REPORTS = ROOT / "reports"

CONTROL_CONFIG_PATH = CONFIGS / "hungry_hippo_ethusd_m5_step14_control.json"
CONTROL_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_step14_control_state.json"
CONTROL_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
NEXT_ACTION_PATH = REPORTS / "hungry_hippo_next_action_board.json"
COEFFICIENT_ANALYSIS_PATH = REPORTS / "eth_step14_coefficient_analysis.md"

OUTPUT_LANE_NAME = "hungry_hippo_ethusd_m5_step3p0_retuned_shadow"
OUTPUT_STATE_PATH = "reports/penetration_lattice_shadow_ethusd_m5_step3p0_retuned_shadow_state.json"
OUTPUT_EVENT_PATH = "reports/penetration_lattice_shadow_ethusd_m5_step3p0_retuned_shadow_events.jsonl"
OUTPUT_CONFIG_PATH = CONFIGS / "hungry_hippo_ethusd_m5_step3p0_retuned_shadow.json"
OUTPUT_JSON_PATH = REPORTS / "eth_m5_retuned_shadow_handoff.json"
OUTPUT_MD_PATH = REPORTS / "eth_m5_retuned_shadow_handoff.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def action_row(payload: dict[str, Any], action: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("action") or "") == action:
            return row
    raise KeyError(f"action not found: {action}")


def replace_arg_value(args: list[str], flag: str, value: str) -> list[str]:
    updated = list(args)
    for idx, item in enumerate(updated):
        if str(item) == flag and idx + 1 < len(updated):
            updated[idx + 1] = value
            return updated
    raise KeyError(f"flag not found: {flag}")


def build_retuned_config(control_config: dict[str, Any], recommended_step_usd: float) -> dict[str, Any]:
    restart_args = list(control_config.get("restart_args") or [])
    step_text = f"{recommended_step_usd:.1f}"
    restart_args = replace_arg_value(restart_args, "--step", step_text)
    restart_args = replace_arg_value(restart_args, "--step-buy", step_text)
    restart_args = replace_arg_value(restart_args, "--step-sell", step_text)
    restart_args = replace_arg_value(restart_args, "--state-path", OUTPUT_STATE_PATH)
    restart_args = replace_arg_value(restart_args, "--event-path", OUTPUT_EVENT_PATH)

    control_meta = dict(control_config.get("hungry_hippo_metadata") or {})
    guardrails = dict(control_meta.get("guardrails") or {})
    return {
        "name": OUTPUT_LANE_NAME,
        "kind": str(control_config.get("kind") or "shadow_crypto"),
        "state_path": OUTPUT_STATE_PATH,
        "event_path": OUTPUT_EVENT_PATH,
        "poll_seconds": int(control_config.get("poll_seconds") or 30),
        "stale_after_seconds": int(control_config.get("stale_after_seconds") or 240),
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            Path(OUTPUT_STATE_PATH).name,
        ],
        "restart_args": restart_args,
        "enabled": False,
        "watchdog_group": str(control_config.get("watchdog_group") or "crypto_watchdog"),
        "hungry_hippo_metadata": {
            "personality": str(control_meta.get("personality") or "NO_SESSION_GATE_HARVEST"),
            "probe_source": "eth_step14_negative_proof_retune_handoff",
            "salvage_verdict": "retuned_shadow_candidate",
            "regime_alignment": (
                "ETH M5 retuned symmetric control cloned from step14 posture, with fixed step changed "
                f"to {step_text} after negative expectancy on the aligned step14 lane"
            ),
            "validation_status": "shadow_only_retuned_handoff_2026_04_15",
            "deploy_priority": 1,
            "risk_notes": (
                "Shadow-only handoff for the published ETH step retune candidate. Keep the disproved step14 "
                "control untouched, launch this as a new lane only if the room chooses retune, and do not mix "
                "step retune with offensive-closure changes in the same experiment."
            ),
            "guardrails": {
                "kill_on_reset_storm": bool(guardrails.get("kill_on_reset_storm", True)),
                "max_resets_per_hour": float(guardrails.get("max_resets_per_hour", 6)),
                "floating_loss_limit_usd": float(guardrails.get("floating_loss_limit_usd", -15.0)),
                "session_gate": guardrails.get("session_gate"),
                "escape_hatch_enabled": bool(guardrails.get("escape_hatch_enabled", False)),
            },
        },
    }


def build_payload(
    control_config: dict[str, Any],
    control_state: dict[str, Any],
    control_gate: dict[str, Any],
    next_action_board: dict[str, Any],
    coefficient_analysis: dict[str, Any],
) -> dict[str, Any]:
    decision_row = action_row(
        next_action_board,
        "decide_eth_step14_negative_proof_response_kill_or_launch_retuned_shadow",
    )
    step_usd = float(coefficient_analysis.get("recommended_step_usd") or 0.0)
    if step_usd <= 0:
        raise ValueError("recommended_step_usd missing from coefficient analysis")

    control_symbol = dict(((control_state.get("symbols") or {}).get("ETHUSD")) or {})
    control_gate_summary = dict(control_gate.get("summary") or {})
    retuned_config = build_retuned_config(control_config, step_usd)
    control_truth = dict(decision_row.get("machine_truth") or {})

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(CONTROL_CONFIG_PATH.relative_to(ROOT)),
            str(CONTROL_STATE_PATH.relative_to(ROOT)),
            str(CONTROL_GATE_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(COEFFICIENT_ANALYSIS_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The room already decided this is a kill-vs-retune fork; the missing piece was a concrete retuned ETH shadow handoff surface that does not mutate the disproved step14 control in place.",
            f"This handoff encodes the published Option A candidate as a new disabled ETH M5 symmetric shadow lane at step ${step_usd:.2f}, preserving the step14 control posture everywhere else.",
            "The config is intentionally disabled and shadow-only: a runtime owner still has to choose the retune branch explicitly, register/launch it cleanly, and judge it on a fresh 25+ close proof contract before any baseline or A/B claims.",
        ],
        "decision_context": {
            "queue_top_action": str(decision_row.get("action") or ""),
            "control_verdict": str(control_gate_summary.get("verdict") or ""),
            "control_realized_closes": int(control_symbol.get("realized_closes") or 0),
            "control_realized_net_usd": round(float(control_symbol.get("realized_net_usd") or 0.0), 2),
            "control_avg_per_close": round(float(control_gate_summary.get("avg_per_close") or 0.0), 4),
        },
        "retune_candidate": {
            "recommended_option": str(coefficient_analysis.get("recommended_option") or ""),
            "recommended_step_usd": step_usd,
            "alternate_step_usd": float(coefficient_analysis.get("alternate_step_usd") or 0.0),
            "minimum_proof_closes": int(coefficient_analysis.get("minimum_proof_closes") or 0),
            "kill_option_available": bool(coefficient_analysis.get("kill_option_available")),
        },
        "launch_discipline": {
            "must_keep_untouched": [
                "the disproved step14 control lane and its state/event lineage",
                "no-dynamic-geometry posture",
                "alpha=1.0 close behavior",
                "offensive-closure OFF baseline for the retuned proof lane",
            ],
            "must_not_do": [
                "do not retune the existing step14 control in place",
                "do not enable offensive closure on the retuned lane during proof accumulation",
                "do not cite the retuned lane as baseline truth before 25+ fresh closes",
                "do not use archival step5 shelf evidence as a substitute for fresh retuned proof",
            ],
            "safe_next_move": (
                "If the room chooses retune, register or launch this disabled shadow config as a new ETH M5 lane, "
                "then judge it on a fresh 25+ close contract before any OFF vs ON offensive-closure comparison."
            ),
        },
        "retuned_config": retuned_config,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    decision = dict(payload.get("decision_context") or {})
    candidate = dict(payload.get("retune_candidate") or {})
    config = dict(payload.get("retuned_config") or {})
    meta = dict(config.get("hungry_hippo_metadata") or {})
    discipline = dict(payload.get("launch_discipline") or {})
    lines = [
        "# ETH M5 Retuned Shadow Handoff",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: turn the ETH kill-vs-retune decision into one concrete shadow-only retune handoff without mutating the disproved step14 control.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Decision Context",
            "",
            f"- Queue top action: `{decision.get('queue_top_action', '-')}`",
            f"- Control verdict: `{decision.get('control_verdict', '-')}`",
            f"- Current step14 closes: `{decision.get('control_realized_closes', 0)}`",
            f"- Current step14 realized net USD: `{decision.get('control_realized_net_usd', 0.0):+.2f}`",
            f"- Current step14 avg/close: `{decision.get('control_avg_per_close', 0.0):+.4f}`",
            "",
            "## Retune Candidate",
            "",
            f"- Recommended option: `{candidate.get('recommended_option', '-')}`",
            f"- Recommended step USD: `{candidate.get('recommended_step_usd', 0.0):.2f}`",
            f"- Alternate step USD: `{candidate.get('alternate_step_usd', 0.0):.2f}`",
            f"- Minimum proof closes: `{candidate.get('minimum_proof_closes', 0)}`",
            f"- Kill option available: `{str(bool(candidate.get('kill_option_available'))).lower()}`",
            "",
            "## Config Summary",
            "",
            f"- Config path: `{OUTPUT_CONFIG_PATH.relative_to(ROOT)}`",
            f"- Lane name: `{config.get('name', '-')}`",
            f"- Enabled by default: `{str(bool(config.get('enabled'))).lower()}`",
            f"- State path: `{config.get('state_path', '-')}`",
            f"- Event path: `{config.get('event_path', '-')}`",
            f"- Watchdog group: `{config.get('watchdog_group', '-')}`",
            f"- Risk notes: `{meta.get('risk_notes', '-')}`",
            "",
            "## Launch Discipline",
            "",
        ]
    )
    for item in list(discipline.get("must_keep_untouched") or []):
        lines.append(f"- Preserve: {item}")
    for item in list(discipline.get("must_not_do") or []):
        lines.append(f"- Do not: {item}")
    lines.append(f"- Safe next move: `{discipline.get('safe_next_move', '-')}`")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_CONFIG_PATH.write_text(json.dumps(payload.get("retuned_config") or {}, indent=2) + "\n", encoding="utf-8")
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> None:
    payload = build_payload(
        load_json(CONTROL_CONFIG_PATH),
        load_json(CONTROL_STATE_PATH),
        load_json(CONTROL_GATE_PATH),
        load_json(NEXT_ACTION_PATH),
        parse_eth_step14_coefficient_analysis(load_text(COEFFICIENT_ANALYSIS_PATH)),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_CONFIG_PATH}")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")


if __name__ == "__main__":
    main()
