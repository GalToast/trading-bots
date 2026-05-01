#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PERFECTION_PATH = REPORTS / "adaptive_lattice_perfection_scorecard_board.json"
GUARDED_PATH = REPORTS / "guarded_toxic_flow_contract_board.json"
INCUMBENT_STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
SEAT_PATH = REPORTS / "per_symbol_live_seat_board.json"
TELEMETRY_VISIBILITY_PATH = REPORTS / "phase1_telemetry_visibility_board.json"
TELEMETRY_PRIORITY_PATH = REPORTS / "telemetry_enforcement_priority_board.json"
INHERITED_ACTIVE_PATH = REPORTS / "inherited_vs_active_pnl_board.json"
ESCAPE_PATTERN_PATH = REPORTS / "escape_pattern_analysis_board.json"
NEXT_ACTION_PATH = REPORTS / "max_profit_next_action_board.json"
CONTRACT_GAP_PATH = REPORTS / "max_profit_contract_gap_board.json"
QUEUE_PACKET_PATH = REPORTS / "max_profit_queue_contract_packet.json"
QUEUE_ADOPTION_PATH = REPORTS / "max_profit_queue_adoption_board.json"
QUEUE_PROMOTION_PATH = REPORTS / "max_profit_queue_promotion_board.json"
RUNNER_PLAN_PATH = REPORTS / "adaptive_btc_shadow_runner_plan.json"

OUTPUT_JSON_PATH = REPORTS / "max_profit_lattice_doctrine.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_lattice_doctrine.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def find_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("symbol") or "") == symbol:
            return dict(row)
    return {}


def build_symbol_focus_rows(
    incumbent_study: dict[str, Any],
    seat_board: dict[str, Any],
    guarded_contract: dict[str, Any],
    next_action_board: dict[str, Any],
) -> list[dict[str, Any]]:
    seat_rows = list(seat_board.get("rows") or [])
    guarded_symbols = set((guarded_contract.get("summary") or {}).get("guarded_symbols") or [])
    study_rows = list(incumbent_study.get("rows") or [])
    action_rows = list(next_action_board.get("rows") or [])

    status_order = {
        "study_ready": 0,
        "blocked_runtime_or_launch_gap": 1,
        "adaptive_candidate_without_incumbent": 2,
        "research_only_adaptive_candidate": 3,
        "prior_only_family_gap": 4,
    }

    rows: list[dict[str, Any]] = []
    for row in study_rows:
        symbol = str(row.get("symbol") or "")
        seat_row = find_symbol_row(seat_rows, symbol)
        action_row = find_symbol_row(action_rows, symbol)
        btc_contract = dict(row.get("btc_max_profit_comparison") or {})

        focus_bits = []
        if symbol in guarded_symbols:
            focus_bits.append("guarded burst doctrine applies")
        if seat_row.get("seat_unblocker_action"):
            focus_bits.append(f"seat action `{seat_row.get('seat_unblocker_action')}`")
        if seat_row.get("seat_actionability_status"):
            focus_bits.append(f"actionability `{seat_row.get('seat_actionability_status')}`")
        if btc_contract.get("verdict"):
            focus_bits.append(f"BTC max-profit verdict `{btc_contract.get('verdict')}`")

        rows.append(
            {
                "symbol": symbol,
                "family": str(row.get("asset_class") or ""),
                "study_status": str(row.get("study_status") or ""),
                "adaptive_stage": str(row.get("adaptive_stage") or row.get("adaptive_candidate_verdict") or ""),
                "profit_mode": str(row.get("adaptive_profit_mode") or ""),
                "seat_action": str(seat_row.get("seat_unblocker_action") or ""),
                "seat_actionability_status": str(seat_row.get("seat_actionability_status") or ""),
                "seat_contract_gap_status": str(seat_row.get("seat_contract_gap_status") or ""),
                "seat_overlay_contract_status": str(seat_row.get("seat_overlay_contract_status") or ""),
                "seat_overlay_launch_bridge_status": str(seat_row.get("seat_overlay_launch_bridge_status") or ""),
                "seat_priority_rank": seat_row.get("seat_unblocker_priority_rank"),
                "queue_task_id": str(seat_row.get("seat_unblocker_queue_task_id") or ""),
                "max_profit_posture": str(action_row.get("max_profit_posture") or ""),
                "focus_read": "; ".join(focus_bits) if focus_bits else str(row.get("why") or ""),
                "btc_max_profit_verdict": str(btc_contract.get("verdict") or ""),
            }
        )

    def sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        rank = item.get("seat_priority_rank")
        return (
            status_order.get(item.get("study_status") or "", 99),
            parse_int(rank, 9999) if rank is not None else 9999,
            item.get("symbol") or "",
        )

    return sorted(rows, key=sort_key)


