#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEPLOYMENT_GATE_PATH = REPORTS / "hungry_hippo_deployment_safety_gate_board.json"
GUARDRAIL_AUDIT_PATH = REPORTS / "hungry_hippo_shapeshifter_guardrail_audit.json"
LAUNCH_SAFETY_PATH = REPORTS / "hungry_hippo_launch_safety_validation.json"
OUT_JSON = REPORTS / "hungry_hippo_symbol_portability_board.json"
OUT_MD = REPORTS / "hungry_hippo_symbol_portability_board.md"

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from hungry_hippo_symbol_profiles import infer_asset_class


MICRO_FORWARD_PROOF_REASONS = {
    "atr_micro_step_without_forward_proof",
    "micro_step_without_20_forward_closes",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def rows_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        symbol = normalize_symbol(row.get("symbol"))
        if symbol:
            out[symbol] = dict(row)
    return out


def launch_rows_by_symbol(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in list(payload.get("rows") or []):
        symbol = normalize_symbol(row.get("symbol"))
        if symbol:
            out.setdefault(symbol, []).append(dict(row))
    return out


def candidate_contract_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("scope") or "") != "live_surface"]


def hard_fail_reasons(rows: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for row in rows:
        reasons.extend(str(item) for item in list(row.get("hard_fail_reasons") or []) if str(item))
    return dedupe_preserve(reasons)


def advisory_reasons(rows: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for row in rows:
        reasons.extend(str(item) for item in list(row.get("advisory_reasons") or []) if str(item))
    return dedupe_preserve(reasons)


def derive_status(
    *,
    asset_class: str,
    deployment_row: dict[str, Any] | None,
    guardrail_row: dict[str, Any] | None,
    launch_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    if asset_class == "unknown":
        return "unknown_family", "runtime_symbol_profile"

    deployment_verdict = str((deployment_row or {}).get("deployment_verdict") or "missing")
    guardrail_status = str((guardrail_row or {}).get("status") or "missing")
    candidate_rows = candidate_contract_rows(launch_rows)
    launch_fail_reasons = set(hard_fail_reasons(candidate_rows))
    gate_hard_reasons = set(str(item) for item in list((deployment_row or {}).get("hard_block_reasons") or []))

    if guardrail_status in {"missing", "unknown", "uncovered"}:
        return "portable_missing_policy", "canonical_guardrail_and_regime"

    if not candidate_rows:
        return "portable_missing_launch_contract", "shadow_or_live_launch_contract"

    if guardrail_status in {"blocked_by_guardrail", "contradiction"}:
        return "portable_guardrail_blocked", "guardrail_alignment"

    if deployment_verdict == "cleared_for_shadow_discussion":
        return "ready_for_shadow_discussion", "fresh_forward_proof"

    if gate_hard_reasons & MICRO_FORWARD_PROOF_REASONS or launch_fail_reasons & MICRO_FORWARD_PROOF_REASONS:
        return "portable_waiting_forward_proof", "forward_shadow_proof"

    if deployment_verdict == "hard_block" and launch_fail_reasons:
        return "portable_contract_blocked", "launch_contract_safety"

    if deployment_verdict == "hard_block":
        return "portable_gate_blocked", "deployment_gate"

    if deployment_verdict == "manual_review":
        return "portable_manual_review", "symbol_specific_manual_review"

    if deployment_verdict == "missing":
        return "portable_missing_gate_surface", "deployment_gate_surface"

    return "portable_research_only", "research_followup"


def build_row(
    symbol: str,
    deployment_row: dict[str, Any] | None,
    guardrail_row: dict[str, Any] | None,
    launch_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    asset_class = infer_asset_class(symbol)
    candidate_rows = candidate_contract_rows(launch_rows)
    launch_fail_list = hard_fail_reasons(candidate_rows)
    advisory_list = advisory_reasons(candidate_rows)
    status, highest_leverage_gap = derive_status(
        asset_class=asset_class,
        deployment_row=deployment_row,
        guardrail_row=guardrail_row,
        launch_rows=launch_rows,
    )

    note_map = {
        "ready_for_shadow_discussion": "Portable stack exists; next honest blocker is fresh forward proof rather than new symbol wiring.",
        "portable_waiting_forward_proof": "Canonical policy and at least one runnable contract exist; the gate is waiting on enough fresh forward proof.",
        "portable_guardrail_blocked": "The family is portable, but the current canonical guardrail or geometry alignment blocks honest rollout.",
        "portable_contract_blocked": "The family is portable, but the checked-in launch contract still violates the current safety contract.",
        "portable_missing_policy": "Family defaults exist, but canonical regime/rearm policy coverage is still missing.",
        "portable_missing_launch_contract": "Policy exists, but there is no checked-in shadow/live launch contract yet for this symbol.",
        "portable_gate_blocked": "A deployment gate exists, but it still blocks this symbol for reasons beyond simple proof debt.",
        "portable_manual_review": "Portable in principle, but current surfaces still require manual symbol-specific review.",
        "portable_missing_gate_surface": "Policy and contract exist, but no deployment-gate row is available yet.",
        "portable_research_only": "Portable in principle, but the current surfaces still leave it in research posture.",
        "unknown_family": "The runtime does not yet have a family-default profile for this symbol.",
    }

    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "family_portable": asset_class != "unknown",
        "has_deployment_gate": deployment_row is not None,
        "has_guardrail_audit": guardrail_row is not None,
        "launch_contract_count": len(candidate_rows),
        "live_surface_count": sum(1 for row in launch_rows if str(row.get("scope") or "") == "live_surface"),
        "enabled_launch_contract_count": sum(1 for row in candidate_rows if bool(row.get("enabled"))),
        "failing_enabled_launch_contract_count": sum(
            1 for row in candidate_rows if bool(row.get("enabled")) and str(row.get("verdict") or "") == "fail"
        ),
        "surface_coverage_complete": deployment_row is not None and guardrail_row is not None and len(candidate_rows) > 0,
        "deployment_verdict": str((deployment_row or {}).get("deployment_verdict") or "missing"),
        "guardrail_status": str((guardrail_row or {}).get("status") or "missing"),
        "hard_block_reasons": [str(item) for item in list((deployment_row or {}).get("hard_block_reasons") or []) if str(item)],
        "manual_review_reasons": [str(item) for item in list((deployment_row or {}).get("manual_review_reasons") or []) if str(item)],
        "launch_contract_fail_reasons": launch_fail_list,
        "launch_contract_advisory_reasons": advisory_list,
        "generalization_status": status,
        "highest_leverage_gap": highest_leverage_gap,
        "note": note_map.get(status, ""),
    }


def format_reason_list(items: list[str], *, limit: int = 2) -> str:
    if not items:
        return "-"
    return ", ".join(items[:limit])


def build_payload(
    deployment_gate: dict[str, Any],
    guardrail_audit: dict[str, Any],
    launch_safety: dict[str, Any],
) -> dict[str, Any]:
    deployment_rows = rows_by_symbol(deployment_gate)
    guardrail_rows = rows_by_symbol(guardrail_audit)
    launch_rows = launch_rows_by_symbol(launch_safety)
    symbols = sorted(set(deployment_rows) | set(guardrail_rows) | set(launch_rows))

    rows = [
        build_row(
            symbol=symbol,
            deployment_row=deployment_rows.get(symbol),
            guardrail_row=guardrail_rows.get(symbol),
            launch_rows=launch_rows.get(symbol, []),
        )
        for symbol in symbols
    ]
    rows.sort(key=lambda row: (row["generalization_status"], row["symbol"]))

    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["generalization_status"]] = status_counts.get(row["generalization_status"], 0) + 1

    ready_symbols = [row["symbol"] for row in rows if row["generalization_status"] == "ready_for_shadow_discussion"]
    waiting_symbols = [row["symbol"] for row in rows if row["generalization_status"] == "portable_waiting_forward_proof"]
    missing_policy_symbols = [row["symbol"] for row in rows if row["generalization_status"] == "portable_missing_policy"]
    guardrail_blocked_symbols = [row["symbol"] for row in rows if row["generalization_status"] == "portable_guardrail_blocked"]
    contract_blocked_symbols = [row["symbol"] for row in rows if row["generalization_status"] == "portable_contract_blocked"]
    missing_launch_contract_symbols = [
        row["symbol"] for row in rows if row["generalization_status"] == "portable_missing_launch_contract"
    ]
    family_portable_count = sum(1 for row in rows if row["family_portable"])
    surface_coverage_complete_count = sum(1 for row in rows if row["surface_coverage_complete"])

    leadership_read = [
        f"Family-based runtime defaults already cover `{family_portable_count}/{len(rows)}` discovered Hungry Hippo symbols, so the current ceiling on 'almost any symbol' is governance and contract coverage, not symbol parsing.",
        f"Only `{surface_coverage_complete_count}/{len(rows)}` symbols currently have the full portability stack: deployment gate + guardrail audit + at least one runnable launch contract.",
        (
            f"Nearest symbols are `ready_for_shadow_discussion={ready_symbols or ['none']}` and "
            f"`portable_waiting_forward_proof={waiting_symbols or ['none']}`; the bigger debt is "
            f"`portable_missing_policy={missing_policy_symbols or ['none']}`."
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            "reports/hungry_hippo_deployment_safety_gate_board.json",
            "reports/hungry_hippo_shapeshifter_guardrail_audit.json",
            "reports/hungry_hippo_launch_safety_validation.json",
        ],
        "summary": {
            "symbol_count": len(rows),
            "family_portable_count": family_portable_count,
            "surface_coverage_complete_count": surface_coverage_complete_count,
            "status_counts": status_counts,
            "ready_for_shadow_discussion_symbols": ready_symbols,
            "waiting_forward_proof_symbols": waiting_symbols,
            "missing_policy_symbols": missing_policy_symbols,
            "guardrail_blocked_symbols": guardrail_blocked_symbols,
            "contract_blocked_symbols": contract_blocked_symbols,
            "missing_launch_contract_symbols": missing_launch_contract_symbols,
        },
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This is a portability/governance surface. It does not claim that a symbol is profitable or live-ready.",
            "`ready_for_shadow_discussion` means the family-default profile, policy surface, and runnable contract all exist; it still needs fresh forward proof.",
            "`portable_waiting_forward_proof` means the main blocker is proof debt rather than missing symbol wiring.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Symbol Portability Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: separate family portability from policy debt, guardrail debt, and launch-contract debt so 'almost any symbol' is judged honestly.",
        "",
        "## Leadership Read",
        "",
    ]
    for line in list(payload.get("leadership_read") or []):
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Symbol count: `{summary.get('symbol_count', 0)}`",
            f"- Family portable count: `{summary.get('family_portable_count', 0)}`",
            f"- Full-stack coverage count: `{summary.get('surface_coverage_complete_count', 0)}`",
            f"- Ready for shadow discussion: `{summary.get('ready_for_shadow_discussion_symbols', [])}`",
            f"- Waiting forward proof: `{summary.get('waiting_forward_proof_symbols', [])}`",
            f"- Missing policy: `{summary.get('missing_policy_symbols', [])}`",
            "",
            "## Rows",
            "",
            "| Symbol | Asset | Status | Deployment Gate | Guardrail | Contracts | Blocking Signals | Next Gap |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        contracts = (
            f"candidate={row.get('launch_contract_count', 0)} / "
            f"live_surface={row.get('live_surface_count', 0)}"
        )
        blocking = format_reason_list(
            list(row.get("hard_block_reasons") or [])
            or list(row.get("launch_contract_fail_reasons") or [])
            or list(row.get("manual_review_reasons") or [])
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("symbol") or ""),
                    str(row.get("asset_class") or ""),
                    str(row.get("generalization_status") or ""),
                    str(row.get("deployment_verdict") or ""),
                    str(row.get("guardrail_status") or ""),
                    contracts,
                    blocking,
                    str(row.get("highest_leverage_gap") or ""),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> None:
    deployment_gate = load_json(DEPLOYMENT_GATE_PATH)
    guardrail_audit = load_json(GUARDRAIL_AUDIT_PATH)
    launch_safety = load_json(LAUNCH_SAFETY_PATH)
    payload = build_payload(deployment_gate, guardrail_audit, launch_safety)
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
