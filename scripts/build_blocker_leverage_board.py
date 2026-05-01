#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TASK_STORE_PATH = ROOT / "war_room_tasks.json"

ETH_BOARD_PATH = REPORTS / "eth_atr_runtime_status_board.json"
SHAPESHIFTER_BOARD_PATH = REPORTS / "structure_shapeshifter_proof_board.json"
EXPERIMENTAL_BOARD_PATH = REPORTS / "experimental_proof_watch_board.json"
LATTICE_GAP_BOARD_PATH = REPORTS / "lattice_telemetry_gap_board.json"
LATTICE_PHASE1_COVERAGE_PATH = REPORTS / "lattice_phase1_event_coverage_board.json"
FX_PHASE1_VISIBILITY_PATH = REPORTS / "fx_phase1_telemetry_visibility_board.json"
FX_SHADOW_RECYCLE_PATH = REPORTS / "fx_shadow_telemetry_recycle_board.json"
FX_SHADOW_CONTRACT_DEBT_PATH = REPORTS / "fx_shadow_telemetry_contract_debt_board.json"

ETH_BOARD_BUILDER = ROOT / "scripts" / "build_eth_atr_runtime_status_board.py"
SHAPESHIFTER_BOARD_BUILDER = ROOT / "scripts" / "build_structure_shapeshifter_proof_board.py"
EXPERIMENTAL_BOARD_BUILDER = ROOT / "scripts" / "build_experimental_proof_watch_board.py"
LATTICE_PHASE1_COVERAGE_BUILDER = ROOT / "scripts" / "build_lattice_phase1_event_coverage_board.py"
FX_PHASE1_VISIBILITY_BUILDER = ROOT / "scripts" / "build_fx_phase1_telemetry_visibility_board.py"
FX_SHADOW_RECYCLE_BUILDER = ROOT / "scripts" / "build_fx_shadow_telemetry_recycle_board.py"
FX_SHADOW_CONTRACT_DEBT_BUILDER = ROOT / "scripts" / "build_fx_shadow_telemetry_contract_debt_board.py"

OUTPUT_JSON_PATH = REPORTS / "blocker_leverage_board.json"
OUTPUT_MD_PATH = REPORTS / "blocker_leverage_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_builder(script_path: Path) -> None:
    subprocess.run([sys.executable, str(script_path)], check=True, cwd=ROOT)


def refresh_inputs() -> None:
    run_builder(ETH_BOARD_BUILDER)
    run_builder(SHAPESHIFTER_BOARD_BUILDER)
    run_builder(LATTICE_PHASE1_COVERAGE_BUILDER)
    run_builder(FX_PHASE1_VISIBILITY_BUILDER)
    run_builder(FX_SHADOW_RECYCLE_BUILDER)
    run_builder(FX_SHADOW_CONTRACT_DEBT_BUILDER)
    run_builder(EXPERIMENTAL_BOARD_BUILDER)


def task_by_id(task_store: dict[str, Any], task_id: int) -> dict[str, Any]:
    for task in list(task_store.get("tasks") or []):
        if int(task.get("id") or 0) == task_id:
            return dict(task)
    raise KeyError(f"task not found: {task_id}")


