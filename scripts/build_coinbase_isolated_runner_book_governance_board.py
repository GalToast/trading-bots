#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

DEPLOYMENT_BRIEF_PATH = REPORTS / "deployment_decision_brief.md"
SUPERTREND_VALIDATION_PATH = REPORTS / "supertrend_30d_validation.json"
TOP3_VALIDATION_PATH = REPORTS / "validate_top3_edges_30d.json"
FIX_VERIFICATION_PATH = REPORTS / "coinbase_isolated_runner_fix_verification.json"
HYPERGROWTH_ROUTER_PATH = REPORTS / "coinbase_spot_hypergrowth_router_board.json"
SLEEVE_CONFIG_PATH = REPORTS / "coinbase_isolated_runner_sleeve_book_config.json"
EXACT_QUEUE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.json"

JSON_PATH = REPORTS / "coinbase_isolated_runner_book_governance_board.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_book_governance_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_payload() -> dict[str, Any]:
    brief = load_text(DEPLOYMENT_BRIEF_PATH)
    supertrend = load_json(SUPERTREND_VALIDATION_PATH)
    top3_validation = load_json(TOP3_VALIDATION_PATH) if TOP3_VALIDATION_PATH.exists() else {}
    fix_verification = load_json(FIX_VERIFICATION_PATH)
    router = load_json(HYPERGROWTH_ROUTER_PATH)
    sleeve_config = load_json(SLEEVE_CONFIG_PATH)
    exact_queue = load_json(EXACT_QUEUE_PATH)

    supertrend_best = dict(supertrend.get("best_params") or {})
    supertrend_total = float(supertrend_best.get("total_net_pnl") or 0.0)
    supertrend_profitable = int(supertrend_best.get("profitable_coins") or 0)
    supertrend_total_coins = int(supertrend_best.get("total_coins") or 0)

    router_rows = list(router.get("rows") or [])
    approved_families = sorted({str(row.get("primary_family") or "") for row in router_rows if row.get("primary_family")})
    core_coins = list((router.get("summary") or {}).get("core_coins") or [])
    config_rows = list(sleeve_config.get("configs") or [])
    exact_queue_first = str((exact_queue.get("summary") or {}).get("first_smoke_candidate") or "")
    top3_results = dict(top3_validation.get("results") or {})
    fibonacci_result = dict(top3_results.get("fibonacci_breakout") or {})
    focused_supertrend_result = dict(top3_results.get("supertrend") or {})
    fibonacci_artifact_found = bool(fibonacci_result)
    fibonacci_total = float(fibonacci_result.get("total_pnl") or 0.0)
    fibonacci_profitable = int(fibonacci_result.get("profitable_coins") or 0)
    focused_supertrend_total = float(focused_supertrend_result.get("total_pnl") or 0.0)
    focused_supertrend_profitable = int(focused_supertrend_result.get("profitable_coins") or 0)

    rows = [
        {
            "subject": "isolated_runner_operational_readiness",
            "status": "controlled_smoke_ready_only",
            "decision": "accept_runner_durability_but_not_book_rewrite",
            "evidence": str(fix_verification.get("verification_verdict") or ""),
            "read": "Restart drills cleared the isolated runner for controlled smoke, which is operational readiness rather than strategy-book approval.",
        },
        {
            "subject": "approved_book_anchor",
            "status": "router_book_still_canonical",
            "decision": "keep_hypergrowth_router_as_strategy_governor",
            "evidence": f"core={core_coins}, families={approved_families}",
            "read": "The saved board-approved spot book is still the router stack built around momentum and range_breakout, not a fresh supertrend/fibonacci replacement.",
        },
        {
            "subject": "supertrend_deploy_now_claim",
            "status": "scope_conflict_needs_governance",
            "decision": "do_not_rebase_book_on_supertrend_without_reconciling_validation_scope",
            "evidence": (
                f"deployment_brief_claims_2705_5of5={'2,705' in brief and '5/5' in brief}; "
                f"broad_saved_best={supertrend_total:.2f}_{supertrend_profitable}of{supertrend_total_coins}; "
                f"focused_top5_saved={focused_supertrend_total:.2f}_{focused_supertrend_profitable}of5"
            ),
            "read": "There is now a saved focused top-5 supertrend artifact matching the deployment brief, but it still conflicts materially with the broader 20-coin supertrend validation. Supertrend is saved evidence, not settled governance.",
        },
        {
            "subject": "fibonacci_breakout_deploy_now_claim",
            "status": "saved_validation_exists_but_not_router_governed",
            "decision": "require_router_and_sleeve_integration_before_book_rewrite",
            "evidence": f"saved_artifact_found={fibonacci_artifact_found}; total={fibonacci_total:.2f}; profitable={fibonacci_profitable}of5",
            "read": "The saved focused top-5 validation artifact does show fibonacci_breakout at +$3582.61 across 5/5 profitable coins, but the canonical router, sleeve config, and exact-config smoke queue have not been re-authored around it yet.",
        },
        {
            "subject": "runner_smoke_vs_sleeve_book",
            "status": "separate_evidence_classes",
            "decision": "use_override_queue_for_book_honest_smokes",
            "evidence": f"exact_rows={len([row for row in config_rows if str(row.get('config_status') or '').startswith('exact_')])}, first_exact={exact_queue_first}",
            "read": "Generic isolated-runner smoke passes only prove the runner can run; honest sleeve-book proof should flow through the override config and the exact-config smoke queue, starting with TRU-USD.",
        },
        {
            "subject": "full_deploy_now_command",
            "status": "reject_for_now",
            "decision": "do_not_treat_default_runner_command_as_board_approved_deployment",
            "evidence": "deployment_brief_recommends_full_runner_command=True",
            "read": "The repo should not treat `python scripts/multi_coin_isolated_runner.py --total-cash 48` as a board-approved deployment command while the room is simultaneously rewriting the strategy book in chat without saved reconciliation artifacts.",
        },
    ]

    summary = {
        "rows": len(rows),
        "blocking_claims": sum(1 for row in rows if str(row["status"]).startswith(("scope_conflict", "saved_validation_exists", "reject"))),
        "approved_next_path": "run exact-config override smokes in queue order",
        "first_exact_smoke": exact_queue_first,
    }

    leadership_read = [
        "The isolated runner is operationally smoke-ready, but that does not authorize a spontaneous strategy-book rewrite.",
        "The saved router and sleeve artifacts still define the canonical book: momentum and range_breakout lanes, with exact-config override smokes leading the next proof wave.",
        "There is now a saved focused top-5 artifact for fibonacci and supertrend, but it has not yet been reconciled with the broader validation boards or integrated into the canonical router and sleeve stack.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "deployment_brief_path": str(DEPLOYMENT_BRIEF_PATH),
        "supertrend_validation_path": str(SUPERTREND_VALIDATION_PATH),
        "top3_validation_path": str(TOP3_VALIDATION_PATH),
        "fix_verification_path": str(FIX_VERIFICATION_PATH),
        "hypergrowth_router_path": str(HYPERGROWTH_ROUTER_PATH),
        "sleeve_config_path": str(SLEEVE_CONFIG_PATH),
        "exact_queue_path": str(EXACT_QUEUE_PATH),
        "leadership_read": leadership_read,
        "summary": summary,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Book Governance Board",
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
            f"- Rows: `{payload['summary']['rows']}`",
            f"- Blocking claims: `{payload['summary']['blocking_claims']}`",
            f"- Approved next path: `{payload['summary']['approved_next_path']}`",
            f"- First exact smoke: `{payload['summary']['first_exact_smoke']}`",
            "",
            "## Board",
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
