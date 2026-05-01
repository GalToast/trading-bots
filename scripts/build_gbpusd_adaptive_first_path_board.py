#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PACKET_PATH = REPORTS / "gbpusd_adaptive_shadow_packet.json"
OVERNIGHT_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"
WATCHER_STATE_PATH = REPORTS / "adaptive_overnight_launch_packet_monitor_state.json"
QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
SEAT_PATH = REPORTS / "per_symbol_live_seat_board.json"
STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
SHARED_SCORE_PATH = REPORTS / "adaptive_shared_score_board.json"
ACCEPTANCE_PATH = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
OUTPUT_JSON_PATH = REPORTS / "gbpusd_adaptive_first_path_board.json"
OUTPUT_MD_PATH = REPORTS / "gbpusd_adaptive_first_path_board.md"

SYMBOL = "GBPUSD"
PACKET_ID = "gbpusd_adaptive_comparison_packet"
CANDIDATE_ID = "gbpusd_adaptive_comparison_packet"
WATCHER_STATE_MAX_AGE_SECONDS = 20 * 60


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def find_row(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    needle = str(value or "").upper()
    for row in rows:
        if str(row.get(key) or "").upper() == needle:
            return dict(row)
    return {}


def summarize_acceptance(candidate: dict[str, Any]) -> dict[str, Any]:
    warning_checks: list[str] = []
    warning_reads: list[str] = []
    for check in list(candidate.get("checks") or []):
        if str(check.get("status") or "") == "warn":
            warning_checks.append(str(check.get("check_id") or ""))
            warning_reads.append(str(check.get("read") or ""))
    return {
        "verdict": str(candidate.get("verdict") or ""),
        "candidate_read": str(candidate.get("candidate_read") or ""),
        "warning_checks": warning_checks,
        "warning_reads": warning_reads,
        "queue_status": str(candidate.get("queue_status") or ""),
    }


def parse_iso_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _watcher_source_payload(
    watcher_state: dict[str, Any],
    *,
    checked_at: str,
    watcher_age_seconds: float | None,
) -> dict[str, Any]:
    return {
        "source": relative_path_text(WATCHER_STATE_PATH),
        "source_status": "watcher_state_fresh",
        "checked_at": checked_at,
        "watcher_checked_at": checked_at,
        "watcher_age_seconds": watcher_age_seconds,
        "watcher_max_age_seconds": WATCHER_STATE_MAX_AGE_SECONDS,
        "action_status": str(watcher_state.get("gbp_action_status") or ""),
        "execution_watchdog_status": str(watcher_state.get("gbp_execution_watchdog_status") or ""),
        "current_run_trade_opens": int(watcher_state.get("gbp_current_run_trade_opens", 0) or 0),
        "current_run_trade_closes": int(watcher_state.get("gbp_current_run_trade_closes", 0) or 0),
        "pre_start_trade_opens": int(watcher_state.get("gbp_pre_start_trade_opens", 0) or 0),
        "pre_start_trade_closes": int(watcher_state.get("gbp_pre_start_trade_closes", 0) or 0),
        "first_path_verdict": str(watcher_state.get("gbp_first_path_verdict") or ""),
        "first_path_rationale": str(watcher_state.get("gbp_first_path_rationale") or ""),
        "first_path_close_realized_pnl": watcher_state.get("gbp_first_path_close_realized_pnl"),
        "first_path_open_entry_context": str(watcher_state.get("gbp_first_path_open_entry_context") or ""),
    }


def _overnight_source_payload(
    overnight_row: dict[str, Any],
    *,
    source_status: str,
    watcher_checked_at: str = "",
    watcher_age_seconds: float | None = None,
) -> dict[str, Any]:
    return {
        "source": relative_path_text(OVERNIGHT_PATH),
        "source_status": source_status,
        "checked_at": "",
        "watcher_checked_at": watcher_checked_at,
        "watcher_age_seconds": watcher_age_seconds,
        "watcher_max_age_seconds": WATCHER_STATE_MAX_AGE_SECONDS,
        "action_status": str(overnight_row.get("action_status") or ""),
        "execution_watchdog_status": str(overnight_row.get("execution_watchdog_status") or ""),
        "current_run_trade_opens": int(overnight_row.get("artifact_trade_opens", 0) or 0),
        "current_run_trade_closes": int(overnight_row.get("artifact_trade_closes", 0) or 0),
        "pre_start_trade_opens": int(overnight_row.get("artifact_pre_start_trade_opens", 0) or 0),
        "pre_start_trade_closes": int(overnight_row.get("artifact_pre_start_trade_closes", 0) or 0),
        "first_path_verdict": str(overnight_row.get("first_path_verdict") or ""),
        "first_path_rationale": str(overnight_row.get("first_path_rationale") or ""),
        "first_path_close_realized_pnl": overnight_row.get("first_path_close_realized_pnl"),
        "first_path_open_entry_context": str(overnight_row.get("first_path_open_entry_context") or ""),
    }


def choose_first_path_source(
    overnight_row: dict[str, Any],
    watcher_state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if watcher_state:
        now_utc = now or utc_now()
        watcher_checked_at = str(watcher_state.get("checked_at") or "")
        parsed_checked_at = parse_iso_timestamp(watcher_checked_at)
        watcher_age_seconds = None
        if parsed_checked_at is not None:
            watcher_age_seconds = max(0.0, (now_utc - parsed_checked_at).total_seconds())
            if watcher_age_seconds <= WATCHER_STATE_MAX_AGE_SECONDS:
                return _watcher_source_payload(
                    watcher_state,
                    checked_at=watcher_checked_at,
                    watcher_age_seconds=watcher_age_seconds,
                )
        return {
            **_overnight_source_payload(
                overnight_row,
                source_status="watcher_state_stale_fallback_to_overnight",
                watcher_checked_at=watcher_checked_at,
                watcher_age_seconds=watcher_age_seconds,
            )
        }
    return _overnight_source_payload(overnight_row, source_status="overnight_board_only")


def proof_gate_status(*, first_path_verdict: str, action_status: str, shared_comparison_verdict: str, shared_basis: str) -> tuple[str, str]:
    if shared_comparison_verdict not in {"", "no_adaptive_score"} and shared_basis not in {"", "missing"}:
        return (
            "shared_score_comparable",
            "Adaptive proof now exposes a real score basis on the shared-score surface, so the symbol has crossed from launch-gap triage into honest incumbent-versus-adaptive comparison.",
        )
    if first_path_verdict not in {"", "awaiting_first_trade_path_event"}:
        return (
            "first_path_recorded_wait_shared_score_refresh",
            "A real first-path verdict now exists for the dedicated GBP lane. Refresh the shared-score/study surfaces and judge whether the first proof is strong enough to become a real adaptive basis.",
        )
    if action_status == "hold_launch_packet_defined_not_started":
        return (
            "packet_defined_waiting_launch",
            "The dedicated packet is explicit and queue-backed, but no fresh runner window exists yet. The next honest step is deliberate shadow launch and the first lane-local proof path.",
        )
    if action_status == "already_running_monitor_only":
        return (
            "launched_waiting_first_path",
            "The dedicated packet is running, but it still has no first-path outcome. Stay in proof collection until the first close-like result lands.",
        )
    return (
        "proof_state_unclear",
        "GBP proof state is present but not yet cleanly classifiable from the current passive surfaces.",
    )


def derive_queue_why(
    source_why: str,
    *,
    action_status: str,
    first_path_verdict: str,
    current_run_trade_opens: int,
    current_run_trade_closes: int,
) -> str:
    if action_status == "already_running_monitor_only":
        if first_path_verdict == "first_path_opened_waiting_close":
            return (
                "GBPUSD has an explicit adaptive trend-harvest packet and the dedicated lane is already running with "
                f"`{current_run_trade_opens}` current-run opens / `{current_run_trade_closes}` closes. The next honest "
                "step is no longer launch; it is first-close collection and shared-score refresh on the same "
                "incumbent-comparison surface."
            )
        if first_path_verdict not in {"", "awaiting_first_trade_path_event"}:
            return (
                "GBPUSD has an explicit adaptive trend-harvest packet and the dedicated lane has already produced a "
                f"real first-path verdict `{first_path_verdict}`. The next honest step is shared-score refresh and "
                "incumbent-versus-adaptive comparison, not packet or launch debate."
            )
        return (
            "GBPUSD has an explicit adaptive trend-harvest packet and the dedicated lane is already running, but it "
            "still needs its first lane-local path event before shared-score comparison becomes honest."
        )
    return source_why


def derive_study_view(source_row: dict[str, Any], *, first_path: dict[str, Any]) -> dict[str, Any]:
    source_study_status = str(source_row.get("study_status") or "")
    source_runtime_status = str(source_row.get("adaptive_runtime_status") or "")
    source_overlay_read = str(source_row.get("adaptive_runtime_overlay_read") or "")
    source_why = str(source_row.get("why") or "")

    action_status = str(first_path.get("action_status") or "")
    first_path_verdict = str(first_path.get("first_path_verdict") or "")

    if first_path_verdict == "first_path_opened_waiting_close":
        return {
            "study_status": "first_path_opened_wait_shared_score_refresh",
            "adaptive_profit_mode": str(source_row.get("adaptive_profit_mode") or ""),
            "adaptive_runtime_status": action_status or source_runtime_status,
            "adaptive_runtime_overlay_read": source_overlay_read,
            "why": (
                "A credible adaptive challenger is no longer blocked on launch/runtime. The dedicated GBP lane is "
                "already running and has opened a real first path, so the remaining blocker is first-close/shared-score "
                "conversion rather than launch."
            ),
            "source_study_status": source_study_status,
            "source_adaptive_runtime_status": source_runtime_status,
            "source_why": source_why,
        }
    if first_path_verdict not in {"", "awaiting_first_trade_path_event"}:
        return {
            "study_status": "first_path_recorded_wait_shared_score_refresh",
            "adaptive_profit_mode": str(source_row.get("adaptive_profit_mode") or ""),
            "adaptive_runtime_status": action_status or source_runtime_status,
            "adaptive_runtime_overlay_read": source_overlay_read,
            "why": (
                "A credible adaptive challenger now has real first-path evidence, so the remaining study debt is score "
                "refresh and comparable incumbent-versus-adaptive judgment rather than runtime launch."
            ),
            "source_study_status": source_study_status,
            "source_adaptive_runtime_status": source_runtime_status,
            "source_why": source_why,
        }
    if action_status == "already_running_monitor_only":
        return {
            "study_status": "launched_waiting_first_path",
            "adaptive_profit_mode": str(source_row.get("adaptive_profit_mode") or ""),
            "adaptive_runtime_status": action_status or source_runtime_status,
            "adaptive_runtime_overlay_read": source_overlay_read,
            "why": (
                "The dedicated GBP lane is already running, so the comparison is no longer blocked on launch. It is "
                "still waiting for the first lane-local proof path before shared-score comparison becomes honest."
            ),
            "source_study_status": source_study_status,
            "source_adaptive_runtime_status": source_runtime_status,
            "source_why": source_why,
        }
    return {
        "study_status": source_study_status,
        "adaptive_profit_mode": str(source_row.get("adaptive_profit_mode") or ""),
        "adaptive_runtime_status": source_runtime_status,
        "adaptive_runtime_overlay_read": source_overlay_read,
        "why": source_why,
        "source_study_status": source_study_status,
        "source_adaptive_runtime_status": source_runtime_status,
        "source_why": source_why,
    }


def derive_shared_view(source_row: dict[str, Any], *, first_path: dict[str, Any]) -> dict[str, Any]:
    source_adaptive = dict(source_row.get("adaptive") or {})
    first_path_verdict = str(first_path.get("first_path_verdict") or "")
    current_run_trade_closes = int(first_path.get("current_run_trade_closes", 0) or 0)
    first_path_close_realized_pnl = first_path.get("first_path_close_realized_pnl")

    comparison_verdict = str(source_row.get("comparison_verdict") or "")
    shared_score_ready = bool(source_row.get("shared_score_ready"))
    score_gap = source_row.get("score_gap")
    adaptive_basis = str(source_adaptive.get("basis") or "")
    adaptive_first_path_verdict = str(source_adaptive.get("first_path_verdict") or "")
    adaptive_score_unavailable_reason = str(source_adaptive.get("score_unavailable_reason") or "")
    why = str(source_row.get("why") or "")

    if comparison_verdict == "no_adaptive_score" and first_path_verdict == "first_path_opened_waiting_close":
        adaptive_first_path_verdict = first_path_verdict
        adaptive_score_unavailable_reason = "adaptive_first_close_not_recorded_yet"
        why = (
            "The dedicated GBP challenger is live and has opened a real first path, but the shared score still lacks "
            "a closed-path realized-profit basis."
        )
    elif (
        comparison_verdict == "no_adaptive_score"
        and first_path_verdict not in {"", "awaiting_first_trade_path_event"}
        and (current_run_trade_closes > 0 or first_path_close_realized_pnl is not None)
    ):
        adaptive_first_path_verdict = first_path_verdict
        adaptive_score_unavailable_reason = "shared_score_refresh_pending_after_first_path"
        why = (
            "The dedicated GBP challenger already has first-path evidence that should now feed the shared-score "
            "surface, but the comparable score basis has not refreshed yet."
        )

    return {
        "comparison_verdict": comparison_verdict,
        "shared_score_ready": shared_score_ready,
        "score_gap": score_gap,
        "adaptive_basis": adaptive_basis,
        "adaptive_first_path_verdict": adaptive_first_path_verdict,
        "adaptive_score_unavailable_reason": adaptive_score_unavailable_reason,
        "why": why,
    }


def build_payload(
    packet_payload: dict[str, Any],
    overnight_payload: dict[str, Any],
    watcher_state: dict[str, Any],
    queue_payload: dict[str, Any],
    seat_payload: dict[str, Any],
    study_payload: dict[str, Any],
    shared_payload: dict[str, Any],
    acceptance_payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    queue_row = find_row(list(queue_payload.get("tasks") or []), "task_id", PACKET_ID)
    seat_row = find_row(list(seat_payload.get("rows") or []), "symbol", SYMBOL)
    study_row = find_row(list(study_payload.get("rows") or []), "symbol", SYMBOL)
    shared_row = find_row(list(shared_payload.get("rows") or []), "symbol", SYMBOL)
    acceptance_row = find_row(list(acceptance_payload.get("candidates") or []), "candidate_id", CANDIDATE_ID)
    overnight_row = find_row(list(overnight_payload.get("rows") or []), "packet_id", PACKET_ID)
    acceptance_summary = summarize_acceptance(acceptance_row)
    first_path = choose_first_path_source(overnight_row, watcher_state, now=now)
    packet_contract = dict(packet_payload.get("packet_contract") or {})
    packet_summary = dict(packet_payload.get("summary") or {})
    study_view = derive_study_view(study_row, first_path=first_path)
    shared_view = derive_shared_view(shared_row, first_path=first_path)
    queue_why = derive_queue_why(
        str(queue_row.get("why") or ""),
        action_status=str(first_path.get("action_status") or ""),
        first_path_verdict=str(first_path.get("first_path_verdict") or ""),
        current_run_trade_opens=int(first_path.get("current_run_trade_opens", 0) or 0),
        current_run_trade_closes=int(first_path.get("current_run_trade_closes", 0) or 0),
    )

    gate_status, gate_read = proof_gate_status(
        first_path_verdict=str(first_path.get("first_path_verdict") or ""),
        action_status=str(first_path.get("action_status") or ""),
        shared_comparison_verdict=str(shared_row.get("comparison_verdict") or ""),
        shared_basis=str(shared_view.get("adaptive_basis") or ""),
    )

    leadership_read = [
        f"GBP is currently the highest actionable queue-backed seat move: seat actionability is `{seat_row.get('seat_actionability_status')}` and queue contract-gap status is `{seat_row.get('seat_contract_gap_status')}`.",
        f"Seat execution gate currently reads `{seat_row.get('seat_execution_gate_status')}`.",
        f"The dedicated GBP adaptive lane remains `{first_path.get('action_status')}` with first-path verdict `{first_path.get('first_path_verdict')}`.",
        f"Incumbent-versus-adaptive study status is `{study_view.get('study_status')}`, and shared-score status is `{shared_view.get('comparison_verdict')}` because adaptive basis currently reads `{shared_view.get('adaptive_basis') or 'missing'}`.",
        gate_read,
    ]
    if str(first_path.get("source_status") or "") == "watcher_state_stale_fallback_to_overnight":
        leadership_read.append(
            "Watcher-state proof was present but stale, so this board fell back to the overnight packet surface instead of trusting old monitor-state as fresh proof."
        )
    if acceptance_summary["warning_reads"]:
        leadership_read.append(f"Acceptance gate is still `{acceptance_summary['verdict']}`; the main remaining checklist warnings are `{acceptance_summary['warning_checks']}`.")

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            relative_path_text(PACKET_PATH),
            relative_path_text(OVERNIGHT_PATH),
            relative_path_text(WATCHER_STATE_PATH),
            relative_path_text(QUEUE_PATH),
            relative_path_text(SEAT_PATH),
            relative_path_text(STUDY_PATH),
            relative_path_text(SHARED_SCORE_PATH),
            relative_path_text(ACCEPTANCE_PATH),
        ],
        "summary": {
            "symbol": SYMBOL,
            "packet_id": PACKET_ID,
            "adaptive_lane": str(packet_contract.get("lane_name") or ""),
            "proof_gate_status": gate_status,
            "seat_actionability_status": str(seat_row.get("seat_actionability_status") or ""),
            "seat_contract_gap_status": str(seat_row.get("seat_contract_gap_status") or ""),
            "seat_execution_gate_status": str(seat_row.get("seat_execution_gate_status") or ""),
            "queue_status": str(queue_row.get("status") or ""),
            "queue_priority": queue_row.get("priority"),
            "overnight_action_status": str(first_path.get("action_status") or ""),
            "runtime_truth_source": str(first_path.get("source") or ""),
            "runtime_truth_source_status": str(first_path.get("source_status") or ""),
            "first_path_verdict": str(first_path.get("first_path_verdict") or ""),
            "study_status": str(study_view.get("study_status") or ""),
            "shared_score_verdict": str(shared_view.get("comparison_verdict") or ""),
            "shared_adaptive_basis": str(shared_view.get("adaptive_basis") or "missing"),
            "acceptance_verdict": acceptance_summary["verdict"],
        },
        "leadership_read": leadership_read,
        "packet_contract": {
            "status": str(packet_payload.get("status") or ""),
            "research_posture": str(packet_summary.get("research_posture") or ""),
            "forward_gate": str(packet_summary.get("forward_gate") or ""),
            "lane_name": str(packet_contract.get("lane_name") or ""),
            "state_path": str(packet_contract.get("state_path") or ""),
            "event_path": str(packet_contract.get("event_path") or ""),
            "command": list(packet_contract.get("command") or []),
            "step": packet_contract.get("step"),
            "step_buy": packet_contract.get("step_buy"),
            "step_sell": packet_contract.get("step_sell"),
            "raw_close_alpha": packet_contract.get("raw_close_alpha"),
            "raw_rearm_variant": str(packet_contract.get("raw_rearm_variant") or ""),
            "raw_sell_gap": packet_contract.get("raw_sell_gap"),
            "raw_buy_gap": packet_contract.get("raw_buy_gap"),
        },
        "seat": {
            "seat_verdict": str(seat_row.get("seat_verdict") or ""),
            "incumbent_lane": str(seat_row.get("current_live_holder_lane") or ""),
            "incumbent_booked_usd": seat_row.get("current_live_holder_booked_usd"),
            "seat_unblocker_action": str(seat_row.get("seat_unblocker_action") or ""),
            "seat_unblocker_read": str(seat_row.get("seat_unblocker_read") or ""),
            "seat_actionability_status": str(seat_row.get("seat_actionability_status") or ""),
            "seat_actionability_read": str(seat_row.get("seat_actionability_read") or ""),
            "seat_contract_gap_status": str(seat_row.get("seat_contract_gap_status") or ""),
            "seat_contract_gap_read": str(seat_row.get("seat_contract_gap_read") or ""),
            "seat_execution_gate_status": str(seat_row.get("seat_execution_gate_status") or ""),
            "seat_execution_gate_read": str(seat_row.get("seat_execution_gate_read") or ""),
        },
        "queue": {
            "priority": queue_row.get("priority"),
            "status": str(queue_row.get("status") or ""),
            "lane": str(queue_row.get("lane") or ""),
            "title": str(queue_row.get("title") or ""),
            "profit_mode": str(queue_row.get("profit_mode") or ""),
            "next_action_class": str(queue_row.get("next_action_class") or ""),
            "why": queue_why,
        },
        "overnight_runtime": first_path,
        "acceptance": acceptance_summary,
        "study": study_view,
        "shared_score": shared_view,
        "notes": [
            "This board is passive. It does not launch the GBP packet or promote the adaptive lane.",
            "Read it as the shortest honest answer to whether GBP is still packet-ready-only or has crossed into real incumbent-versus-adaptive proof.",
            "When runtime truth outruns stale queue/study/shared-score copy, this board prefers the freshest first-path evidence and rewrites the operator-facing GBP read accordingly.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    packet_contract = dict(payload.get("packet_contract") or {})
    seat = dict(payload.get("seat") or {})
    queue = dict(payload.get("queue") or {})
    overnight = dict(payload.get("overnight_runtime") or {})
    acceptance = dict(payload.get("acceptance") or {})
    study = dict(payload.get("study") or {})
    shared = dict(payload.get("shared_score") or {})

    lines = [
        "# GBPUSD Adaptive First-Path Board",
        "",
        "> Compact passive proof read for the dedicated GBP adaptive comparison packet against the incumbent live seat.",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- symbol: `{summary.get('symbol')}`",
            f"- packet_id: `{summary.get('packet_id')}`",
            f"- adaptive_lane: `{summary.get('adaptive_lane')}`",
            f"- proof_gate_status: `{summary.get('proof_gate_status')}`",
            f"- seat_actionability_status: `{summary.get('seat_actionability_status')}`",
            f"- seat_contract_gap_status: `{summary.get('seat_contract_gap_status')}`",
            f"- seat_execution_gate_status: `{summary.get('seat_execution_gate_status')}`",
            f"- queue_status: `{summary.get('queue_status')}`",
            f"- queue_priority: `{summary.get('queue_priority')}`",
            f"- overnight_action_status: `{summary.get('overnight_action_status')}`",
            f"- runtime_truth_source: `{summary.get('runtime_truth_source')}`",
            f"- runtime_truth_source_status: `{summary.get('runtime_truth_source_status')}`",
            f"- first_path_verdict: `{summary.get('first_path_verdict')}`",
            f"- study_status: `{summary.get('study_status')}`",
            f"- shared_score_verdict: `{summary.get('shared_score_verdict')}`",
            f"- shared_adaptive_basis: `{summary.get('shared_adaptive_basis')}`",
            f"- acceptance_verdict: `{summary.get('acceptance_verdict')}`",
            "",
            "## Packet Contract",
            "",
            f"- status: `{packet_contract.get('status')}`",
            f"- research_posture: `{packet_contract.get('research_posture')}`",
            f"- forward_gate: `{packet_contract.get('forward_gate')}`",
            f"- lane_name: `{packet_contract.get('lane_name')}`",
            f"- state_path: `{packet_contract.get('state_path')}`",
            f"- event_path: `{packet_contract.get('event_path')}`",
            f"- step: `{packet_contract.get('step')}`",
            f"- step_buy: `{packet_contract.get('step_buy')}`",
            f"- step_sell: `{packet_contract.get('step_sell')}`",
            f"- raw_close_alpha: `{packet_contract.get('raw_close_alpha')}`",
            f"- raw_rearm_variant: `{packet_contract.get('raw_rearm_variant')}`",
            f"- raw_sell_gap: `{packet_contract.get('raw_sell_gap')}`",
            f"- raw_buy_gap: `{packet_contract.get('raw_buy_gap')}`",
            f"- command: `{packet_contract.get('command')}`",
            "",
            "## Seat And Queue",
            "",
            f"- seat_verdict: `{seat.get('seat_verdict')}`",
            f"- incumbent_lane: `{seat.get('incumbent_lane')}`",
            f"- incumbent_booked_usd: `{seat.get('incumbent_booked_usd')}`",
            f"- seat_unblocker_action: `{seat.get('seat_unblocker_action')}`",
            f"- seat_unblocker_read: {seat.get('seat_unblocker_read')}",
            f"- seat_actionability_status: `{seat.get('seat_actionability_status')}`",
            f"- seat_actionability_read: {seat.get('seat_actionability_read')}",
            f"- seat_contract_gap_status: `{seat.get('seat_contract_gap_status')}`",
            f"- seat_contract_gap_read: {seat.get('seat_contract_gap_read')}",
            f"- seat_execution_gate_status: `{seat.get('seat_execution_gate_status')}`",
            f"- seat_execution_gate_read: {seat.get('seat_execution_gate_read')}",
            f"- queue_priority: `{queue.get('priority')}`",
            f"- queue_status: `{queue.get('status')}`",
            f"- queue_lane: `{queue.get('lane')}`",
            f"- queue_title: `{queue.get('title')}`",
            f"- queue_profit_mode: `{queue.get('profit_mode')}`",
            f"- queue_next_action_class: `{queue.get('next_action_class')}`",
            f"- queue_why: {queue.get('why')}",
            "",
            "## Runtime Proof",
            "",
            f"- source: `{overnight.get('source')}`",
            f"- source_status: `{overnight.get('source_status')}`",
            f"- checked_at: `{overnight.get('checked_at') or 'n/a'}`",
            f"- watcher_checked_at: `{overnight.get('watcher_checked_at') or 'n/a'}`",
            f"- watcher_age_seconds: `{overnight.get('watcher_age_seconds')}`",
            f"- watcher_max_age_seconds: `{overnight.get('watcher_max_age_seconds')}`",
            f"- action_status: `{overnight.get('action_status')}`",
            f"- execution_watchdog_status: `{overnight.get('execution_watchdog_status') or 'n/a'}`",
            f"- current_run_trade_opens: `{overnight.get('current_run_trade_opens')}`",
            f"- current_run_trade_closes: `{overnight.get('current_run_trade_closes')}`",
            f"- pre_start_trade_opens: `{overnight.get('pre_start_trade_opens')}`",
            f"- pre_start_trade_closes: `{overnight.get('pre_start_trade_closes')}`",
            f"- first_path_verdict: `{overnight.get('first_path_verdict')}`",
            f"- first_path_rationale: {overnight.get('first_path_rationale')}",
            f"- first_path_close_realized_pnl: `{overnight.get('first_path_close_realized_pnl')}`",
            f"- first_path_open_entry_context: `{overnight.get('first_path_open_entry_context') or 'n/a'}`",
            "",
            "## Study And Score",
            "",
            f"- acceptance_verdict: `{acceptance.get('verdict')}`",
            f"- acceptance_candidate_read: {acceptance.get('candidate_read')}",
            f"- acceptance_warning_checks: `{acceptance.get('warning_checks')}`",
            f"- study_status: `{study.get('study_status')}`",
            f"- adaptive_profit_mode: `{study.get('adaptive_profit_mode')}`",
            f"- adaptive_runtime_status: `{study.get('adaptive_runtime_status')}`",
            f"- adaptive_runtime_overlay_read: {study.get('adaptive_runtime_overlay_read') or '-'}",
            f"- study_why: {study.get('why')}",
            f"- shared_score_verdict: `{shared.get('comparison_verdict')}`",
            f"- shared_score_ready: `{shared.get('shared_score_ready')}`",
            f"- shared_score_gap: `{shared.get('score_gap')}`",
            f"- adaptive_basis: `{shared.get('adaptive_basis') or 'missing'}`",
            f"- adaptive_first_path_verdict: `{shared.get('adaptive_first_path_verdict')}`",
            f"- adaptive_score_unavailable_reason: `{shared.get('adaptive_score_unavailable_reason') or 'n/a'}`",
            f"- shared_score_why: {shared.get('why')}",
            "",
            "## Notes",
            "",
        ]
    )
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    payload = build_payload(
        load_json(PACKET_PATH),
        load_json(OVERNIGHT_PATH),
        load_json(WATCHER_STATE_PATH),
        load_json(QUEUE_PATH),
        load_json(SEAT_PATH),
        load_json(STUDY_PATH),
        load_json(SHARED_SCORE_PATH),
        load_json(ACCEPTANCE_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