def build_next_actions(
    perfection: dict[str, Any],
    seat_board: dict[str, Any],
    telemetry_priority: dict[str, Any],
    incumbent_study: dict[str, Any],
    guarded_contract: dict[str, Any],
    next_action_board: dict[str, Any],
    contract_gap_board: dict[str, Any],
    queue_packet_board: dict[str, Any],
    queue_adoption_board: dict[str, Any],
    queue_promotion_board: dict[str, Any],
    runner_plan: dict[str, Any],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []

    perfection_summary = dict(perfection.get("summary") or {})
    ready_title = str(perfection_summary.get("highest_priority_ready_title") or "").strip()
    if ready_title:
        actions.append(
            {
                "source": "adaptive_lattice_perfection_scorecard_board",
                "title": ready_title,
                "read": (
                    f"Current adaptive perfection score is `{perfection_summary.get('total_score')}/"
                    f"{perfection_summary.get('max_score')}` and the highest-priority ready seam is "
                    f"`{perfection_summary.get('highest_priority_ready_task_id', '')}`."
                ),
            }
        )

    next_action_summary = dict(next_action_board.get("summary") or {})
    actionable_symbol = str(next_action_summary.get("launch_now_symbols", [None])[0] or "").strip()
    if actionable_symbol:
        action_row = find_symbol_row(list(next_action_board.get("rows") or []), actionable_symbol)
        actions.append(
            {
                "source": "max_profit_next_action_board",
                "title": f"Advance {actionable_symbol} from queue-backed seat action",
                "read": (
                    f"{actionable_symbol} is currently `launch_now` with queue task "
                    f"`{action_row.get('queue_task_id', '')}` and actionability "
                    f"`{action_row.get('seat_actionability_status', '')}`."
                ),
            }
        )

    contract_gap_summary = dict(contract_gap_board.get("summary") or {})
    highest_gap_symbol = str(contract_gap_summary.get("highest_contract_gap_symbol") or "").strip()
    if highest_gap_symbol:
        gap_row = find_symbol_row(list(contract_gap_board.get("rows") or []), highest_gap_symbol)
        actions.append(
            {
                "source": "max_profit_contract_gap_board",
                "title": f"Formalize the missing queue contract for {highest_gap_symbol}",
                "read": (
                    f"{highest_gap_symbol} is the top contract-gap seam with proposed task "
                    f"`{gap_row.get('proposed_queue_task_id', '')}` on "
                    f"`{gap_row.get('proposed_queue_lane', '')}`."
                ),
            }
        )

    queue_promotion_summary = dict(queue_promotion_board.get("summary") or {})
    highest_promotion_symbol = str(queue_promotion_summary.get("highest_promotion_symbol") or "").strip()
    if highest_promotion_symbol:
        promotion_row = find_symbol_row(list(queue_promotion_board.get("rows") or []), highest_promotion_symbol)
        actions.append(
            {
                "source": "max_profit_queue_promotion_board",
                "title": f"Insert the next missing queue row for {highest_promotion_symbol}",
                "read": (
                    f"{highest_promotion_symbol} is the top queue-promotion seam with class "
                    f"`{promotion_row.get('promotion_class', '')}` on task "
                    f"`{promotion_row.get('task_id', '')}`."
                ),
            }
        )

    queue_adoption_summary = dict(queue_adoption_board.get("summary") or {})
    highest_missing_adoption = str(queue_adoption_summary.get("highest_missing_symbol") or "").strip()
    if highest_missing_adoption:
        adoption_row = find_symbol_row(list(queue_adoption_board.get("rows") or []), highest_missing_adoption)
        actions.append(
            {
                "source": "max_profit_queue_adoption_board",
                "title": f"Close the queue adoption gap for {highest_missing_adoption}",
                "read": (
                    f"{highest_missing_adoption} still reads "
                    f"`{adoption_row.get('queue_adoption_status', '')}` with related queue rows "
                    f"`{adoption_row.get('related_symbol_queue_task_ids', [])}`."
                ),
            }
        )

    queue_packet_summary = dict(queue_packet_board.get("summary") or {})
    highest_ready_symbol = str(queue_packet_summary.get("highest_ready_symbol") or "").strip()
    if highest_ready_symbol:
        packet_row = find_symbol_row(list(queue_packet_board.get("rows") or []), highest_ready_symbol)
        actions.append(
            {
                "source": "max_profit_queue_contract_packet",
                "title": f"Keep the concrete queue packet ready for {highest_ready_symbol}",
                "read": (
                    f"{highest_ready_symbol} currently carries packet status "
                    f"`{packet_row.get('proposal_status', '')}` with task "
                    f"`{packet_row.get('task_id', '')}`."
                ),
            }
        )

    seat_summary = dict(seat_board.get("summary") or {})
    overlay_gap_symbols = list(seat_summary.get("overlay_launch_gap_symbols") or [])
    if overlay_gap_symbols:
        supported = list(dict(runner_plan.get("runtime_overlay_contract") or {}).get("supported_overlays") or [])
        requested = list(dict(runner_plan.get("runtime_overlay_contract") or {}).get("requested_overlays") or [])
        executable = list(dict(runner_plan.get("runtime_overlay_contract") or {}).get("executable_overlays") or [])
        unsupported = list(dict(runner_plan.get("runtime_overlay_contract") or {}).get("unsupported_overlays") or [])
        actions.append(
            {
                "source": "per_symbol_live_seat_board",
                "title": f"Align the BTC overlay request state with the launch-capable bridge for {overlay_gap_symbols[0]}",
                "read": (
                    f"Seat truth still shows overlay launch-alignment symbols `{overlay_gap_symbols}` while the BTC runner-plan "
                    f"supports `{supported}`, currently requests `{requested}`, executes `{executable}`, and leaves `{unsupported}` unsupported."
                ),
            }
        )

    telemetry_lanes = list(telemetry_priority.get("lanes") or [])
    if telemetry_lanes:
        top_lane = dict(telemetry_lanes[0])
        actions.append(
            {
                "source": "telemetry_enforcement_priority_board",
                "title": f"Enrich {top_lane.get('lane_name', '')} telemetry before over-claiming adaptive proof",
                "read": (
                    f"{top_lane.get('lane_name', '')} currently leads telemetry enforcement debt with "
                    f"`{top_lane.get('total_closes', 0)}` closes and watchdog status "
                    f"`{top_lane.get('watchdog_status', '')}`."
                ),
            }
        )

    family_gaps = [
        dict(row)
        for row in list(incumbent_study.get("family_coverage") or [])
        if str(row.get("verdict") or "") != "ready_candidate_present"
    ]
    if family_gaps:
        top_gap = family_gaps[0]
        actions.append(
            {
                "source": "adaptive_incumbent_study_board",
                "title": f"Close the current {top_gap.get('family', '')} family coverage gap",
                "read": str(top_gap.get("read") or ""),
            }
        )

    guarded_rows = list(guarded_contract.get("rows") or [])
    if guarded_rows:
        guarded_row = dict(guarded_rows[0])
        contract = dict(guarded_row.get("contract") or {})
        actions.append(
            {
                "source": "guarded_toxic_flow_contract_board",
                "title": f"Keep {guarded_row.get('symbol', '')} on the checked-in guarded burst contract",
                "read": (
                    f"Primary guard remains `{contract.get('primary_entry_guard', '')}` and runtime posture "
                    f"prefers `{contract.get('escape_role', '')}` over spread-threshold gating."
                ),
            }
        )

    return actions[:6]


def build_payload(
    *,
    perfection: dict[str, Any],
    guarded_contract: dict[str, Any],
    incumbent_study: dict[str, Any],
    seat_board: dict[str, Any],
    telemetry_visibility: dict[str, Any],
    telemetry_priority: dict[str, Any],
    inherited_active: dict[str, Any],
    escape_pattern: dict[str, Any],
    next_action_board: dict[str, Any],
    contract_gap_board: dict[str, Any],
    queue_packet_board: dict[str, Any],
    queue_adoption_board: dict[str, Any],
    queue_promotion_board: dict[str, Any],
    runner_plan: dict[str, Any],
) -> dict[str, Any]:
    perfection_summary = dict(perfection.get("summary") or {})
    guarded_summary = dict(guarded_contract.get("summary") or {})
    inherited_summary = dict(inherited_active.get("summary") or {})
    telemetry_summary = dict(telemetry_visibility.get("summary") or {})
    telemetry_priority_summary = dict(telemetry_priority.get("summary") or {})
    escape_summary = dict(escape_pattern.get("summary") or {})
    study_summary = dict(incumbent_study.get("summary") or {})
    seat_summary = dict(seat_board.get("summary") or {})
    next_action_summary = dict(next_action_board.get("summary") or {})
    contract_gap_summary = dict(contract_gap_board.get("summary") or {})
    queue_packet_summary = dict(queue_packet_board.get("summary") or {})
    queue_adoption_summary = dict(queue_adoption_board.get("summary") or {})
    queue_promotion_summary = dict(queue_promotion_board.get("summary") or {})
    overlay_contract = dict(runner_plan.get("runtime_overlay_contract") or {})

    core_truth = {
        "natural_profit_count": parse_int(escape_summary.get("total_natural_profits")),
        "natural_loss_count": parse_int(escape_summary.get("total_natural_losses")),
        "escape_loss_count": parse_int(escape_summary.get("total_escape_losses")),
        "escape_profit_count": parse_int(escape_summary.get("total_escape_profits")),
        "active_realized_usd": round(parse_float(inherited_summary.get("total_active_realized_usd")), 2),
        "inherited_realized_usd": round(parse_float(inherited_summary.get("total_inherited_realized_usd")), 2),
        "active_close_count": parse_int(inherited_summary.get("total_active_closes")),
        "inherited_close_count": parse_int(inherited_summary.get("total_inherited_closes")),
        "active_only_lane_count": parse_int(inherited_summary.get("lanes_active_only")),
        "read": (
            f"Checked-in close economics still favor the lattice: natural profits="
            f"`{parse_int(escape_summary.get('total_natural_profits'))}` vs natural losses="
            f"`{parse_int(escape_summary.get('total_natural_losses'))}`. "
            f"Active realized currently reads `{parse_float(inherited_summary.get('total_active_realized_usd')):+.2f}` "
            f"against inherited `{parse_float(inherited_summary.get('total_inherited_realized_usd')):+.2f}`, "
            "so progress claims still need active-vs-inherited separation."
        ),
    }

    guarded_rows = list(guarded_contract.get("rows") or [])
    guarded_row = dict(guarded_rows[0]) if guarded_rows else {}
    guarded_doctrine = {
        "guarded_symbols": list(guarded_summary.get("guarded_symbols") or []),
        "spread_gate_verdict": str(guarded_summary.get("spread_gate_verdict") or ""),
        "cluster_escape_verdict": str(guarded_summary.get("cluster_escape_verdict") or ""),
        "step_widening_verdict": str(guarded_summary.get("step_widening_verdict") or ""),
        "primary_entry_guard": str(dict(guarded_row.get("contract") or {}).get("primary_entry_guard") or ""),
        "read": str(guarded_summary.get("contract_read") or ""),
    }

    telemetry_truth = {
        "total_event_files": parse_int(telemetry_summary.get("total_event_files")),
        "fully_enriched": parse_int(telemetry_summary.get("fully_enriched")),
        "partially_enriched": parse_int(telemetry_summary.get("partially_enriched")),
        "no_enrichment_with_closes": parse_int(telemetry_summary.get("no_enrichment_with_closes")),
        "high_priority_count": parse_int(telemetry_priority_summary.get("high_priority_count")),
        "medium_priority_count": parse_int(telemetry_priority_summary.get("medium_priority_count")),
        "read": (
            f"Phase-1 telemetry coverage is still the main proof-integrity tax: "
            f"`{parse_int(telemetry_summary.get('fully_enriched'))}` fully enriched files, "
            f"`{parse_int(telemetry_summary.get('partially_enriched'))}` partial, and "
            f"`{parse_int(telemetry_summary.get('no_enrichment_with_closes'))}` close-bearing files with no enrichment. "
            f"Current enforcement backlog is `{parse_int(telemetry_priority_summary.get('high_priority_count'))}` high + "
            f"`{parse_int(telemetry_priority_summary.get('medium_priority_count'))}` medium priority lanes."
        ),
    }

    adaptive_truth = {
        "score": parse_int(perfection_summary.get("total_score")),
        "max_score": parse_int(perfection_summary.get("max_score")),
        "overall_verdict": str(perfection_summary.get("overall_verdict") or ""),
        "btc_max_profit_verdict": str(dict(study_summary.get("btc_max_profit_contract") or {}).get("verdict") or ""),
        "study_ready_symbols": list(study_summary.get("study_ready_symbols") or []),
        "blocked_symbols": list(study_summary.get("blocked_symbols") or []),
        "research_only_symbols": list(study_summary.get("research_only_symbols") or []),
        "family_coverage": dict(study_summary.get("family_coverage") or {}),
        "read": (
            f"Adaptive perfection currently reads `{parse_int(perfection_summary.get('total_score'))}/"
            f"{parse_int(perfection_summary.get('max_score'))}` -> "
            f"`{perfection_summary.get('overall_verdict', '')}`. "
            f"Study-ready symbols are `{study_summary.get('study_ready_symbols', [])}`, and the BTC max-profit contract "
            f"still reads `{dict(study_summary.get('btc_max_profit_contract') or {}).get('verdict', '')}`."
        ),
    }

    seat_truth = {
        "highest_priority_seat_symbol": str(seat_summary.get("highest_priority_seat_symbol") or ""),
        "highest_actionable_seat_symbol": str(seat_summary.get("highest_actionable_seat_symbol") or ""),
        "highest_actionable_queue_backed_symbol": str(seat_summary.get("highest_actionable_queue_backed_symbol") or ""),
        "actionable_unqueued_symbols": list(seat_summary.get("actionable_unqueued_symbols") or []),
        "queue_precedes_seat_symbols": list(seat_summary.get("queue_precedes_seat_symbols") or []),
        "overlay_launch_gap_symbols": list(seat_summary.get("overlay_launch_gap_symbols") or []),
        "read": (
            f"Seat truth says priority and actionability are no longer the same thing: highest queue priority is "
            f"`{seat_summary.get('highest_priority_seat_symbol', '')}`, but the highest actionable seat symbol is "
            f"`{seat_summary.get('highest_actionable_seat_symbol', '')}`."
        ),
    }

    execution_truth = {
        "launch_now_symbols": list(next_action_summary.get("launch_now_symbols") or []),
        "preparatory_symbols": list(next_action_summary.get("preparatory_symbols") or []),
        "queue_contract_missing_symbols": list(next_action_summary.get("queue_contract_missing_symbols") or []),
        "highest_contract_gap_symbol": str(contract_gap_summary.get("highest_contract_gap_symbol") or ""),
        "read": (
            f"Executable max-profit seams currently read launch-now=`{next_action_summary.get('launch_now_symbols', [])}`, "
            f"preparatory=`{next_action_summary.get('preparatory_symbols', [])}`, and contract-gap backlog="
            f"`{contract_gap_summary.get('contract_gap_symbols', [])}`."
        ),
    }

    queue_truth = {
        "packet_symbols": list(queue_packet_summary.get("proposal_symbols") or []),
        "highest_ready_symbol": str(queue_packet_summary.get("highest_ready_symbol") or ""),
        "adopted_count": parse_int(queue_adoption_summary.get("adopted_count")),
        "missing_adoption_count": parse_int(queue_adoption_summary.get("missing_count")),
        "highest_missing_adoption_symbol": str(queue_adoption_summary.get("highest_missing_symbol") or ""),
        "highest_promotion_symbol": str(queue_promotion_summary.get("highest_promotion_symbol") or ""),
        "promotion_symbols": list(queue_promotion_summary.get("promotion_symbols") or []),
        "read": (
            f"Queue insertion truth now reads packet=`{queue_packet_summary.get('proposal_symbols', [])}`, "
            f"adopted=`{parse_int(queue_adoption_summary.get('adopted_count'))}`, missing adoption="
            f"`{parse_int(queue_adoption_summary.get('missing_count'))}`, and promotion order="
            f"`{queue_promotion_summary.get('promotion_symbols', [])}`."
        ),
    }

    overlay_launch_truth = {
        "overlay_launch_gap_symbols": list(seat_summary.get("overlay_launch_gap_symbols") or []),
        "supported_overlays": list(overlay_contract.get("supported_overlays") or []),
        "requested_overlays": list(overlay_contract.get("requested_overlays") or []),
        "executable_overlays": list(overlay_contract.get("executable_overlays") or []),
        "unsupported_overlays": list(overlay_contract.get("unsupported_overlays") or []),
        "read": (
            f"Overlay launch truth currently reads seat alignment symbols `{seat_summary.get('overlay_launch_gap_symbols', [])}` "
            f"while the BTC runner plan supports `{overlay_contract.get('supported_overlays', [])}`, requests `{overlay_contract.get('requested_overlays', [])}`, executes "
            f"`{overlay_contract.get('executable_overlays', [])}`, and leaves `{overlay_contract.get('unsupported_overlays', [])}` unsupported."
        ),
    }

    symbol_focus = build_symbol_focus_rows(
        incumbent_study=incumbent_study,
        seat_board=seat_board,
        guarded_contract=guarded_contract,
        next_action_board=next_action_board,
    )
    next_actions = build_next_actions(
        perfection=perfection,
        seat_board=seat_board,
        telemetry_priority=telemetry_priority,
        incumbent_study=incumbent_study,
        guarded_contract=guarded_contract,
        next_action_board=next_action_board,
        contract_gap_board=contract_gap_board,
        queue_packet_board=queue_packet_board,
        queue_adoption_board=queue_adoption_board,
        queue_promotion_board=queue_promotion_board,
        runner_plan=runner_plan,
    )

    leadership_read = [
        (
            f"Max-profit adaptive doctrine currently reads "
            f"`{adaptive_truth['score']}/{adaptive_truth['max_score']}` -> "
            f"`{adaptive_truth['overall_verdict']}`."
        ),
        core_truth["read"],
        guarded_doctrine["read"] or "No guarded-toxic-flow contract rows are available.",
        telemetry_truth["read"],
        execution_truth["read"],
        queue_truth["read"],
        overlay_launch_truth["read"],
        seat_truth["read"],
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(PERFECTION_PATH.relative_to(ROOT)),
            str(GUARDED_PATH.relative_to(ROOT)),
            str(INCUMBENT_STUDY_PATH.relative_to(ROOT)),
            str(SEAT_PATH.relative_to(ROOT)),
            str(TELEMETRY_VISIBILITY_PATH.relative_to(ROOT)),
            str(TELEMETRY_PRIORITY_PATH.relative_to(ROOT)),
            str(INHERITED_ACTIVE_PATH.relative_to(ROOT)),
            str(ESCAPE_PATTERN_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(CONTRACT_GAP_PATH.relative_to(ROOT)),
            str(QUEUE_PACKET_PATH.relative_to(ROOT)),
            str(QUEUE_ADOPTION_PATH.relative_to(ROOT)),
            str(QUEUE_PROMOTION_PATH.relative_to(ROOT)),
            str(RUNNER_PLAN_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "perfection_score": f"{adaptive_truth['score']}/{adaptive_truth['max_score']}",
            "overall_verdict": adaptive_truth["overall_verdict"],
            "btc_max_profit_verdict": adaptive_truth["btc_max_profit_verdict"],
            "study_ready_symbols": adaptive_truth["study_ready_symbols"],
            "guarded_symbols": guarded_doctrine["guarded_symbols"],
            "highest_priority_ready_task_id": str(perfection_summary.get("highest_priority_ready_task_id") or ""),
            "highest_actionable_seat_symbol": seat_truth["highest_actionable_seat_symbol"],
            "highest_actionable_queue_backed_symbol": seat_truth["highest_actionable_queue_backed_symbol"],
            "highest_contract_gap_symbol": execution_truth["highest_contract_gap_symbol"],
            "highest_promotion_symbol": queue_truth["highest_promotion_symbol"],
            "missing_queue_adoption_count": queue_truth["missing_adoption_count"],
            "launch_now_symbols": execution_truth["launch_now_symbols"],
            "overlay_launch_gap_symbols": overlay_launch_truth["overlay_launch_gap_symbols"],
            "telemetry_close_bearing_gap_count": telemetry_truth["no_enrichment_with_closes"],
            "telemetry_priority_count": telemetry_truth["high_priority_count"] + telemetry_truth["medium_priority_count"],
        },
        "leadership_read": leadership_read,
        "core_truth": core_truth,
        "guarded_doctrine": guarded_doctrine,
        "telemetry_truth": telemetry_truth,
        "adaptive_truth": adaptive_truth,
        "execution_truth": execution_truth,
        "queue_truth": queue_truth,
        "overlay_launch_truth": overlay_launch_truth,
        "seat_truth": seat_truth,
        "symbol_focus": symbol_focus,
        "next_actions": next_actions,
        "notes": [
            "This doctrine surface is passive synthesis. It compresses current authority boards into one max-profit read; it does not override queue, seat, or runtime control surfaces.",
            "Use it to keep cross-board claims honest: close economics first, guarded-burst doctrine second, telemetry integrity third, then executable seams, overlay launch-bridge truth, and seat actionability.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    core_truth = dict(payload.get("core_truth") or {})
    guarded = dict(payload.get("guarded_doctrine") or {})
    telemetry = dict(payload.get("telemetry_truth") or {})
    adaptive = dict(payload.get("adaptive_truth") or {})
    execution = dict(payload.get("execution_truth") or {})
    queue_truth = dict(payload.get("queue_truth") or {})
    overlay_launch = dict(payload.get("overlay_launch_truth") or {})
    seat = dict(payload.get("seat_truth") or {})

    lines = [
        "# Max-Profit Adaptive Lattice Doctrine",
        "",
        "Generated passive synthesis surface for the current adaptive authority stack.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- perfection_score: `{summary.get('perfection_score', '')}`",
        f"- overall_verdict: `{summary.get('overall_verdict', '')}`",
        f"- btc_max_profit_verdict: `{summary.get('btc_max_profit_verdict', '')}`",
        f"- study_ready_symbols: `{summary.get('study_ready_symbols', [])}`",
        f"- guarded_symbols: `{summary.get('guarded_symbols', [])}`",
        f"- launch_now_symbols: `{summary.get('launch_now_symbols', [])}`",
        "",
        "## Leadership Read",
        "",
    ]

    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Core Truth",
            "",
            f"- natural_profit_count: `{core_truth.get('natural_profit_count', 0)}`",
            f"- natural_loss_count: `{core_truth.get('natural_loss_count', 0)}`",
            f"- escape_loss_count: `{core_truth.get('escape_loss_count', 0)}`",
            f"- active_realized_usd: `{core_truth.get('active_realized_usd', 0.0):+.2f}`",
            f"- inherited_realized_usd: `{core_truth.get('inherited_realized_usd', 0.0):+.2f}`",
            f"- read: {core_truth.get('read', '')}",
            "",
            "## Guarded Doctrine",
            "",
            f"- primary_entry_guard: `{guarded.get('primary_entry_guard', '')}`",
            f"- spread_gate_verdict: `{guarded.get('spread_gate_verdict', '')}`",
            f"- cluster_escape_verdict: `{guarded.get('cluster_escape_verdict', '')}`",
            f"- step_widening_verdict: `{guarded.get('step_widening_verdict', '')}`",
            f"- read: {guarded.get('read', '')}",
            "",
            "## Telemetry Truth",
            "",
            f"- total_event_files: `{telemetry.get('total_event_files', 0)}`",
            f"- fully_enriched: `{telemetry.get('fully_enriched', 0)}`",
            f"- partially_enriched: `{telemetry.get('partially_enriched', 0)}`",
            f"- no_enrichment_with_closes: `{telemetry.get('no_enrichment_with_closes', 0)}`",
            f"- high_priority_count: `{telemetry.get('high_priority_count', 0)}`",
            f"- medium_priority_count: `{telemetry.get('medium_priority_count', 0)}`",
            f"- read: {telemetry.get('read', '')}",
            "",
            "## Adaptive Truth",
            "",
            f"- score: `{adaptive.get('score', 0)}/{adaptive.get('max_score', 0)}`",
            f"- overall_verdict: `{adaptive.get('overall_verdict', '')}`",
            f"- btc_max_profit_verdict: `{adaptive.get('btc_max_profit_verdict', '')}`",
            f"- family_coverage: `{adaptive.get('family_coverage', {})}`",
            f"- read: {adaptive.get('read', '')}",
            "",
            "## Execution Truth",
            "",
            f"- launch_now_symbols: `{execution.get('launch_now_symbols', [])}`",
            f"- preparatory_symbols: `{execution.get('preparatory_symbols', [])}`",
            f"- queue_contract_missing_symbols: `{execution.get('queue_contract_missing_symbols', [])}`",
            f"- highest_contract_gap_symbol: `{execution.get('highest_contract_gap_symbol', '')}`",
            f"- read: {execution.get('read', '')}",
            "",
            "## Queue Truth",
            "",
            f"- packet_symbols: `{queue_truth.get('packet_symbols', [])}`",
            f"- highest_ready_symbol: `{queue_truth.get('highest_ready_symbol', '')}`",
            f"- adopted_count: `{queue_truth.get('adopted_count', 0)}`",
            f"- missing_adoption_count: `{queue_truth.get('missing_adoption_count', 0)}`",
            f"- highest_missing_adoption_symbol: `{queue_truth.get('highest_missing_adoption_symbol', '')}`",
            f"- highest_promotion_symbol: `{queue_truth.get('highest_promotion_symbol', '')}`",
            f"- promotion_symbols: `{queue_truth.get('promotion_symbols', [])}`",
            f"- read: {queue_truth.get('read', '')}",
            "",
            "## Overlay Launch Truth",
            "",
            f"- overlay_launch_gap_symbols: `{overlay_launch.get('overlay_launch_gap_symbols', [])}`",
            f"- supported_overlays: `{overlay_launch.get('supported_overlays', [])}`",
            f"- requested_overlays: `{overlay_launch.get('requested_overlays', [])}`",
            f"- executable_overlays: `{overlay_launch.get('executable_overlays', [])}`",
            f"- unsupported_overlays: `{overlay_launch.get('unsupported_overlays', [])}`",
            f"- read: {overlay_launch.get('read', '')}",
            "",
            "## Seat Truth",
            "",
            f"- highest_priority_seat_symbol: `{seat.get('highest_priority_seat_symbol', '')}`",
            f"- highest_actionable_seat_symbol: `{seat.get('highest_actionable_seat_symbol', '')}`",
            f"- highest_actionable_queue_backed_symbol: `{seat.get('highest_actionable_queue_backed_symbol', '')}`",
            f"- actionable_unqueued_symbols: `{seat.get('actionable_unqueued_symbols', [])}`",
            f"- queue_precedes_seat_symbols: `{seat.get('queue_precedes_seat_symbols', [])}`",
            f"- read: {seat.get('read', '')}",
            "",
            "## Symbol Focus",
            "",
            "| Symbol | Family | Study Status | Profit Mode | Posture | Seat Action | Actionability | Contract Gap | Overlay Bridge |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )

    for row in list(payload.get("symbol_focus") or []):
        lines.append(
            f"| `{row.get('symbol', '')}` | `{row.get('family', '')}` | `{row.get('study_status', '')}` | "
            f"`{row.get('profit_mode', '')}` | `{row.get('max_profit_posture', '')}` | `{row.get('seat_action', '')}` | "
            f"`{row.get('seat_actionability_status', '')}` | `{row.get('seat_contract_gap_status', '')}` | "
            f"`{row.get('seat_overlay_launch_bridge_status', '')}` |"
        )

    lines.extend(["", "## Next Actions", ""])
    for item in list(payload.get("next_actions") or []):
        lines.append(f"- `{item.get('source', '')}`: **{item.get('title', '')}**. {item.get('read', '')}")

    lines.extend(["", "## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        perfection=load_json(PERFECTION_PATH),
        guarded_contract=load_json(GUARDED_PATH),
        incumbent_study=load_json(INCUMBENT_STUDY_PATH),
        seat_board=load_json(SEAT_PATH),
        telemetry_visibility=load_json(TELEMETRY_VISIBILITY_PATH),
        telemetry_priority=load_json(TELEMETRY_PRIORITY_PATH),
        inherited_active=load_json(INHERITED_ACTIVE_PATH),
        escape_pattern=load_json(ESCAPE_PATTERN_PATH),
        next_action_board=load_json(NEXT_ACTION_PATH),
        contract_gap_board=load_json(CONTRACT_GAP_PATH),
        queue_packet_board=load_json(QUEUE_PACKET_PATH),
        queue_adoption_board=load_json(QUEUE_ADOPTION_PATH),
        queue_promotion_board=load_json(QUEUE_PROMOTION_PATH),
        runner_plan=load_json(RUNNER_PLAN_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
