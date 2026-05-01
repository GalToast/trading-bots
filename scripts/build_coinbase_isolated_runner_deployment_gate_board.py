#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

READINESS_PATH = REPORTS / "deployment_readiness_assessment.json"
GOVERNANCE_PATH = REPORTS / "coinbase_isolated_runner_book_governance_board.json"
PROOF_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.json"
TRACKER_PATH = REPORTS / "live_performance_tracker.json"

JSON_PATH = REPORTS / "coinbase_isolated_runner_deployment_gate_board.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_deployment_gate_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_payload() -> dict[str, Any]:
    readiness = load_json(READINESS_PATH)
    governance = load_json(GOVERNANCE_PATH)
    proof = load_json(PROOF_PATH)
    tracker = load_json(TRACKER_PATH)

    governance_rows = {str(row.get("subject") or ""): row for row in list(governance.get("rows") or [])}
    proof_rows = {str(row.get("coin") or ""): row for row in list(proof.get("rows") or [])}
    tracker_cycle = int(tracker.get("runner_cycle") or 0)
    tracker_total_closes = int(tracker.get("total_closes") or 0)

    rows = [
        {
            "subject": "infrastructure_base",
            "status": "ready",
            "decision": "accept_operational_foundation",
            "evidence": str((readiness.get("infrastructure") or {}).get("isolated_runner") or ""),
            "read": "The isolated runner, crash recovery, dashboard, registry, and tracker tooling exist and are runnable.",
        },
        {
            "subject": "strategy_book_governance",
            "status": "blocked",
            "decision": "do_not_launch_default_runner_book",
            "evidence": str((governance_rows.get("full_deploy_now_command") or {}).get("status") or ""),
            "read": "The default full-runner launch is still explicitly rejected in the governance board because the book rewrite and validation scope conflicts are unresolved.",
        },
        {
            "subject": "override_path_signal_evidence",
            "status": "blocked",
            "decision": "wait_for_signal_or_governed_next_slot",
            "evidence": (
                f"TRU={proof_rows.get('TRU-USD', {}).get('status','')}; "
                f"SUP={proof_rows.get('SUP-USD', {}).get('status','')}; "
                f"NOM={proof_rows.get('NOM-USD', {}).get('status','')}"
            ),
            "read": "TRU and SUP are operationally clean across multiple bounded windows but still have zero signals/closes; NOM is the next governed slot, but still deferred for overlap.",
        },
        {
            "subject": "live_monitoring_state",
            "status": "not_live_yet",
            "decision": "do_not_treat_tracker_snapshot_as_live_watch_confirmation",
            "evidence": f"runner_cycle={tracker_cycle}; total_closes={tracker_total_closes}; total_equity={tracker.get('total_equity')}",
            "read": "The saved tracker snapshot is from a bounded smoke state, not an active live watch regime with realized closes across the governed book.",
        },
        {
            "subject": "deployment_readiness_doc",
            "status": "partial",
            "decision": "treat_conditional_go_as_infrastructure_only",
            "evidence": str(readiness.get("status") or ""),
            "read": "The readiness assessment is useful, but its conditional-go framing still depends on pending probe coverage, monitoring, and allocation approval that have not converged with the governed proof board.",
        },
    ]

    blocking_subjects = [row["subject"] for row in rows if row["status"] in {"blocked", "not_live_yet"}]
    proof_summary = dict(proof.get("summary") or {})
    verdict = "hold_for_governed_proof_completion"
    if not blocking_subjects:
        verdict = "governed_go"

    leadership_read = [
        "Infrastructure readiness is real, but the governed deployment gate is still closed.",
        "The hard blocker is not runner stability anymore; it is that the default runner book is not yet the board-approved book, and the exact-config override proof lanes have stayed flat across their bounded windows.",
        "That leaves one honest next governed path: wait for the NOM overlap lane to clear, or wait for a real signal window on the already-clean TRU and SUP lanes before pretending the deployment question is settled.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "readiness_path": str(READINESS_PATH),
        "governance_path": str(GOVERNANCE_PATH),
        "proof_path": str(PROOF_PATH),
        "tracker_path": str(TRACKER_PATH),
        "leadership_read": leadership_read,
        "summary": {
            "verdict": verdict,
            "blocking_subjects": blocking_subjects,
            "next_governed_slot": str(proof_summary.get("deferred_next_target") or ""),
            "next_governed_strategy": str(proof_summary.get("deferred_next_strategy") or ""),
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Deployment Gate Board",
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
            f"- Verdict: `{payload['summary']['verdict']}`",
            f"- Blocking subjects: `{', '.join(payload['summary']['blocking_subjects'])}`",
            f"- Next governed slot: `{payload['summary']['next_governed_slot']}`",
            f"- Next governed strategy: `{payload['summary']['next_governed_strategy']}`",
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
