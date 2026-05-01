#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
REPORTS = ROOT / "reports"

DOCTRINE_PATH = DOCS / "adaptive_foundational_gap_program.md"
PERFECTION_PATH = REPORTS / "adaptive_lattice_perfection_scorecard_board.json"
INCUMBENT_STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
SEAT_BOARD_PATH = REPORTS / "per_symbol_live_seat_board.json"
ACCEPTANCE_PATH = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
SHARED_SCORE_PATH = REPORTS / "adaptive_shared_score_board.json"

OUTPUT_JSON_PATH = REPORTS / "adaptive_foundational_gap_status_board.json"
OUTPUT_MD_PATH = REPORTS / "adaptive_foundational_gap_status_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def category_map(perfection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("category_id") or ""): dict(row)
        for row in list(perfection.get("categories") or [])
        if isinstance(row, dict)
    }


def seat_rows_by_gate(seat_board: dict[str, Any], gate_status: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in list(seat_board.get("rows") or [])
        if str(row.get("seat_execution_gate_status") or "") == gate_status
    ]


def gap_row(
    *,
    gap_id: str,
    title: str,
    dependency_rank: int,
    urgency: str,
    current_verdict: str,
    bridge_status: str,
    current_read: str,
    next_action: str,
    supporting_evidence: list[str],
    closure_requirements: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    return {
        "gap_id": gap_id,
        "title": title,
        "dependency_rank": dependency_rank,
        "urgency": urgency,
        "current_verdict": current_verdict,
        "closure_status": "open",
        "bridge_status": bridge_status,
        "current_read": current_read,
        "next_action": next_action,
        "supporting_evidence": supporting_evidence,
        "closure_requirements": closure_requirements,
        "source_surfaces": source_surfaces,
    }


def build_payload(
    perfection: dict[str, Any] | None = None,
    incumbent_study: dict[str, Any] | None = None,
    seat_board: dict[str, Any] | None = None,
    acceptance: dict[str, Any] | None = None,
    shared_score: dict[str, Any] | None = None,
) -> dict[str, Any]:
    perfection_payload = perfection or load_json(PERFECTION_PATH)
    study_payload = incumbent_study or load_json(INCUMBENT_STUDY_PATH)
    seat_payload = seat_board or load_json(SEAT_BOARD_PATH)
    acceptance_payload = acceptance or load_json(ACCEPTANCE_PATH)
    shared_payload = shared_score or load_json(SHARED_SCORE_PATH)

    perfection_summary = dict(perfection_payload.get("summary") or {})
    study_summary = dict(study_payload.get("summary") or {})
    seat_summary = dict(seat_payload.get("summary") or {})
    acceptance_summary = dict(acceptance_payload.get("summary") or {})
    shared_summary = dict(shared_payload.get("summary") or {})
    categories = category_map(perfection_payload)

    state_category = categories.get("state_reading_honesty", {})
    telemetry_category = categories.get("telemetry_explainability", {})
    forward_category = categories.get("forward_proof_status", {})

    study_ready_symbols = unique_strings(list(study_summary.get("study_ready_symbols") or []))
    comparable_symbols = unique_strings(list(study_summary.get("comparable_symbols") or []))
    scored_symbols = unique_strings(list(shared_summary.get("scored_symbols") or []))
    shared_ready_symbols = unique_strings(list(shared_summary.get("shared_score_ready_symbols") or []))
    adaptive_leading_symbols = unique_strings(list(shared_summary.get("adaptive_leading_symbols") or []))
    incumbent_leading_symbols = unique_strings(list(shared_summary.get("incumbent_leading_symbols") or []))
    missing_adaptive_score_symbols = unique_strings(list(shared_summary.get("missing_adaptive_score_symbols") or []))
    execution_ready_symbols = unique_strings(
        [row.get("symbol") for row in seat_rows_by_gate(seat_payload, "ready_for_seat_execution")]
    )
    preparatory_symbols = unique_strings(
        [row.get("symbol") for row in seat_rows_by_gate(seat_payload, "queue_backed_preparatory_only")]
    )
    family_coverage = dict(study_summary.get("family_coverage") or {})
    verdict_counts = dict(acceptance_summary.get("verdict_counts") or {})

    rows = [
        gap_row(
            gap_id="state_space_model",
            title="Validated State-Space Model",
            dependency_rank=1,
            urgency="high",
            current_verdict="missing_but_instrumentable",
            bridge_status=(
                "telemetry_ready_formal_state_model_missing"
                if str(state_category.get("verdict") or "") == "strong"
                and str(telemetry_category.get("verdict") or "") == "strong"
                else "instrumentation_gap_still_present"
            ),
            current_read=(
                "Telemetry and state-reading inputs are now strong enough to stop blaming missing instrumentation, "
                "but the repo still has no pinned state vocabulary, no feature-to-state mapping, and no predictive falsification layer."
            ),
            next_action=(
                "Define the pinned state taxonomy and the runtime feature map explicitly, then add falsification checks "
                "that prove those labels predict different control behavior."
            ),
            supporting_evidence=[
                f"perfection_state_reading_honesty={state_category.get('verdict', 'missing')}",
                f"perfection_telemetry_explainability={telemetry_category.get('verdict', 'missing')}",
                "highest_runtime_obligation="
                + str(perfection_summary.get("highest_priority_runtime_obligation_class") or "missing"),
                "overlay_governed_candidate_count="
                + str(parse_int(acceptance_summary.get("overlay_governed_candidate_count"))),
            ],
            closure_requirements=[
                "Pin the state vocabulary used by the controller.",
                "Map runtime telemetry features into those states explicitly.",
                "Add falsification tests that show the labels are predictive rather than decorative.",
                "Show that state-conditioned control beats non-stateful baselines.",
            ],
            source_surfaces=[
                relative_path_text(PERFECTION_PATH),
                relative_path_text(ACCEPTANCE_PATH),
            ],
        ),
        gap_row(
            gap_id="objective_function",
            title="Unified Objective Function",
            dependency_rank=2,
            urgency="highest",
            current_verdict="missing_and_doctrinally_urgent",
            bridge_status=(
                "proxy_present_shared_score_partial" if scored_symbols else "objective_proxy_not_yet_comparable"
            ),
            current_read=(
                "The repo now has an incumbent-side max-profit proxy plus a partial shared-score bridge, "
                "but it still lacks one explicit controller objective that scores incumbent and challenger rows on the same rule."
            ),
            next_action=(
                "Promote the current proxy/score fragments into one written controller objective with explicit survival "
                "constraints and require every incumbent-versus-challenger comparison to use that score."
            ),
            supporting_evidence=[
                f"objective_comparison_ready_symbols={unique_strings(list(seat_summary.get('objective_comparison_ready_symbols') or []))}",
                f"challenger_comparable_symbols={unique_strings(list(seat_summary.get('challenger_comparable_symbols') or []))}",
                f"shared_score_ready_symbols={shared_ready_symbols}",
                f"missing_adaptive_score_symbols={missing_adaptive_score_symbols}",
            ],
            closure_requirements=[
                "Write one controller objective function explicitly.",
                "Define one shared survival and concentration constraint set.",
                "Use one derived evaluation score across challenger comparisons.",
                "Show that optimizing the score improves realized outcomes and survivability.",
            ],
            source_surfaces=[
                relative_path_text(SEAT_BOARD_PATH),
                relative_path_text(SHARED_SCORE_PATH),
                relative_path_text(INCUMBENT_STUDY_PATH),
            ],
        ),
        gap_row(
            gap_id="cross_family_control_law",
            title="Cross-Family Control Law",
            dependency_rank=3,
            urgency="high",
            current_verdict="aspirational_only",
            bridge_status="family_coverage_split_not_generalized",
            current_read=(
                "Adaptive doctrine now spans multiple families, but the controller story is still split: "
                "crypto has a ready candidate, FX remains blocked, index coverage is prior-only, and commodity coverage is missing."
            ),
            next_action=(
                "Define the family-general control mapping from shared states to geometry, close, rearm, and risk choices, "
                "with explicit family defaults instead of separate family folklore."
            ),
            supporting_evidence=[
                f"family_coverage={family_coverage}",
                "shadow_ready_candidates=" + str(parse_int(verdict_counts.get("shadow_ready"))),
                "research_only_candidates=" + str(parse_int(verdict_counts.get("research_only"))),
                "promotion_ready_candidates=" + str(parse_int(verdict_counts.get("promotion_ready"))),
            ],
            closure_requirements=[
                "Share one state taxonomy across families.",
                "Map those states to one controller law with explicit family defaults.",
                "Forward-proof at least one challenger per major family on that doctrine.",
                "Stop inventing a new controller story for each family.",
            ],
            source_surfaces=[
                relative_path_text(INCUMBENT_STUDY_PATH),
                relative_path_text(ACCEPTANCE_PATH),
            ],
        ),
        gap_row(
            gap_id="forward_superiority",
            title="Forward Superiority Over Incumbents",
            dependency_rank=4,
            urgency="highest",
            current_verdict="missing_and_blocking_authority",
            bridge_status="comparison_scaffold_exists_but_repeated_wins_missing",
            current_read=(
                "The repo can now name incumbents, challengers, and execution-ready seat seams, "
                "but it still cannot show repeated adaptive wins on the shared score. That keeps adaptive authority blocked."
            ),
            next_action=(
                "Run repeated incumbent-versus-challenger forward comparisons on the shared objective, "
                "and do not call adaptive authority closed until adaptive rows win with clean runtime hygiene."
            ),
            supporting_evidence=[
                f"study_ready_symbols={study_ready_symbols}",
                f"execution_ready_symbols={execution_ready_symbols}",
                f"adaptive_leading_symbols={adaptive_leading_symbols}",
                f"incumbent_leading_symbols={incumbent_leading_symbols}",
                "promotion_ready_candidates=" + str(parse_int(verdict_counts.get("promotion_ready"))),
                f"forward_proof_status={forward_category.get('verdict', 'missing')}",
            ],
            closure_requirements=[
                "Expose explicit incumbent and challenger per symbol.",
                "Compare them on the same scoring rule in forward windows.",
                "Require repeated superiority, not one sample.",
                "Keep runtime hygiene good enough that the comparison is not invalidated.",
            ],
            source_surfaces=[
                relative_path_text(INCUMBENT_STUDY_PATH),
                relative_path_text(SEAT_BOARD_PATH),
                relative_path_text(ACCEPTANCE_PATH),
                relative_path_text(SHARED_SCORE_PATH),
            ],
        ),
    ]

    rows_by_id = {row["gap_id"]: row for row in rows}
    overall_verdict = "formalization_program_active_not_closed"

    leadership_read = [
        (
            "Execution-ready seat seams now exist for "
            f"`{execution_ready_symbols}`, but that is not the same thing as closing the foundational gaps."
        ),
        (
            "The strongest bridge is state instrumentation: perfection currently reads "
            f"`{perfection_summary.get('total_score', 0)}/{perfection_summary.get('max_score', 0)}` with "
            f"`{state_category.get('verdict', 'missing')}` state reading and "
            f"`{telemetry_category.get('verdict', 'missing')}` telemetry explainability."
        ),
        (
            "Objective formalization is still partial: comparable symbols are "
            f"`{comparable_symbols}`, but only `{scored_symbols}` currently expose scoreable adaptive evidence."
        ),
        (
            "Cross-family control is still split by family coverage: "
            f"`{family_coverage}`."
        ),
        (
            "Forward superiority remains the authority blocker: study-ready symbols are "
            f"`{study_ready_symbols}`, adaptive-leading symbols are `{adaptive_leading_symbols}`, "
            f"and incumbent-leading symbols are `{incumbent_leading_symbols}`."
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            relative_path_text(DOCTRINE_PATH),
            relative_path_text(PERFECTION_PATH),
            relative_path_text(INCUMBENT_STUDY_PATH),
            relative_path_text(SEAT_BOARD_PATH),
            relative_path_text(ACCEPTANCE_PATH),
            relative_path_text(SHARED_SCORE_PATH),
        ],
        "summary": {
            "gap_count": len(rows),
            "closed_gap_count": 0,
            "overall_verdict": overall_verdict,
            "dependency_order": [row["gap_id"] for row in rows],
            "next_formalization_gap": "state_space_model",
            "highest_urgency_gap": "objective_function",
            "authority_blocking_gap": "forward_superiority",
            "best_instrumented_gap": "state_space_model",
            "execution_ready_symbols": execution_ready_symbols,
            "preparatory_symbols": preparatory_symbols,
            "study_ready_symbols": study_ready_symbols,
            "shared_score_ready_symbols": shared_ready_symbols,
            "adaptive_leading_symbols": adaptive_leading_symbols,
            "promotion_ready_candidate_count": parse_int(verdict_counts.get("promotion_ready")),
        },
        "leadership_read": leadership_read,
        "gaps": rows,
        "notes": [
            "This board is passive doctrine/status synthesis. It does not promote any adaptive branch or override seat, queue, or runtime authority.",
            "Use it to keep execution work honest: execution-ready packets and queue contracts can move forward while the four foundational gaps remain explicitly open.",
            "The authority standard still comes from docs/adaptive_foundational_gap_program.md; this board is the generated current-state companion to that doctrine.",
        ],
        "current_task_translation": [
            {
                "gap_id": rows_by_id["state_space_model"]["gap_id"],
                "read": rows_by_id["state_space_model"]["next_action"],
            },
            {
                "gap_id": rows_by_id["objective_function"]["gap_id"],
                "read": rows_by_id["objective_function"]["next_action"],
            },
            {
                "gap_id": rows_by_id["cross_family_control_law"]["gap_id"],
                "read": rows_by_id["cross_family_control_law"]["next_action"],
            },
            {
                "gap_id": rows_by_id["forward_superiority"]["gap_id"],
                "read": rows_by_id["forward_superiority"]["next_action"],
            },
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Adaptive Foundational Gap Status Board",
        "",
        "Generated companion surface for the adaptive foundational-gap doctrine.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- overall_verdict: `{summary.get('overall_verdict', '')}`",
        f"- next_formalization_gap: `{summary.get('next_formalization_gap', '')}`",
        f"- highest_urgency_gap: `{summary.get('highest_urgency_gap', '')}`",
        f"- authority_blocking_gap: `{summary.get('authority_blocking_gap', '')}`",
        f"- execution_ready_symbols: `{summary.get('execution_ready_symbols', [])}`",
        f"- study_ready_symbols: `{summary.get('study_ready_symbols', [])}`",
        f"- shared_score_ready_symbols: `{summary.get('shared_score_ready_symbols', [])}`",
        "",
        "## Leadership Read",
        "",
    ]

    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Gaps", ""])
    for row in list(payload.get("gaps") or []):
        lines.extend(
            [
                f"### {row.get('title', '')}",
                "",
                f"- gap_id: `{row.get('gap_id', '')}`",
                f"- dependency_rank: `{row.get('dependency_rank', '')}`",
                f"- urgency: `{row.get('urgency', '')}`",
                f"- current_verdict: `{row.get('current_verdict', '')}`",
                f"- bridge_status: `{row.get('bridge_status', '')}`",
                f"- current_read: {row.get('current_read', '')}",
                f"- next_action: {row.get('next_action', '')}",
                f"- supporting_evidence: `{row.get('supporting_evidence', [])}`",
                f"- source_surfaces: `{row.get('source_surfaces', [])}`",
                "",
                "Closure requirements:",
            ]
        )
        for item in list(row.get("closure_requirements") or []):
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## Current Task Translation", ""])
    for row in list(payload.get("current_task_translation") or []):
        lines.append(f"- `{row.get('gap_id', '')}`: {row.get('read', '')}")

    lines.extend(["", "## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        perfection=load_json(PERFECTION_PATH),
        incumbent_study=load_json(INCUMBENT_STUDY_PATH),
        seat_board=load_json(SEAT_BOARD_PATH),
        acceptance=load_json(ACCEPTANCE_PATH),
        shared_score=load_json(SHARED_SCORE_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
