#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

SEAT_BOARD_PATH = REPORTS / "per_symbol_live_seat_board.json"
PROOF_BOARD_PATH = REPORTS / "adaptive_lattice_proof_board.json"
CONTROLLER_PRIORS_PATH = CONFIGS / "adaptive_controller_priors.json"
BRANCH_DECISION_PATH = REPORTS / "adaptive_btc_branch_decision_board.json"
BTC_RUNTIME_AUDIT_PATH = REPORTS / "btc_adaptive_runtime_audit.json"
BTC_ADAPTIVE_PLAN_PATH = REPORTS / "adaptive_btc_shadow_runner_plan.json"
BTC_RESTORE_BOARD_PATH = REPORTS / "btc_m15_warp_restore_board.json"
BOOKED_BREAKDOWN_PATH = REPORTS / "booked_pnl_breakdown_board.json"
PACKET_BOARD_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"

OUTPUT_JSON_PATH = REPORTS / "adaptive_incumbent_study_board.json"
OUTPUT_MD_PATH = REPORTS / "adaptive_incumbent_study_board.md"

DOCTRINE_FAMILIES = ("crypto", "fx", "index", "commodity")

SYMBOL_ASSET_CLASS = {
    "AUDUSD": "fx",
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",
    "EURUSD": "fx",
    "GBPUSD": "fx",
    "NAS100": "index",
    "NZDUSD": "fx",
    "US30": "index",
    "USDCAD": "fx",
    "USDJPY": "fx",
    "XAGUSD": "commodity",
    "XAUUSD": "commodity",
    "XRPUSD": "crypto",
}

VERDICT_RANK = {
    "promotion_ready": 4,
    "shadow_ready": 3,
    "bounded_proof_pending": 2,
    "probation": 1,
    "research_only": 0,
    "rejected": -1,
}

ACTIVE_RUNTIME_STATUSES = {
    "already_running_monitor_only",
    "forward_proof_started",
}

BLOCKED_RUNTIME_STATUSES = {
    "hold_launch_packet_defined_not_started",
    "hold_runtime_repair_candidate",
    "hold_disabled_proof_candidate",
}


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


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def first_float(values: list[Any]) -> float | None:
    for value in values:
        parsed = parse_float(value, default=float("nan"))
        if parsed == parsed:
            return parsed
    return None


def first_int(values: list[Any]) -> int | None:
    for value in values:
        parsed = parse_int(value, default=-10**9)
        if parsed != -10**9:
            return parsed
    return None