def build_payload(
    experimental_board: dict[str, Any],
    eth_board: dict[str, Any],
    shapeshifter_board: dict[str, Any],
    lattice_gap_board: dict[str, Any],
    lattice_phase1_coverage_board: dict[str, Any],
    task_store: dict[str, Any],
    fx_shadow_recycle_board: dict[str, Any] | None = None,
    fx_shadow_contract_debt_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eth_task = task_by_id(task_store, 13)
    shapeshifter_task = task_by_id(task_store, 23)
    hedge_task = task_by_id(task_store, 24)

    eth_summary = dict(experimental_board.get("eth_atr") or {})
    shapeshifter_runner = dict(shapeshifter_board.get("runner") or {})
    shapeshifter_events = dict(shapeshifter_board.get("events") or {})
    hedge_evidence = dict(hedge_task.get("evidence") or {})
    coverage_summary = dict(lattice_phase1_coverage_board.get("summary") or {})
    deployment_context = dict(lattice_phase1_coverage_board.get("deployment_context") or {})
    fx_shadow_recycle_board = fx_shadow_recycle_board or {}
    fx_shadow_summary = dict(fx_shadow_recycle_board.get("summary") or {})
    fx_shadow_contract_debt_board = fx_shadow_contract_debt_board or {}
    fx_shadow_contract_summary = dict(fx_shadow_contract_debt_board.get("summary") or {})
    telemetry_surface_present = str(lattice_gap_board.get("readiness") or "") == "telemetry_surface_present"
    coverage_readiness = str(lattice_phase1_coverage_board.get("readiness") or "")
    fx_shadow_recycle_readiness = str(fx_shadow_recycle_board.get("readiness") or "")
    fx_shadow_contract_debt_readiness = str(fx_shadow_contract_debt_board.get("readiness") or "")
    fx_shadow_top_candidate = str(fx_shadow_summary.get("top_recycle_candidate") or "")
    fx_shadow_first_wave_count = int(fx_shadow_summary.get("recycle_first_wave_count") or 0)
    fx_shadow_unlockable_first_wave_count = int(fx_shadow_contract_summary.get("unlockable_first_wave_count") or 0)
    fx_shadow_projected_safe_first_wave_count = int(fx_shadow_contract_summary.get("projected_safe_first_wave_count") or 0)
    fx_shadow_top_unlock_candidate = str(fx_shadow_contract_summary.get("top_unlock_candidate") or "")
    fx_shadow_acceleration_available = bool(
        fx_shadow_recycle_readiness in {"shadow_recycle_queue_ready", "shadow_recycle_second_wave_only"}
        and int(fx_shadow_summary.get("recycle_candidate_count") or 0) > 0
    )
    fx_shadow_contract_debt_actionable = bool(
        fx_shadow_contract_debt_readiness == "contract_debt_actionable"
        and fx_shadow_unlockable_first_wave_count > 0
    )
    coverage_field_count = int(coverage_summary.get("field_count") or 0)
    coverage_covered_field_count = int(coverage_summary.get("covered_field_count") or 0)
    coverage_zero_ratio_text = f"{coverage_covered_field_count}/{coverage_field_count}" if coverage_field_count > 0 else "0/0"
    stale_pre_enrichment_log = coverage_readiness == "stale_or_pre_enrichment_log"
    event_log_is_newer_than_reference_code = deployment_context.get("event_log_is_newer_than_reference_code")
    post_restart_waiting_window = str(experimental_board.get("overall_status") or "") == "waiting_post_restart_event"
    deployment_lag_text = (
        "the reviewed event log predates the telemetry-bearing core code"
        if event_log_is_newer_than_reference_code is False
        else "the reviewed runtime log is still pre-enrichment"
    )

    rows = [
        {
            "priority": 1,
            "blocker": "First ETH ATR market event",
            "leverage_tier": "L2",
            "why_it_is_first": "It is the clearest next proof gate on a healthy launched pack, and it decides whether task 13 remains passive waiting or becomes real evidence review.",
            "current_blocker": [
                f"ETH ATR healthy lanes are `{int(eth_summary.get('healthy_lane_count') or 0)}/{int(eth_summary.get('lane_count') or 0)}`",
                f"ETH ATR total realized closes are `{int(eth_summary.get('total_realized_closes') or 0)}`",
                f"ETH ATR total open positions are `{int(eth_summary.get('total_open_positions') or 0)}`",
            ],
            "authoritative_reports": [
                "reports/experimental_proof_watch_board.md",
                "reports/eth_atr_runtime_status_board.md",
                "reports/team_leverage_execution_docket.md",
            ],
            "unlocks": [
                "task 13 can move from passive accumulation to proof judgment",
                "the passive-proof board can stop reading as pure waiting once ETH contributes evidence",
            ],
            "current_queue_effect_if_unresolved": [
                "the top ETH experiment stays in passive-monitor posture",
                "any new ETH runtime surgery would be premature",
            ],
            "honest_next_move": [
                "keep the launched ETH ATR pack healthy",
                "wait for the first open/close event rather than retuning pre-proof",
            ],
            "machine_truth": {
                "task_id": 13,
                "task_status": eth_task.get("status"),
                "overall_status": experimental_board.get("overall_status"),
                "healthy_lane_count": eth_summary.get("healthy_lane_count"),
                "total_realized_closes": eth_summary.get("total_realized_closes"),
                "total_open_positions": eth_summary.get("total_open_positions"),
            },
        },
        {
            "priority": 2,
            "blocker": "First post-repair shapeshifter proof event",
            "leverage_tier": "L2",
            "why_it_is_second": "The wiring work is complete, so the room now needs a current-runner proof event before spending more effort on adaptive-geometry stories.",
            "current_blocker": [
                f"proof_status is `{shapeshifter_board.get('proof_status')}`",
                f"structure flips since runner start are `{int(shapeshifter_events.get('structure_flip_count_since_runner_start') or 0)}`",
                f"box geometry adjusts since runner start are `{int(shapeshifter_events.get('box_geometry_adjust_count_since_runner_start') or 0)}`",
            ],
            "authoritative_reports": [
                "reports/experimental_proof_watch_board.md",
                "reports/structure_shapeshifter_proof_board.md",
                "reports/team_leverage_execution_docket.md",
            ],
            "unlocks": [
                "task 23 can move from repair-complete to evidence-backed evaluation",
                "the room can decide whether shapeshifter deserves more budget than passive monitoring",
            ],
            "current_queue_effect_if_unresolved": [
                "shapeshifter stays shadow-only and proof-incomplete",
                "new bridge or scheduler edits would just be guesswork",
            ],
            "honest_next_move": [
                "leave the repaired runner alive",
                "watch for the first post-start structure_flip or repeated fresh box mutation",
            ],
            "machine_truth": {
                "task_id": 23,
                "task_status": shapeshifter_task.get("status"),
                "proof_status": shapeshifter_board.get("proof_status"),
                "runner_fresh": shapeshifter_runner.get("fresh"),
                "structure_flip_count_since_runner_start": shapeshifter_events.get("structure_flip_count_since_runner_start"),
                "box_geometry_adjust_count_since_runner_start": shapeshifter_events.get("box_geometry_adjust_count_since_runner_start"),
            },
        },
        {
            "priority": 3,
            "blocker": (
                f"Task 28 runtime event coverage still {coverage_zero_ratio_text} on a pre-enrichment log"
                if stale_pre_enrichment_log
                else f"Task 28 runtime event coverage still {coverage_zero_ratio_text}"
            ),
            "leverage_tier": "L1",
            "why_it_is_third": (
                (
                    (
                        (
                            f"Task 28 code work is already done, and until a fresh post-enrichment log moves the coverage board off {coverage_zero_ratio_text} the room still cannot claim the telemetry patch is diagnostically legible from runtime evidence. A shadow-only acceleration queue now exists, but it is optional rather than the default next move, and {fx_shadow_unlockable_first_wave_count} more first-wave candidates are currently suppressed by restart-contract debt."
                            if fx_shadow_contract_debt_actionable
                            else f"Task 28 code work is already done, and until a fresh post-enrichment log moves the coverage board off {coverage_zero_ratio_text} the room still cannot claim the telemetry patch is diagnostically legible from runtime evidence. A shadow-only acceleration queue now exists, but it is optional rather than the default next move."
                        )
                        if fx_shadow_acceleration_available
                        else f"Task 28 code work is already done, and until a fresh post-enrichment log moves the coverage board off {coverage_zero_ratio_text} the room still cannot claim the telemetry patch is diagnostically legible from runtime evidence."
                    )
                    if post_restart_waiting_window
                    else f"Task 28 is already the first active engineering lane, and until a fresh post-enrichment log moves the coverage board off {coverage_zero_ratio_text} the room still cannot claim the telemetry patch is diagnostically legible just because the scope is defined."
                )
                if stale_pre_enrichment_log
                else f"Task 28 is already the first active engineering lane, and until the coverage board leaves {coverage_zero_ratio_text} the room still cannot claim the telemetry patch is diagnostically legible just because the scope is defined."
            ),
            "current_blocker": [
                (
                    f"coverage readiness is `{coverage_readiness}` and {deployment_lag_text}"
                    if stale_pre_enrichment_log
                    else f"coverage readiness is `{coverage_readiness}`"
                ),
                f"covered phase1 fields are `{coverage_zero_ratio_text}`",
                f"zero-coverage field count is `{int(coverage_summary.get('zero_coverage_field_count') or 0)}`",
            ],
            "authoritative_reports": [
                "reports/lattice_phase1_event_coverage_board.md",
                "reports/lattice_telemetry_gap_board.md",
                "reports/fx_shadow_telemetry_recycle_board.md",
                "reports/fx_shadow_telemetry_contract_debt_board.md",
                "reports/team_leverage_execution_docket.md",
            ],
            "unlocks": [
                "task 28 can move from bounded implementation to diagnostically legible implementation",
                "decision 7 can be read from live field visibility instead of scope-only truth",
            ],
            "current_queue_effect_if_unresolved": [
                (
                    (
                        (
                            "the telemetry lane stays landed but blocked on the first fresh post-patch event window"
                            if post_restart_waiting_window
                            else "the telemetry lane stays code-present but blocked on fresh post-enrichment runtime evidence"
                        )
                        if stale_pre_enrichment_log
                        else "the telemetry lane stays code-present but not yet reviewable from runtime events"
                    )
                    if telemetry_surface_present
                    else "the telemetry lane stays start-now but not yet reviewable from runtime events"
                ),
                "fresh adaptive events can still be over-read unless the coverage board is consulted explicitly",
            ],
            "honest_next_move": [
                (
                    (
                        (
                            "keep the current post-patch runners alive until a fresh enriched open or close-like event lands in the log"
                            if post_restart_waiting_window
                            else "run or relaunch the patched lattice path until fresh enriched open and close-like events exist in a post-patch log"
                        )
                        if stale_pre_enrichment_log
                        else "run or relaunch the patched lattice path until fresh enriched open and close-like events exist in the log"
                    )
                    if telemetry_surface_present
                    else "land the bounded phase1 event enrichment on open and close-like events"
                ),
                (
                    (
                        (
                            f"if faster evidence is worth the continuity cost, recycle only a current safe first-wave shadow FX candidate such as `{fx_shadow_top_candidate}`; read the contract-debt board before assuming the safe queue is exhaustive because `{fx_shadow_top_unlock_candidate}` is suppressed by `--fresh-start`; otherwise keep waiting on the current runners"
                            if post_restart_waiting_window and fx_shadow_acceleration_available and fx_shadow_top_candidate and fx_shadow_contract_debt_actionable and fx_shadow_top_unlock_candidate
                            else f"if faster evidence is worth the continuity cost, recycle only a first-wave shadow FX candidate such as `{fx_shadow_top_candidate}`; otherwise keep waiting on the current runners"
                        )
                        if post_restart_waiting_window and fx_shadow_acceleration_available and fx_shadow_top_candidate
                        else "rebuild the coverage board after the first fresh event and wait for non-zero field coverage before calling the patch legible"
                    )
                    if post_restart_waiting_window
                    else "rebuild the coverage board and wait for non-zero field coverage before calling the patch legible"
                ),
            ],
            "machine_truth": {
                "task_id": 28,
                "experimental_board_status": experimental_board.get("overall_status"),
                "coverage_readiness": lattice_phase1_coverage_board.get("readiness"),
                "coverage_field_count": coverage_field_count,
                "coverage_covered_field_count": coverage_covered_field_count,
                "coverage_zero_coverage_field_count": coverage_summary.get("zero_coverage_field_count"),
                "coverage_event_log_is_newer_than_reference_code": event_log_is_newer_than_reference_code,
                "coverage_event_log_mtime": deployment_context.get("event_log_mtime"),
                "coverage_reference_code_mtime": deployment_context.get("reference_code_mtime"),
                "fx_shadow_recycle_readiness": fx_shadow_recycle_readiness,
                "fx_shadow_recycle_first_wave_count": fx_shadow_first_wave_count,
                "fx_shadow_top_recycle_candidate": fx_shadow_top_candidate,
                "fx_shadow_contract_debt_readiness": fx_shadow_contract_debt_readiness,
                "fx_shadow_unlockable_first_wave_count": fx_shadow_unlockable_first_wave_count,
                "fx_shadow_projected_safe_first_wave_count": fx_shadow_projected_safe_first_wave_count,
                "fx_shadow_top_unlock_candidate": fx_shadow_top_unlock_candidate,
            },
        },
        {
            "priority": 4,
            "blocker": "Inverse-correlated FX lane for cross-symbol hedging",
            "leverage_tier": "L1",
            "why_it_is_third": "It blocks only one medium-term architecture track, so it belongs behind the live passive-proof blockers above and behind the already-active telemetry lane.",
            "current_blocker": [
                str(hedge_evidence.get("fx_verdict") or "FX hedge verdict unavailable"),
                str(hedge_evidence.get("current_fx_lanes") or "current FX lane set unavailable"),
                str(hedge_evidence.get("blocking_dependency") or "missing inverse FX leg"),
            ],
            "authoritative_reports": [
                "docs/cross-symbol-hedge-orchestration.md",
                "reports/team_leverage_execution_docket.md",
            ],
            "unlocks": [
                "task 24 can move from design to implementation",
            ],
            "current_queue_effect_if_unresolved": [
                "cross-symbol hedging stays medium-term design work",
                "no current implementation time should be spent there",
            ],
            "honest_next_move": [
                "launch or identify an inverse-correlated FX lane first",
                "keep the prototype parked until that dependency exists",
            ],
            "machine_truth": {
                "task_id": 24,
                "task_status": hedge_task.get("status"),
                "implementation_status": hedge_evidence.get("implementation_status"),
                "priority_downgraded": hedge_evidence.get("priority_downgraded"),
                "blocking_dependency": hedge_evidence.get("blocking_dependency"),
            },
        },
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(EXPERIMENTAL_BOARD_PATH.relative_to(ROOT)),
            str(ETH_BOARD_PATH.relative_to(ROOT)),
            str(SHAPESHIFTER_BOARD_PATH.relative_to(ROOT)),
            str(LATTICE_GAP_BOARD_PATH.relative_to(ROOT)),
            str(LATTICE_PHASE1_COVERAGE_PATH.relative_to(ROOT)),
            str(FX_SHADOW_RECYCLE_PATH.relative_to(ROOT)),
            str(FX_SHADOW_CONTRACT_DEBT_PATH.relative_to(ROOT)),
            str(TASK_STORE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The main blockers are reality blockers, not missing code: the launched ETH proof pack still needs first market evidence.",
            (
                (
                    (
                        f"ETH ATR first proof and shapeshifter first fresh proof both outrank medium-term hedge architecture because they can change the queue immediately without new runtime surgery, and Task 28 is now waiting on the first fresh post-patch event window rather than another restart order even though runtime coverage is still {coverage_zero_ratio_text}. The room also now knows {fx_shadow_unlockable_first_wave_count} more first-wave FX acceleration rows are suppressed by restart-contract debt."
                        if fx_shadow_contract_debt_actionable
                        else f"ETH ATR first proof and shapeshifter first fresh proof both outrank medium-term hedge architecture because they can change the queue immediately without new runtime surgery, and Task 28 is now waiting on the first fresh post-patch event window rather than another restart order even though runtime coverage is still {coverage_zero_ratio_text}."
                    )
                    if post_restart_waiting_window
                    else f"ETH ATR first proof and shapeshifter first fresh proof both outrank medium-term hedge architecture because they can change the queue immediately without new runtime surgery, and the active Task 28 lane is now blocked on a pre-enrichment watched log rather than another missing telemetry field even though runtime coverage is still {coverage_zero_ratio_text}."
                )
                if stale_pre_enrichment_log
                else f"ETH ATR first proof and shapeshifter first fresh proof both outrank medium-term hedge architecture because they can change the queue immediately without new runtime surgery, and the active Task 28 lane still has {coverage_zero_ratio_text} runtime field coverage."
            ),
            "If those proof blockers stay unresolved, keep the team in disciplined monitoring mode instead of inventing a louder story.",
        ],
        "rows": rows,
        "dependency_summary": [
            {"blocker": row["blocker"], "unlock_count": len(list(row.get("unlocks") or [])), "current_leverage": row["leverage_tier"]}
            for row in rows
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Blocker Leverage Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: rank the current upstream blockers by how much honest queue movement they unlock once cleared.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Ranked Blockers", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {int(row['priority'])}. {row['blocker']}")
        lines.append("")
        lines.append(f"- Leverage tier: `{row['leverage_tier']}`")
        lines.append(f"- Why it is ranked here: `{row['why_it_is_first'] if row['priority'] == 1 else row['why_it_is_second'] if row['priority'] == 2 else row['why_it_is_third']}`")
        lines.append(f"- Current blocker: `{'; '.join(list(row.get('current_blocker') or []))}`")
        lines.append(f"- Authoritative reports: `{'; '.join(list(row.get('authoritative_reports') or []))}`")
        lines.append(f"- Unlocks: `{'; '.join(list(row.get('unlocks') or []))}`")
        lines.append(f"- Current queue effect if unresolved: `{'; '.join(list(row.get('current_queue_effect_if_unresolved') or []))}`")
        lines.append(f"- Honest next move: `{'; '.join(list(row.get('honest_next_move') or []))}`")
        machine_truth = dict(row.get("machine_truth") or {})
        lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in machine_truth.items())}`")
        lines.append("")

    lines.extend(["## Dependency Summary", ""])
    lines.append("| Blocker | Unlock count | Current leverage |")
    lines.append("|---|---:|---|")
    for row in list(payload.get("dependency_summary") or []):
        lines.append(f"| {row['blocker']} | {int(row['unlock_count'])} | `{row['current_leverage']}` |")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the blocker leverage board.")
    parser.add_argument(
        "--skip-refresh-inputs",
        action="store_true",
        help="Assume upstream proof boards are already current and skip rebuilding them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_refresh_inputs:
        refresh_inputs()
    payload = build_payload(
        load_json(EXPERIMENTAL_BOARD_PATH),
        load_json(ETH_BOARD_PATH),
        load_json(SHAPESHIFTER_BOARD_PATH),
        load_json(LATTICE_GAP_BOARD_PATH),
        load_json(LATTICE_PHASE1_COVERAGE_PATH),
        load_json(TASK_STORE_PATH),
        load_json(FX_SHADOW_RECYCLE_PATH),
        load_json(FX_SHADOW_CONTRACT_DEBT_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
