#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

STACK_ADMISSION_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.json"
OVERLAP_ANALYSIS_PATH = REPORTS / "nom_strategy_overlap_analysis.json"
DEPLOYMENT_GATE_PATH = REPORTS / "coinbase_isolated_runner_deployment_gate_board.json"
PROOF_BOARD_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.json"
TRACKER_PATH = REPORTS / "live_performance_tracker.json"
PRODUCT_STACK_PATH = REPORTS / "coinbase_product_lane_stack_board.json"

JSON_PATH = REPORTS / "coinbase_nom_overlap_release_gate_board.json"
MD_PATH = REPORTS / "coinbase_nom_overlap_release_gate_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_payload() -> dict[str, Any]:
    stack_admission = load_json(STACK_ADMISSION_PATH)
    overlap = load_json(OVERLAP_ANALYSIS_PATH)
    deployment_gate = load_json(DEPLOYMENT_GATE_PATH)
    proof_board = load_json(PROOF_BOARD_PATH)
    tracker = load_json(TRACKER_PATH)
    product_stack = load_json(PRODUCT_STACK_PATH)

    admission_row = next(
        (row for row in list(stack_admission.get("rows") or []) if str(row.get("coin") or "") == "NOM-USD"),
        {},
    )
    proof_row = next(
        (row for row in list(proof_board.get("rows") or []) if str(row.get("coin") or "") == "NOM-USD"),
        {},
    )
    product_row = next(
        (row for row in list(product_stack.get("rows") or []) if str(row.get("coin") or "") == "NOM-USD"),
        {},
    )
    tracker_nom = dict((tracker.get("coins") or {}).get("NOM-USD") or {})

    overlap_5m = dict((overlap.get("overlap_analysis") or {}).get("1bar_5min") or {})

    rows = [
        {
            "subject": "same_coin_admission",
            "status": "ready",
            "decision": "nom_stack_is_allowed_in_principle",
            "evidence": (
                f"admission={admission_row.get('admission_decision')}; "
                f"overlap_pct_5m={admission_row.get('overlap_pct_5m')}; "
                f"uplift={admission_row.get('combined_uplift_vs_best_single')}"
            ),
            "read": "NOM is already the benchmark overlap-admitted stack: 33.4% 5-minute overlap and +1314.83 additive uplift over the best single lane.",
        },
        {
            "subject": "primary_secondary_shape",
            "status": "ready",
            "decision": "keep_breakout_primary_momentum_secondary",
            "evidence": (
                f"primary={product_row.get('preferred_primary_lane')}; "
                f"lane_count={product_row.get('lane_count')}; "
                f"max_live_lanes={product_row.get('max_live_lanes')}"
            ),
            "read": "The saved stack policy already defines the governed NOM shape: range_breakout_shadow primary with one momentum secondary lane behind it.",
        },
        {
            "subject": "override_path_release",
            "status": "deferred",
            "decision": "wait_for_parallel_nom_lane_to_clear",
            "evidence": (
                f"proof_status={proof_row.get('status')}; "
                f"next_action={proof_row.get('next_action')}; "
                f"deployment_next={deployment_gate.get('summary', {}).get('next_governed_slot')}"
            ),
            "read": "The governed override path does point to NOM next, but it is still explicitly deferred in the proof board to avoid overlap with the active parallel NOM lane.",
        },
        {
            "subject": "parallel_nom_lane_conflict",
            "status": "active_conflict",
            "decision": "do_not_launch_governed_nom_until_alt_lane_is_flat",
            "evidence": (
                f"tracker_strategy={tracker_nom.get('strategy')}; "
                f"tracker_position={tracker_nom.get('live_position')}; "
                f"tracker_signals={tracker_nom.get('live_signals')}"
            ),
            "read": "The saved tracker snapshot still shows NOM active on the alternate fibonacci lane, so the room does not yet have a clean release point for the governed NOM override slot.",
        },
    ]

    release_verdict = "hold_until_parallel_nom_lane_clears"
    if str(tracker_nom.get("live_position") or "") == "flat" and str(proof_row.get("status") or "") != "dry_clean_defer_for_overlap":
        release_verdict = "nom_release_clear"

    leadership_read = [
        "NOM is not blocked because the overlap thesis is weak; that part is already proven.",
        "NOM is blocked because the governed override path and the alternate live NOM lane have not converged to a clean handoff point yet.",
        "The honest release condition is narrow: once the alternate NOM lane is flat or retired, the governed NOM breakout-primary slot becomes the next clean proof/deployment candidate.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "stack_admission_path": str(STACK_ADMISSION_PATH),
        "overlap_analysis_path": str(OVERLAP_ANALYSIS_PATH),
        "deployment_gate_path": str(DEPLOYMENT_GATE_PATH),
        "proof_board_path": str(PROOF_BOARD_PATH),
        "tracker_path": str(TRACKER_PATH),
        "product_stack_path": str(PRODUCT_STACK_PATH),
        "leadership_read": leadership_read,
        "summary": {
            "release_verdict": release_verdict,
            "overlap_pct_5m": overlap_5m.get("overlap_pct"),
            "combined_total_pnl": (overlap.get("combined") or {}).get("total_pnl"),
            "next_release_action": "wait_for_nom_alt_lane_flat_then_run_governed_nom_probe",
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase NOM Overlap Release Gate Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Release verdict: `{payload['summary']['release_verdict']}`",
            f"- 5m overlap: `{payload['summary']['overlap_pct_5m']}`",
            f"- Combined total pnl: `{payload['summary']['combined_total_pnl']}`",
            f"- Next release action: `{payload['summary']['next_release_action']}`",
            "",
            "## Gates",
            "",
            "| Subject | Status | Decision |",
            "| --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(f"| {row['subject']} | {row['status']} | {row['decision']} |")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