def find_shadow_row(booked_payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(dict(booked_payload.get("shadow_lattice") or {}).get("rows") or []):
        if str(row.get("lane") or "") == lane_name:
            return dict(row)
    return {}


def extract_count_from_notes(notes: str, pattern: str) -> int | None:
    match = re.search(pattern, notes or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def score_realized_profit(realized_usd: float | None) -> int:
    if realized_usd is None:
        return 0
    if realized_usd >= 25:
        return 3
    if realized_usd > 0:
        return 2
    if realized_usd == 0:
        return 0
    if realized_usd <= -25:
        return -3
    return -2


def score_cash_velocity(realized_usd: float | None, close_count: int | None) -> int:
    if realized_usd is None or close_count is None or close_count <= 0:
        return 0
    usd_per_close = realized_usd / close_count
    if usd_per_close >= 10:
        return 3
    if usd_per_close >= 2:
        return 2
    if usd_per_close > 0:
        return 1
    if usd_per_close == 0:
        return 0
    if usd_per_close <= -10:
        return -3
    return -1


def score_carry(carry_usd: float | None) -> int:
    if carry_usd is None:
        return 0
    if carry_usd < 0:
        return -1
    if carry_usd > 0:
        return 1
    return 0


def build_btc_profit_comparison(
    *,
    branch_payload: dict[str, Any],
    runtime_audit: dict[str, Any],
    adaptive_plan: dict[str, Any],
    restore_board: dict[str, Any],
    booked_breakdown: dict[str, Any],
) -> dict[str, Any]:
    branch_rows = {
        str(row.get("branch_id") or ""): dict(row)
        for row in list(branch_payload.get("rows") or [])
        if isinstance(row, dict)
    }
    restore_branch = branch_rows.get("launch_restore_comparison_shadow", {})
    restore_candidate = dict(restore_board.get("restore_candidate") or {})
    restore_lane = str(restore_candidate.get("lane") or restore_branch.get("allowed_inputs", ["", ""])[0] or "")
    restore_row = find_shadow_row(booked_breakdown, restore_lane)
    restore_notes = str(restore_row.get("notes") or "")
    restore_basis = "missing"
    restore_realized = None
    restore_close_count = None
    if parse_float(restore_row.get("runner_session_booked_usd")) not in {None, 0.0}:
        restore_basis = "runner_session_booked_usd"
        restore_realized = parse_float(restore_row.get("runner_session_booked_usd"))
    elif parse_float(restore_row.get("clean_forward_delta_usd")) is not None:
        restore_basis = "clean_forward_delta_usd"
        restore_realized = parse_float(restore_row.get("clean_forward_delta_usd"))
        restore_close_count = extract_count_from_notes(restore_notes, r"clean_forward_since_repair=[^/]+/(\d+)c")
    elif parse_float(restore_row.get("booked_usd")) is not None:
        restore_basis = "booked_usd_proxy"
        restore_realized = parse_float(restore_row.get("booked_usd"))
        restore_close_count = parse_int(restore_row.get("close_count"))
    restore_score = (
        score_realized_profit(restore_realized)
        + score_cash_velocity(restore_realized, restore_close_count)
        + score_carry(None)
    )

    runtime_lane = dict(runtime_audit.get("runtime_lane") or {})
    runtime_objective = dict(runtime_audit.get("runtime_objective_context") or {})
    adaptive_shape_id = str(dict(adaptive_plan.get("controller_recommendation") or {}).get("recommended_shape_id") or "")
    adaptive_realized = parse_float(runtime_lane.get("runner_session_trade_realized_usd"))
    adaptive_close_count = parse_int(runtime_lane.get("runner_session_trade_closes"))
    adaptive_carry = parse_float(runtime_lane.get("pre_start_state_carry_realized_usd"))
    adaptive_score = (
        score_realized_profit(adaptive_realized)
        + score_cash_velocity(adaptive_realized, adaptive_close_count)
        + score_carry(adaptive_carry)
    )

    if (adaptive_close_count or 0) <= 0:
        verdict = "adaptive_candidate_defined_but_unproven"
    elif adaptive_score > restore_score:
        verdict = "adaptive_candidate_preliminarily_leading"
    elif adaptive_score < restore_score:
        verdict = "restore_control_still_leading"
    else:
        verdict = "too_close_to_call"

    read = (
        f"Restore control `{restore_lane}` currently scores from `{restore_basis}`="
        f"{restore_realized if restore_realized is not None else 'n/a'} over "
        f"{restore_close_count if restore_close_count is not None else 'n/a'} closes, while adaptive candidate "
        f"`{adaptive_shape_id or 'unknown'}` scores from runner-session realized="
        f"{adaptive_realized if adaptive_realized is not None else 'n/a'} over "
        f"{adaptive_close_count if adaptive_close_count is not None else 'n/a'} closes with carry "
        f"{adaptive_carry if adaptive_carry is not None else 'n/a'}. "
        + (
            f"Current selector objective: {runtime_objective.get('objective_read')}"
            if runtime_objective.get("objective_read")
            else ""
        )
    ).strip()

    return {
        "restore_lane": restore_lane,
        "restore_launch_status": str(restore_branch.get("launch_status") or ""),
        "restore_basis": restore_basis,
        "restore_realized_usd": restore_realized,
        "restore_close_count": restore_close_count,
        "restore_score": restore_score,
        "adaptive_shape_id": adaptive_shape_id,
        "adaptive_close_conversion_pressure": bool(runtime_objective.get("close_conversion_pressure")),
        "adaptive_objective_read": str(runtime_objective.get("objective_read") or ""),
        "adaptive_runner_session_realized_usd": adaptive_realized,
        "adaptive_runner_session_close_count": adaptive_close_count,
        "adaptive_pre_start_carry_realized_usd": adaptive_carry,
        "adaptive_score": adaptive_score,
        "score_gap": adaptive_score - restore_score,
        "verdict": verdict,
        "read": read,
    }


def mode_summary(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        row["symbol"]: str(row.get("adaptive_profit_mode") or "")
        for row in rows
        if str(row.get("adaptive_profit_mode") or "").strip()
    }


def infer_asset_class(symbol: str) -> str:
    return SYMBOL_ASSET_CLASS.get(str(symbol or "").upper(), "unknown")


def best_acceptance_by_symbol(acceptance_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for candidate in list(acceptance_payload.get("candidates") or []):
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol") or "").upper()
        if not symbol:
            continue
        verdict = str(candidate.get("verdict") or "")
        priority = parse_int(candidate.get("priority"), 9999)
        incumbent = mapped.get(symbol)
        challenger = {
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "verdict": verdict,
            "queue_status": str(candidate.get("queue_status") or ""),
            "candidate_read": str(candidate.get("candidate_read") or ""),
            "priority": priority,
            "runtime_status": str(dict(candidate.get("machine_truth") or {}).get("recommended_branch_launch_status") or ""),
            "supporting_evidence": list(candidate.get("supporting_evidence") or []),
        }
        if not incumbent:
            mapped[symbol] = challenger
            continue
        incumbent_key = (VERDICT_RANK.get(str(incumbent.get("verdict") or ""), -99), -parse_int(incumbent.get("priority"), 9999))
        challenger_key = (VERDICT_RANK.get(verdict, -99), -priority)
        if challenger_key > incumbent_key:
            mapped[symbol] = challenger
    return mapped


def packet_row_by_symbol(packet_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    symbol_tokens = tuple(SYMBOL_ASSET_CLASS.keys())
    for row in list(packet_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane_name = str(row.get("lane_name") or "").upper()
        symbol = next((candidate for candidate in symbol_tokens if candidate in lane_name), "")
        if not symbol:
            continue
        existing = mapped.get(symbol)
        current = dict(row)
        current_running = str(current.get("action_status") or "") == "already_running_monitor_only"
        existing_running = str((existing or {}).get("action_status") or "") == "already_running_monitor_only"
        if existing is None or (current_running and not existing_running):
            mapped[symbol] = current
    return mapped


def study_status(
    *,
    incumbent_present: bool,
    adaptive_present: bool,
    adaptive_verdict: str,
    adaptive_stage: str,
    adaptive_runtime_status: str,
    prior_present: bool,
) -> tuple[str, str, bool]:
    effective_verdict = adaptive_verdict or adaptive_stage
    runtime_status = str(adaptive_runtime_status or "")
    if incumbent_present and adaptive_present:
        if effective_verdict in {"promotion_ready", "shadow_ready"}:
            if runtime_status in ACTIVE_RUNTIME_STATUSES:
                return (
                    "study_ready",
                    "Current incumbent and adaptive challenger are both explicit enough to score on one shared study surface.",
                    True,
                )
            if runtime_status in BLOCKED_RUNTIME_STATUSES:
                return (
                    "blocked_runtime_or_launch_gap",
                    "A credible adaptive challenger exists, but the comparison is still blocked by launch/runtime status rather than shared-score logic.",
                    False,
                )
            return (
                "adaptive_shape_defined_packet_missing",
                "The symbol has an adaptive shape and some readiness, but it still lacks an executable adaptive comparison packet on current surfaces.",
                False,
            )
        return (
            "research_only_adaptive_candidate",
            "The symbol has adaptive research coverage, but the candidate is not yet mature enough for an honest incumbent-versus-adaptive study.",
            False,
        )
    if adaptive_present:
        return (
            "adaptive_candidate_without_incumbent",
            "The symbol has adaptive coverage, but the repo does not yet expose a current incumbent live-seat comparison surface for it.",
            False,
        )
    if incumbent_present:
        return (
            "incumbent_without_adaptive_candidate",
            "The symbol has an incumbent reference, but no adaptive study candidate is yet defined on the current adaptive proof surface.",
            False,
        )
    if prior_present:
        return (
            "prior_only_family_gap",
            "The symbol is present in controller priors, but not yet in the adaptive proof stack or incumbent seat comparison surface.",
            False,
        )
    return (
        "coverage_gap",
        "Neither incumbent seat truth nor adaptive proof truth exists yet for this symbol on the current authority stack.",
        False,
    )


def study_bucket(status: str) -> str:
    if status == "study_ready":
        return "ready"
    if status in {"blocked_runtime_or_launch_gap", "adaptive_shape_defined_packet_missing"}:
        return "blocked"
    if status in {"research_only_adaptive_candidate", "adaptive_candidate_without_incumbent", "incumbent_without_adaptive_candidate"}:
        return "not_ready"
    if status == "prior_only_family_gap":
        return "prior_only"
    return "coverage_gap"


def family_coverage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    for family in DOCTRINE_FAMILIES:
        family_rows = [row for row in rows if row["asset_class"] == family]
        if not family_rows:
            coverage.append(
                {
                    "family": family,
                    "verdict": "missing",
                    "study_ready_symbols": [],
                    "symbols": [],
                    "read": "No current adaptive/incumbent study rows exist for this doctrine family.",
                }
            )
            continue
        ready_symbols = [row["symbol"] for row in family_rows if row["study_bucket"] == "ready"]
        blocked_symbols = [row["symbol"] for row in family_rows if row["study_bucket"] == "blocked"]
        prior_only_symbols = [row["symbol"] for row in family_rows if row["study_bucket"] == "prior_only"]
        if ready_symbols:
            verdict = "ready_candidate_present"
            read = f"Family has at least one study-ready symbol: `{ready_symbols}`."
        elif blocked_symbols:
            verdict = "blocked_candidate_present"
            read = f"Family has candidate coverage, but current study comparison is blocked on packet/runtime gaps for `{blocked_symbols}`."
        elif prior_only_symbols and len(prior_only_symbols) == len(family_rows):
            verdict = "prior_only"
            read = f"Family is represented only by controller-prior truth: `{prior_only_symbols}`."
        else:
            verdict = "partial_candidate_coverage"
            read = "Family has some adaptive/incumbent coverage, but not yet a study-ready challenger."
        coverage.append(
            {
                "family": family,
                "verdict": verdict,
                "study_ready_symbols": ready_symbols,
                "symbols": [row["symbol"] for row in family_rows],
                "read": read,
            }
        )
    return coverage


def build_payload(
    *,
    seat_board: dict[str, Any],
    proof_board: dict[str, Any],
    controller_priors: dict[str, Any],
    acceptance_board: dict[str, Any] | None = None,
    perfection_scorecard: dict[str, Any] | None = None,
    branch_decision: dict[str, Any],
    btc_runtime_audit: dict[str, Any],
    btc_adaptive_plan: dict[str, Any],
    btc_restore_board: dict[str, Any],
    booked_breakdown: dict[str, Any],
    packet_board: dict[str, Any],
) -> dict[str, Any]:
    seat_rows = {
        str(row.get("symbol") or "").upper(): dict(row)
        for row in list(seat_board.get("rows") or [])
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    }
    proof_rows = {
        str(row.get("symbol") or "").upper(): dict(row)
        for row in list(proof_board.get("rows") or [])
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    }
    acceptance_rows = best_acceptance_by_symbol(acceptance_board or {}) if acceptance_board else {}
    packet_rows = packet_row_by_symbol(packet_board)
    prior_rows = {
        str(symbol).upper(): dict(payload)
        for symbol, payload in dict(controller_priors.get("symbol_priors") or {}).items()
    }
    btc_profit_comparison = build_btc_profit_comparison(
        branch_payload=branch_decision,
        runtime_audit=btc_runtime_audit,
        adaptive_plan=btc_adaptive_plan,
        restore_board=btc_restore_board,
        booked_breakdown=booked_breakdown,
    )

    symbols = sorted(unique_strings(list(proof_rows.keys()) + list(prior_rows.keys())))
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        seat_row = dict(seat_rows.get(symbol) or {})
        proof_row = dict(proof_rows.get(symbol) or {})
        acceptance_row = dict(acceptance_rows.get(symbol) or {})
        packet_row = dict(packet_rows.get(symbol) or {})
        prior_row = dict(prior_rows.get(symbol) or {})

        incumbent_present = bool(str(seat_row.get("current_live_holder_lane") or "").strip())
        incumbent_lane = str(seat_row.get("current_live_holder_lane") or "")
        incumbent_basis = str(seat_row.get("current_live_holder_evidence_basis") or "")
        incumbent_booked_usd = parse_float(seat_row.get("current_live_holder_booked_usd"))
        incumbent_close_count = parse_int(seat_row.get("current_live_holder_close_count"))
        incumbent_operator_posture = str(seat_row.get("current_live_holder_operator_posture") or "")
        incumbent_seat_verdict = str(seat_row.get("seat_verdict") or "")

        adaptive_present = bool(proof_row)
        adaptive_stage = str(proof_row.get("stage") or "")
        adaptive_shape_id = str(proof_row.get("recommended_shape_id") or "")
        adaptive_family = str(proof_row.get("family") or "")
        adaptive_profit_mode = str(proof_row.get("profit_mode") or "")
        adaptive_profit_mode_read = str(proof_row.get("profit_mode_read") or "")
        adaptive_runtime_overlays = list(proof_row.get("runtime_overlays") or [])
        adaptive_runtime_overlay_read = str(proof_row.get("runtime_overlay_read") or "")
        adaptive_objective_read = str(proof_row.get("objective_read") or "")
        adaptive_verdict = str(acceptance_row.get("verdict") or "")
        adaptive_candidate_id = str(acceptance_row.get("candidate_id") or "")
        adaptive_queue_status = str(acceptance_row.get("queue_status") or "")
        adaptive_runtime_status = str(acceptance_row.get("runtime_status") or "")
        if not adaptive_runtime_status and str(seat_row.get("best_challenger_family") or "") == "adaptive_shadow":
            adaptive_runtime_status = str(seat_row.get("best_challenger_runtime_status") or "")
        if not adaptive_runtime_status:
            adaptive_runtime_status = str(packet_row.get("action_status") or "")
        adaptive_lane = ""
        if str(seat_row.get("best_challenger_family") or "") == "adaptive_shadow":
            adaptive_lane = str(seat_row.get("best_challenger_lane") or "")
        if not adaptive_lane:
            adaptive_lane = str(packet_row.get("lane_name") or "")

        status, read, ready = study_status(
            incumbent_present=incumbent_present,
            adaptive_present=adaptive_present,
            adaptive_verdict=adaptive_verdict,
            adaptive_stage=adaptive_stage,
            adaptive_runtime_status=adaptive_runtime_status,
            prior_present=bool(prior_row),
        )

        row = {
            "symbol": symbol,
            "asset_class": infer_asset_class(symbol),
            "incumbent_present": incumbent_present,
            "incumbent_seat_verdict": incumbent_seat_verdict,
            "incumbent_lane": incumbent_lane,
            "incumbent_evidence_basis": incumbent_basis,
            "incumbent_booked_usd": incumbent_booked_usd,
            "incumbent_close_count": incumbent_close_count,
            "incumbent_operator_posture": incumbent_operator_posture,
            "adaptive_present": adaptive_present,
            "adaptive_stage": adaptive_stage,
            "adaptive_shape_id": adaptive_shape_id,
            "adaptive_family": adaptive_family,
            "adaptive_profit_mode": adaptive_profit_mode,
            "adaptive_profit_mode_read": adaptive_profit_mode_read,
            "adaptive_runtime_overlays": adaptive_runtime_overlays,
            "adaptive_runtime_overlay_read": adaptive_runtime_overlay_read,
            "adaptive_objective_read": adaptive_objective_read,
            "adaptive_candidate_verdict": adaptive_verdict or adaptive_stage,
            "adaptive_candidate_id": adaptive_candidate_id,
            "adaptive_queue_status": adaptive_queue_status,
            "adaptive_runtime_status": adaptive_runtime_status,
            "adaptive_lane": adaptive_lane,
            "prior_present": bool(prior_row),
            "prior_role": str(prior_row.get("controller_role") or ""),
            "prior_promotion_action": str(prior_row.get("promotion_action") or ""),
            "study_status": status,
            "study_bucket": study_bucket(status),
            "study_ready": ready,
            "why": read,
            "incumbent_read": str(seat_row.get("why") or ""),
            "adaptive_read": str(acceptance_row.get("candidate_read") or proof_row.get("why") or ""),
            "prior_read": str(prior_row.get("controller_read") or ""),
            "machine_truth": {
                "seat_verdict": incumbent_seat_verdict,
                "adaptive_stage": adaptive_stage,
                "adaptive_verdict": adaptive_verdict or adaptive_stage,
                "adaptive_runtime_status": adaptive_runtime_status,
                "prior_role": str(prior_row.get("controller_role") or ""),
            },
        }
        if symbol == "BTCUSD":
            row["btc_max_profit_comparison"] = btc_profit_comparison
        rows.append(row)

    family_coverage = family_coverage_rows(rows)
    profit_modes = mode_summary(rows)
    summary = {
        "symbol_count": len(rows),
        "comparable_symbols": [row["symbol"] for row in rows if row["incumbent_present"] and row["adaptive_present"]],
        "study_ready_symbols": [row["symbol"] for row in rows if row["study_bucket"] == "ready"],
        "blocked_symbols": [row["symbol"] for row in rows if row["study_bucket"] == "blocked"],
        "research_only_symbols": [row["symbol"] for row in rows if row["study_status"] == "research_only_adaptive_candidate"],
        "adaptive_without_incumbent_symbols": [row["symbol"] for row in rows if row["study_status"] == "adaptive_candidate_without_incumbent"],
        "prior_only_symbols": [row["symbol"] for row in rows if row["study_bucket"] == "prior_only"],
        "coverage_gap_symbols": [row["symbol"] for row in rows if row["study_bucket"] == "coverage_gap"],
        "family_coverage": {row["family"]: row["verdict"] for row in family_coverage},
        "adaptive_profit_modes": profit_modes,
        "btc_max_profit_contract": {
            "verdict": str(btc_profit_comparison.get("verdict") or ""),
            "restore_lane": str(btc_profit_comparison.get("restore_lane") or ""),
            "adaptive_shape_id": str(btc_profit_comparison.get("adaptive_shape_id") or ""),
            "score_gap": btc_profit_comparison.get("score_gap"),
        },
        "adaptive_program_score": {
            "total_score": parse_int(dict((perfection_scorecard or {}).get("summary") or {}).get("total_score")),
            "max_score": parse_int(dict((perfection_scorecard or {}).get("summary") or {}).get("max_score")),
            "overall_verdict": str(dict((perfection_scorecard or {}).get("summary") or {}).get("overall_verdict") or ""),
        },
    }
    leadership_read = [
        (
            f"Current comparable incumbent-versus-adaptive symbols are `{summary['comparable_symbols']}`, "
            f"but study-ready rows are only `{summary['study_ready_symbols']}`."
        ),
        (
            f"Blocked comparison rows are `{summary['blocked_symbols']}`, while symbols that still only have research-grade adaptive coverage are `{summary['research_only_symbols']}`."
        ),
        (
            f"Adaptive candidates without a current incumbent seat are `{summary['adaptive_without_incumbent_symbols']}`, "
            f"and prior-only family rows are `{summary['prior_only_symbols']}`."
        ),
        (
            f"Current adaptive profit modes by symbol are `{summary['adaptive_profit_modes']}`. "
            "Read them as the controller's intended extraction posture, not as proof that the symbol has already won its seat."
        ),
        (
            f"Doctrine-family coverage is `{summary['family_coverage']}`. "
            "Read this board as a study-readiness scaffold, not as proof that adaptive has already displaced incumbents."
        ),
        (
            f"BTC now has an explicit max-profit comparison contract: restore control `{summary['btc_max_profit_contract']['restore_lane']}` "
            f"versus adaptive candidate `{summary['btc_max_profit_contract']['adaptive_shape_id']}`, currently reading "
            f"`{summary['btc_max_profit_contract']['verdict']}` with score gap `{summary['btc_max_profit_contract']['score_gap']}`."
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SEAT_BOARD_PATH.relative_to(ROOT)),
            str(PROOF_BOARD_PATH.relative_to(ROOT)),
            str(CONTROLLER_PRIORS_PATH.relative_to(ROOT)),
            str(BRANCH_DECISION_PATH.relative_to(ROOT)),
            str(BTC_RUNTIME_AUDIT_PATH.relative_to(ROOT)),
            str(BTC_ADAPTIVE_PLAN_PATH.relative_to(ROOT)),
            str(BTC_RESTORE_BOARD_PATH.relative_to(ROOT)),
            str(BOOKED_BREAKDOWN_PATH.relative_to(ROOT)),
            str(PACKET_BOARD_PATH.relative_to(ROOT)),
        ],
        "summary": summary,
        "leadership_read": leadership_read,
        "family_coverage": family_coverage,
        "rows": rows,
        "notes": [
            "This board is passive. It does not settle the shared score or auto-select a winner; it only says whether the repo currently exposes enough incumbent-versus-adaptive structure to run the missing study honestly.",
            "Incumbent truth comes from `per_symbol_live_seat_board`; adaptive challenger truth comes from the adaptive proof plus acceptance surfaces; controller priors fill family-coverage gaps where no adaptive row exists yet.",
            "A `study_ready` row means the symbol has both an incumbent reference and an executable-enough adaptive challenger. It does not mean the challenger has already won.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    score = dict(summary.get("adaptive_program_score") or {})
    lines = [
        "# Adaptive Incumbent Study Board",
        "",
        "This board is the passive scaffold for the missing cross-family incumbent-versus-adaptive study.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- Adaptive program score: `{score.get('total_score', 0)}/{score.get('max_score', 0)}` -> `{score.get('overall_verdict', '')}`",
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
            f"- comparable_symbols: `{summary.get('comparable_symbols', [])}`",
            f"- study_ready_symbols: `{summary.get('study_ready_symbols', [])}`",
            f"- blocked_symbols: `{summary.get('blocked_symbols', [])}`",
            f"- research_only_symbols: `{summary.get('research_only_symbols', [])}`",
            f"- adaptive_without_incumbent_symbols: `{summary.get('adaptive_without_incumbent_symbols', [])}`",
            f"- prior_only_symbols: `{summary.get('prior_only_symbols', [])}`",
            f"- adaptive_profit_modes: `{summary.get('adaptive_profit_modes', {})}`",
            f"- family_coverage: `{summary.get('family_coverage', {})}`",
            f"- btc_max_profit_contract: `{summary.get('btc_max_profit_contract', {})}`",
            "",
            "## Doctrine Family Coverage",
            "",
            "| Family | Verdict | Symbols | Read |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("family_coverage") or []):
        lines.append(
            f"| `{row['family']}` | `{row['verdict']}` | `{row['symbols']}` | {row['read']} |"
        )

    lines.extend(
        [
            "",
            "## Comparison Table",
            "",
            "| Symbol | Asset | Incumbent | Adaptive shape | Profit mode | Verdict | Runtime | Study status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['symbol']}` | `{row['asset_class']}` | `{row['incumbent_lane'] or '-'}` | "
            f"`{row['adaptive_shape_id'] or '-'}` | `{row['adaptive_profit_mode'] or '-'}` | `{row['adaptive_candidate_verdict'] or '-'}` | "
            f"`{row['adaptive_runtime_status'] or '-'}` | `{row['study_status']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- asset_class: `{row['asset_class']}`",
                f"- study_status: `{row['study_status']}`",
                f"- study_ready: `{row['study_ready']}`",
                f"- incumbent_lane: `{row['incumbent_lane'] or ''}`",
                f"- incumbent_basis: `{row['incumbent_evidence_basis'] or ''}`",
                f"- incumbent_booked_usd: `{parse_float(row.get('incumbent_booked_usd')):+.2f}`",
                f"- adaptive_shape_id: `{row['adaptive_shape_id'] or ''}`",
                f"- adaptive_profit_mode: `{row['adaptive_profit_mode'] or ''}`",
                f"- adaptive_runtime_overlays: `{row.get('adaptive_runtime_overlays') or []}`",
                f"- adaptive_candidate_verdict: `{row['adaptive_candidate_verdict'] or ''}`",
                f"- adaptive_runtime_status: `{row['adaptive_runtime_status'] or ''}`",
                f"- prior_role: `{row['prior_role'] or ''}`",
                f"- why: {row['why']}",
            ]
        )
        if row.get("incumbent_read"):
            lines.append(f"- incumbent_read: {row['incumbent_read']}")
        if row.get("adaptive_read"):
            lines.append(f"- adaptive_read: {row['adaptive_read']}")
        if row.get("adaptive_profit_mode_read"):
            lines.append(f"- adaptive_profit_mode_read: {row['adaptive_profit_mode_read']}")
        if row.get("adaptive_runtime_overlay_read"):
            lines.append(f"- adaptive_runtime_overlay_read: {row['adaptive_runtime_overlay_read']}")
        if row.get("adaptive_objective_read"):
            lines.append(f"- adaptive_objective_read: {row['adaptive_objective_read']}")
        if row.get("btc_max_profit_comparison"):
            btc = dict(row["btc_max_profit_comparison"])
            lines.append(f"- btc_max_profit_verdict: `{btc.get('verdict', '')}`")
            lines.append(
                f"- btc_max_profit_scores: restore=`{btc.get('restore_score')}` adaptive=`{btc.get('adaptive_score')}` gap=`{btc.get('score_gap')}`"
            )
            lines.append(
                f"- btc_max_profit_contract: restore=`{btc.get('restore_lane', '')}` via `{btc.get('restore_basis', '')}` vs adaptive=`{btc.get('adaptive_shape_id', '')}`"
            )
            lines.append(f"- btc_max_profit_read: {btc.get('read', '')}")
        if row.get("prior_read"):
            lines.append(f"- prior_read: {row['prior_read']}")
        lines.append("")

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        seat_board=load_json(SEAT_BOARD_PATH),
        proof_board=load_json(PROOF_BOARD_PATH),
        controller_priors=load_json(CONTROLLER_PRIORS_PATH),
        branch_decision=load_json(BRANCH_DECISION_PATH),
        btc_runtime_audit=load_json(BTC_RUNTIME_AUDIT_PATH),
        btc_adaptive_plan=load_json(BTC_ADAPTIVE_PLAN_PATH),
        btc_restore_board=load_json(BTC_RESTORE_BOARD_PATH),
        booked_breakdown=load_json(BOOKED_BREAKDOWN_PATH),
        packet_board=load_json(PACKET_BOARD_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
