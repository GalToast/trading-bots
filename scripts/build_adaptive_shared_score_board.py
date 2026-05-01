#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from unified_objective import UnifiedObjective, ObjectiveInput
except ImportError:
    from scripts.unified_objective import UnifiedObjective, ObjectiveInput


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

INCUMBENT_STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
OVERNIGHT_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"
BOOKED_PATH = REPORTS / "booked_pnl_breakdown_board.json"
ORGANISM_PATH = REPORTS / "organism_state.json"

OUTPUT_JSON_PATH = REPORTS / "adaptive_shared_score_board.json"
OUTPUT_MD_PATH = REPORTS / "adaptive_shared_score_board.md"

CURRENT_RUN_BASES = {"runner_session_booked_usd", "first_path_close_realized_pnl"}
PROXY_BASES = {"clean_forward_delta_usd", "booked_usd_proxy"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_float(value: Any) -> float | None:
    try:
        if value in {"", None}:
            return None
        return float(value)
    except Exception:
        return None


def parse_int(value: Any) -> int | None:
    try:
        if value in {"", None}:
            return None
        return int(value)
    except Exception:
        return None


def as_text(value: Any) -> str:
    return str(value or "").strip()


def live_lane_map(organism_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in list(organism_payload.get("live_lanes") or []):
        if not isinstance(row, dict):
            continue
        lane = as_text(row.get("lane"))
        if lane:
            mapped[lane] = dict(row)
    return mapped


def shadow_lane_map(booked_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    shadow_rows = dict(booked_payload.get("shadow_lattice") or {}).get("rows") or []
    for row in shadow_rows:
        if not isinstance(row, dict):
            continue
        lane = as_text(row.get("lane"))
        if lane:
            mapped[lane] = dict(row)
    return mapped


def overnight_lane_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane = as_text(row.get("lane_name"))
        if lane:
            mapped[lane] = dict(row)
    return mapped


def profit_component(realized_usd: float | None) -> int:
    if realized_usd is None:
        return 0
    if realized_usd >= 200:
        return 4
    if realized_usd >= 50:
        return 3
    if realized_usd > 0:
        return 2
    if realized_usd == 0:
        return 0
    if realized_usd <= -200:
        return -4
    if realized_usd <= -50:
        return -3
    return -2


def velocity_component(usd_per_close: float | None) -> int:
    if usd_per_close is None:
        return 0
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
    if usd_per_close <= -2:
        return -2
    return -1


def conversion_component(realized_usd: float | None, floating_usd: float | None) -> int:
    if realized_usd is None or floating_usd is None:
        return 0
    denominator = abs(realized_usd) + abs(floating_usd)
    if denominator == 0:
        return 0
    ratio = realized_usd / denominator
    if ratio >= 0.9:
        return 2
    if ratio >= 0.6:
        return 1
    if ratio >= 0.3:
        return 0
    if ratio > 0:
        return -1
    return -2


def incumbent_validity_component(evidence_basis: str, seat_verdict: str) -> int:
    if evidence_basis == "graduated_live_reference":
        return 2
    if seat_verdict.startswith("defended"):
        return 1
    if evidence_basis == "fresh_forward_live":
        return 1
    if evidence_basis in {"carry_weighted_live", "inherited_history_live"}:
        return 0
    return 0


def adaptive_readiness_component(verdict: str, runtime_status: str, basis: str) -> int:
    score = 0
    if verdict == "promotion_ready":
        score += 2
    elif verdict == "shadow_ready":
        score += 1
    elif verdict in {"research_only", "probation"}:
        score -= 1
    elif verdict:
        score -= 1

    if runtime_status == "already_running_monitor_only":
        score += 1
    elif runtime_status in {"hold_runtime_repair_candidate", "hold_disabled_proof_candidate"}:
        score -= 2

    if basis in CURRENT_RUN_BASES:
        score += 1
    elif basis in PROXY_BASES:
        score += 0
    elif basis == "missing":
        score -= 1
    return score


def carry_penalty_from_notes(notes: str) -> int:
    text = notes.lower()
    penalty = 0
    if "pre_start_state_carry" in text:
        penalty -= 1
    if "broker_sync_inherited" in text or "inherited" in text:
        penalty -= 1
    return penalty


def extract_incumbent_metrics(study_row: dict[str, Any], live_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lane = as_text(study_row.get("incumbent_lane"))
    live_row = dict(live_map.get(lane) or {})
    realized = parse_float(live_row.get("realized_usd"))
    if realized is None:
        realized = parse_float(study_row.get("incumbent_booked_usd"))
    close_count = parse_int(live_row.get("closes"))
    if close_count is None:
        close_count = parse_int(study_row.get("incumbent_close_count"))
    floating = parse_float(live_row.get("floating_usd"))
    notes = as_text(live_row.get("notes"))
    basis = "exact_live_realized" if live_row else "seat_board_booked_proxy"
    usd_per_close = None
    if realized is not None and close_count and close_count > 0:
        usd_per_close = realized / close_count
    components = {
        "profit": profit_component(realized),
        "velocity": velocity_component(usd_per_close),
        "conversion": conversion_component(realized, floating),
        "validity": incumbent_validity_component(
            as_text(study_row.get("incumbent_evidence_basis")),
            as_text(study_row.get("incumbent_seat_verdict")),
        ),
        "carry": carry_penalty_from_notes(notes),
    }
    total = sum(int(value) for value in components.values())

    # Unified objective function (Gap 2): evaluate alongside piecewise score
    unified_result = None
    if realized is not None and close_count and close_count > 0:
        unified_result = UnifiedObjective.evaluate(ObjectiveInput(
            realized_net_usd=realized,
            close_count=close_count,
            floating_usd=floating if floating is not None else 0.0,
            open_count=0,  # Not available in incumbent study
            anchor_reset_count=0,  # Not available in incumbent study
            max_adverse_excursion_usd=0.0,
            first_path_verdict="",
            realized_win_rate=0.0,  # Not available in incumbent study
        ))

    return {
        "lane": lane,
        "basis": basis,
        "realized_usd": realized,
        "close_count": close_count,
        "usd_per_close": usd_per_close,
        "floating_usd": floating,
        "notes": notes,
        "components": components,
        "score_total": total,
        "unified_objective_score": round(unified_result.total, 3) if unified_result else None,
        "unified_objective_verdict": unified_result.verdict if unified_result else None,
    }


def extract_adaptive_metrics(
    study_row: dict[str, Any],
    shadow_map: dict[str, dict[str, Any]],
    overnight_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lane = as_text(study_row.get("adaptive_lane"))
    shadow_row = dict(shadow_map.get(lane) or {})
    overnight_row = dict(overnight_map.get(lane) or {})

    basis = "missing"
    realized = None
    close_count = None
    notes = as_text(shadow_row.get("notes"))

    runner_session_booked = parse_float(shadow_row.get("runner_session_booked_usd"))
    first_path_realized = parse_float(overnight_row.get("first_path_close_realized_pnl"))
    clean_forward = parse_float(shadow_row.get("clean_forward_delta_usd"))
    booked_proxy = parse_float(shadow_row.get("booked_usd"))

    if runner_session_booked is not None and runner_session_booked != 0:
        basis = "runner_session_booked_usd"
        realized = runner_session_booked
    elif first_path_realized is not None:
        basis = "first_path_close_realized_pnl"
        realized = first_path_realized
    elif clean_forward is not None and clean_forward != 0:
        basis = "clean_forward_delta_usd"
        realized = clean_forward
    elif booked_proxy is not None:
        basis = "booked_usd_proxy"
        realized = booked_proxy

    artifact_trade_closes = parse_int(overnight_row.get("artifact_trade_closes"))
    shadow_total_closes = parse_int(shadow_row.get("close_count"))
    if basis == "first_path_close_realized_pnl":
        close_count = 1
    elif basis == "runner_session_booked_usd" and artifact_trade_closes is not None and artifact_trade_closes > 0:
        close_count = artifact_trade_closes
    elif basis == "booked_usd_proxy" and shadow_total_closes and shadow_total_closes > 0:
        close_count = shadow_total_closes

    usd_per_close = None
    if realized is not None and close_count and close_count > 0:
        usd_per_close = realized / close_count

    first_path_verdict = as_text(overnight_row.get("first_path_verdict"))
    if not lane or basis == "missing":
        return {
            "lane": lane,
            "basis": "missing",
            "realized_usd": None,
            "close_count": None,
            "usd_per_close": None,
            "floating_usd": None,
            "notes": notes,
            "first_path_verdict": first_path_verdict,
            "components": {},
            "score_total": None,
            "score_unavailable_reason": "adaptive_profit_basis_missing",
        }

    toxicity_penalty = -2 if first_path_verdict == "never_green_toxic_continuation" else 0
    carry_penalty = -1 if basis == "booked_usd_proxy" else 0

    components = {
        "profit": profit_component(realized),
        "velocity": velocity_component(usd_per_close),
        "conversion": 0,
        "readiness": adaptive_readiness_component(
            as_text(study_row.get("adaptive_candidate_verdict")),
            as_text(study_row.get("adaptive_runtime_status")),
            basis,
        ),
        "carry": carry_penalty,
        "toxicity": toxicity_penalty,
    }
    total = sum(int(value) for value in components.values())

    # Unified objective function (Gap 2): evaluate alongside piecewise score
    unified_result = None
    if realized is not None and close_count and close_count > 0:
        unified_result = UnifiedObjective.evaluate(ObjectiveInput(
            realized_net_usd=realized if realized is not None else 0.0,
            close_count=close_count,
            floating_usd=0.0,  # Not available for adaptive shadows
            open_count=0,  # Not available in adaptive study
            anchor_reset_count=0,  # Not available in adaptive study
            max_adverse_excursion_usd=0.0,
            first_path_verdict=first_path_verdict,
            realized_win_rate=0.0,  # Not available in adaptive study
        ))

    return {
        "lane": lane,
        "basis": basis,
        "realized_usd": realized,
        "close_count": close_count,
        "usd_per_close": usd_per_close,
        "floating_usd": None,
        "notes": notes,
        "first_path_verdict": first_path_verdict,
        "components": components,
        "score_total": total,
        "score_unavailable_reason": "",
        "unified_objective_score": round(unified_result.total, 3) if unified_result else None,
        "unified_objective_verdict": unified_result.verdict if unified_result else None,
    }


def comparison_verdict(incumbent_score: int | None, adaptive_score: int | None, adaptive_basis: str) -> str:
    if incumbent_score is None:
        return "no_incumbent_score"
    if adaptive_score is None:
        return "no_adaptive_score"
    gap = adaptive_score - incumbent_score
    if adaptive_basis in CURRENT_RUN_BASES and gap >= 2:
        return "adaptive_leading_preliminarily"
    if gap <= -2:
        return "incumbent_still_leading"
    return "too_close_or_low_confidence"


def build_payload(
    *,
    incumbent_study: dict[str, Any],
    overnight_packet: dict[str, Any],
    booked_breakdown: dict[str, Any],
    organism_state: dict[str, Any],
) -> dict[str, Any]:
    live_map = live_lane_map(organism_state)
    shadow_map = shadow_lane_map(booked_breakdown)
    overnight_map = overnight_lane_map(overnight_packet)

    rows: list[dict[str, Any]] = []
    for study_row in list(incumbent_study.get("rows") or []):
        if not isinstance(study_row, dict):
            continue
        symbol = as_text(study_row.get("symbol"))
        incumbent = extract_incumbent_metrics(study_row, live_map) if study_row.get("incumbent_present") else {}
        adaptive = extract_adaptive_metrics(study_row, shadow_map, overnight_map) if study_row.get("adaptive_present") else {}
        incumbent_score = incumbent.get("score_total") if incumbent else None
        adaptive_score = adaptive.get("score_total") if adaptive else None
        verdict = comparison_verdict(incumbent_score, adaptive_score, as_text(adaptive.get("basis")))
        score_gap = None
        if incumbent_score is not None and adaptive_score is not None:
            score_gap = adaptive_score - incumbent_score
        row = {
            "symbol": symbol,
            "asset_class": as_text(study_row.get("asset_class")),
            "study_status": as_text(study_row.get("study_status")),
            "shared_score_ready": bool(
                incumbent
                and adaptive
                and as_text(adaptive.get("basis")) in CURRENT_RUN_BASES.union(PROXY_BASES)
            ),
            "comparison_verdict": verdict,
            "score_gap": score_gap,
            "incumbent": incumbent,
            "adaptive": adaptive,
            "why": as_text(study_row.get("why")),
        }
        rows.append(row)

    study_comparable_symbols = [
        row["symbol"]
        for row in rows
        if row.get("incumbent") and row.get("adaptive")
    ]
    summary = {
        "symbol_count": len(rows),
        "study_comparable_symbols": study_comparable_symbols,
        "scored_symbols": [
            row["symbol"] for row in rows
            if row.get("incumbent", {}).get("score_total") is not None and row.get("adaptive", {}).get("score_total") is not None
        ],
        "shared_score_ready_symbols": [row["symbol"] for row in rows if row["shared_score_ready"]],
        "adaptive_leading_symbols": [row["symbol"] for row in rows if row["comparison_verdict"] == "adaptive_leading_preliminarily"],
        "incumbent_leading_symbols": [row["symbol"] for row in rows if row["comparison_verdict"] == "incumbent_still_leading"],
        "low_confidence_symbols": [row["symbol"] for row in rows if row["comparison_verdict"] == "too_close_or_low_confidence"],
        "missing_adaptive_score_symbols": [row["symbol"] for row in rows if row["comparison_verdict"] == "no_adaptive_score"],
        "missing_incumbent_score_symbols": [row["symbol"] for row in rows if row["comparison_verdict"] == "no_incumbent_score"],
    }
    leadership_read = [
        (
            f"Study-comparable symbols are `{summary['study_comparable_symbols']}`, "
            f"but only `{summary['scored_symbols']}` currently expose scoreable adaptive profit evidence."
        ),
        (
            f"Within those scoreable rows, only `{summary['shared_score_ready_symbols']}` have an adaptive score built from current-run or clean-forward evidence instead of pure historical proxy."
        ),
        (
            f"Adaptive-leading rows are `{summary['adaptive_leading_symbols']}`, "
            f"incumbent-leading rows are `{summary['incumbent_leading_symbols']}`, "
            f"and low-confidence rows are `{summary['low_confidence_symbols']}`."
        ),
        (
            f"Rows missing an adaptive score are `{summary['missing_adaptive_score_symbols']}`, "
            f"while rows missing an incumbent score are `{summary['missing_incumbent_score_symbols']}`."
        ),
        "This board is a passive scoring contract for max-profit development. It rewards realized profit and per-close efficiency, but it also penalizes carry inheritance, blocked runtime state, and toxic first-path evidence instead of hiding those costs inside vague readiness language.",
    ]
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(INCUMBENT_STUDY_PATH.relative_to(ROOT)),
            str(OVERNIGHT_PATH.relative_to(ROOT)),
            str(BOOKED_PATH.relative_to(ROOT)),
            str(ORGANISM_PATH.relative_to(ROOT)),
        ],
        "score_contract": {
            "profit_component": "Piecewise score from realized USD.",
            "velocity_component": "Piecewise score from USD per close when close count is honest enough to compute.",
            "conversion_component": "Only used where exact realized and floating fields exist together; missing conversion does not get fabricated.",
            "carry_penalty": "Penalizes inherited/pre-start carry and historical-only proxy reliance.",
            "readiness_component": "Penalizes blocked runtime and research-only adaptive posture.",
            "toxicity_penalty": "Penalizes `never_green_toxic_continuation` first-path evidence.",
        },
        "summary": summary,
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This board is passive. It does not replace the incumbent-study board; it scores the current comparison rows using the cleanest available profit basis for each side.",
            "Incumbent scores prefer exact live realized fields from `organism_state`. Adaptive scores prefer current-run runner-session profit, then first-path realized profit, then clean-forward delta, and only then fall back to booked proxy.",
            "A positive adaptive score does not mean promotion. The comparison verdict still requires fresh evidence basis and a meaningful score gap over the incumbent.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Adaptive Shared Score Board",
        "",
        "This board applies one explicit passive score contract to the incumbent-versus-adaptive rows.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
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
            f"- study_comparable_symbols: `{summary.get('study_comparable_symbols', [])}`",
            f"- scored_symbols: `{summary.get('scored_symbols', [])}`",
            f"- shared_score_ready_symbols: `{summary.get('shared_score_ready_symbols', [])}`",
            f"- adaptive_leading_symbols: `{summary.get('adaptive_leading_symbols', [])}`",
            f"- incumbent_leading_symbols: `{summary.get('incumbent_leading_symbols', [])}`",
            f"- low_confidence_symbols: `{summary.get('low_confidence_symbols', [])}`",
            f"- missing_adaptive_score_symbols: `{summary.get('missing_adaptive_score_symbols', [])}`",
            "",
            "## Score Contract",
            "",
        ]
    )
    for key, read in dict(payload.get("score_contract") or {}).items():
        lines.append(f"- `{key}`: {read}")

    lines.extend(
        [
            "",
            "## Score Table",
            "",
            "| Symbol | Study status | Incumbent score | Incumbent unified | Adaptive score | Adaptive unified | Gap | Adaptive basis | Verdict |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        incumbent_score = row.get("incumbent", {}).get("score_total")
        incumbent_unified = row.get("incumbent", {}).get("unified_objective_score")
        adaptive_score = row.get("adaptive", {}).get("score_total")
        adaptive_unified = row.get("adaptive", {}).get("unified_objective_score")
        gap = row.get("score_gap")
        lines.append(
            f"| `{row['symbol']}` | `{row['study_status']}` | "
            f"{incumbent_score if incumbent_score is not None else '-'} | "
            f"{incumbent_unified if incumbent_unified is not None else '-'} | "
            f"{adaptive_score if adaptive_score is not None else '-'} | "
            f"{adaptive_unified if adaptive_unified is not None else '-'} | "
            f"{gap if gap is not None else '-'} | "
            f"`{row.get('adaptive', {}).get('basis', '-')}` | "
            f"`{row['comparison_verdict']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- study_status: `{row['study_status']}`",
                f"- comparison_verdict: `{row['comparison_verdict']}`",
                f"- score_gap: `{row['score_gap']}`",
                f"- why: {row['why']}",
            ]
        )
        incumbent = dict(row.get("incumbent") or {})
        adaptive = dict(row.get("adaptive") or {})
        if incumbent:
            lines.extend(
                [
                    f"- incumbent_lane: `{incumbent.get('lane', '')}`",
                    f"- incumbent_basis: `{incumbent.get('basis', '')}`",
                    f"- incumbent_realized_usd: `{incumbent.get('realized_usd')}`",
                    f"- incumbent_usd_per_close: `{incumbent.get('usd_per_close')}`",
                    f"- incumbent_score_total: `{incumbent.get('score_total')}`",
                    f"- incumbent_unified_objective: `{incumbent.get('unified_objective_score')}`",
                    f"- incumbent_unified_verdict: `{incumbent.get('unified_objective_verdict') or '-'}`",
                    f"- incumbent_components: `{incumbent.get('components')}`",
                ]
            )
        if adaptive:
            lines.extend(
                [
                    f"- adaptive_lane: `{adaptive.get('lane', '')}`",
                    f"- adaptive_basis: `{adaptive.get('basis', '')}`",
                    f"- adaptive_realized_usd: `{adaptive.get('realized_usd')}`",
                    f"- adaptive_usd_per_close: `{adaptive.get('usd_per_close')}`",
                    f"- adaptive_first_path_verdict: `{adaptive.get('first_path_verdict', '')}`",
                    f"- adaptive_score_total: `{adaptive.get('score_total')}`",
                    f"- adaptive_unified_objective: `{adaptive.get('unified_objective_score')}`",
                    f"- adaptive_unified_verdict: `{adaptive.get('unified_objective_verdict') or '-'}`",
                    f"- adaptive_score_unavailable_reason: `{adaptive.get('score_unavailable_reason', '')}`",
                    f"- adaptive_components: `{adaptive.get('components')}`",
                ]
            )
        lines.append("")

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        incumbent_study=load_json(INCUMBENT_STUDY_PATH),
        overnight_packet=load_json(OVERNIGHT_PATH),
        booked_breakdown=load_json(BOOKED_PATH),
        organism_state=load_json(ORGANISM_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
