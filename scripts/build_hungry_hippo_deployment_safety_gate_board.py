#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SPREAD_ROBUSTNESS_PATH = REPORTS / "spread_robustness.json"
ATR_PARAMS_PATH = REPORTS / "hungry_hippo_atr_step_params.json"
ATR_AUDIT_PATH = REPORTS / "hungry_hippo_atr_step_audit.json"
GUARDRAIL_AUDIT_PATH = REPORTS / "hungry_hippo_shapeshifter_guardrail_audit.json"
READINESS_BOARD_PATH = REPORTS / "shadow_graduation_readiness_board.json"
FIRST_PILOT_BOARD_GLOB = "*_first_pilot_comparison_board.json"
CONTROL_GATE_BOARD_GLOB = "*_control_proof_gate_board.json"
CONTROL_STATE_GLOB = "penetration_lattice_shadow_*_control_state.json"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_deployment_safety_gate_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_deployment_safety_gate_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def normalize_rows(payload: dict[str, Any], key: str, symbol_key: str = "symbol") -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in list(payload.get(key) or []):
        symbol = str(row.get(symbol_key) or "").upper()
        if symbol:
            rows[symbol] = row
    return rows


def symbol_from_candidate(candidate: str) -> str:
    token = str(candidate or "").split(" ", 1)[0].upper()
    return token


def extract_proof_closes(readiness_row: dict[str, Any]) -> int:
    evidence = dict(readiness_row.get("evidence") or {})
    for key in ("closes", "shadow_realized_closes", "forward_closes_on_shelf"):
        if key in evidence:
            try:
                return int(evidence.get(key) or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def build_readiness_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        candidate = str(row.get("candidate") or "")
        symbol = symbol_from_candidate(candidate)
        if not symbol:
            continue
        closes = extract_proof_closes(row)
        current = out.get(symbol)
        if current is None or closes > int(current.get("_proof_closes") or 0):
            enriched = dict(row)
            enriched["_proof_closes"] = closes
            out[symbol] = enriched
    return out


def effective_step_ratio(atr_row: dict[str, Any]) -> float:
    atr = float(atr_row.get("atr_current") or 0.0)
    if atr <= 0:
        return 0.0
    step_candidates = [
        float(atr_row.get("step") or 0.0),
        float(atr_row.get("step_buy") or 0.0),
        float(atr_row.get("step_sell") or 0.0),
    ]
    positive = [value for value in step_candidates if value > 0]
    if not positive:
        return 0.0
    return min(positive) / atr


def infer_shadow_context_symbol(
    first_pilot_board: dict[str, Any] | None,
    control_state: dict[str, Any] | None,
) -> str:
    symbol_rows = dict((control_state or {}).get("symbols") or {})
    if len(symbol_rows) == 1:
        return str(next(iter(symbol_rows.keys()))).upper()
    summary = dict((first_pilot_board or {}).get("summary") or {})
    first_pilot = str(summary.get("first_pilot") or "")
    return symbol_from_candidate(first_pilot)


def build_shadow_context_overrides(
    first_pilot_board: dict[str, Any] | None,
    control_state: dict[str, Any] | None,
    control_gate: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    if not isinstance(first_pilot_board, dict):
        return overrides
    symbol = infer_shadow_context_symbol(first_pilot_board, control_state)
    if not symbol:
        return overrides

    normalization = dict(first_pilot_board.get("normalization_recommendation") or {})
    control_options = list(first_pilot_board.get("control_options") or [])
    option_b = next(
        (row for row in control_options if str(row.get("option") or "") == "B_use_step14_as_control"),
        {},
    )
    recommended_control_step = float(normalization.get("recommended_control_step") or 0.0)
    proof_summary = dict((control_gate or {}).get("summary") or {})
    proof_verdict = str(proof_summary.get("verdict") or "")
    comparison_status = str(proof_summary.get("comparison_status") or first_pilot_board.get("comparison_status") or "")

    state_metadata = dict((control_state or {}).get("metadata") or {})
    state_symbol = dict(((control_state or {}).get("symbols") or {}).get(symbol) or {})
    runtime_step = float(state_metadata.get("step") or 0.0)
    runtime_closes = int(state_symbol.get("realized_closes") or 0)
    runtime_net = float(state_symbol.get("realized_net_usd") or 0.0)
    proof_closes = int(proof_summary["realized_closes"]) if "realized_closes" in proof_summary else runtime_closes
    proof_net = float(proof_summary["realized_net_usd"]) if "realized_net_usd" in proof_summary else runtime_net

    if recommended_control_step <= 0:
        return overrides

    active_step = runtime_step or recommended_control_step
    if active_step < recommended_control_step:
        return overrides

    symbol_label = symbol
    overrides[symbol] = {
        "effective_spread_status": "CONTROL-UNDER-TEST",
        "spread_scope_note": (
            f"Recorded spread loss belongs to archival control geometry. Current shadow control is running at "
            f"step {active_step:.0f}, which clears the documented min-viable floor, so spread truth "
            f"must stay scoped to the failing geometry."
        ),
        "control_context": (
            f"Aligned {symbol_label} control remains research-only: proof_verdict={proof_verdict or 'unknown'}; "
            f"comparison_status={comparison_status or 'unknown'}; realized_closes={proof_closes}; "
            f"realized_net_usd={proof_net:.2f}. The control arm itself still needs positive proof "
            f"before any same-shape OFF vs budgeted-ON deployment talk."
            if proof_verdict == "blocked_by_negative_expectancy"
            else f"Aligned {symbol_label} control is the intended baseline, but proof_verdict={proof_verdict or 'unknown'} "
            f"and comparison_status={comparison_status or 'unknown'} still keep deployment blocked. "
            f"realized_closes={proof_closes}; realized_net_usd={proof_net:.2f}; "
            f"option_B={str(option_b.get('verdict') or 'unknown')}."
        ),
        "scope_spread_loss_to_archival_control": True,
    }
    return overrides


def discover_shadow_context_inputs() -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    first_pilot_path = next(iter(sorted(REPORTS.glob(FIRST_PILOT_BOARD_GLOB))), None)
    control_gate_path = next(iter(sorted(REPORTS.glob(CONTROL_GATE_BOARD_GLOB))), None)
    control_state_path = next(iter(sorted(REPORTS.glob(CONTROL_STATE_GLOB))), None)

    source_paths: list[str] = []
    for path in (first_pilot_path, control_state_path, control_gate_path):
        if isinstance(path, Path) and path.exists():
            source_paths.append(display_path(path))

    return (
        load_optional_json(first_pilot_path) if isinstance(first_pilot_path, Path) else None,
        load_optional_json(control_state_path) if isinstance(control_state_path, Path) else None,
        load_optional_json(control_gate_path) if isinstance(control_gate_path, Path) else None,
        source_paths,
    )


def evaluate_symbol(
    symbol: str,
    spread_row: dict[str, Any] | None,
    atr_param_row: dict[str, Any] | None,
    atr_audit_row: dict[str, Any] | None,
    guardrail_row: dict[str, Any] | None,
    readiness_row: dict[str, Any] | None,
    shadow_context_override: dict[str, Any] | None,
) -> dict[str, Any]:
    hard_block_reasons: list[str] = []
    manual_review_reasons: list[str] = []

    spread_status = str((spread_row or {}).get("status") or "unknown")
    effective_spread_status = str((shadow_context_override or {}).get("effective_spread_status") or spread_status)
    guardrail_status = str((guardrail_row or {}).get("status") or "unknown")
    atr_status = str((atr_audit_row or {}).get("status") or "unknown")
    proof_closes = int((readiness_row or {}).get("_proof_closes") or 0)
    ratio_to_atr = effective_step_ratio(atr_param_row or {})
    micro_step = ratio_to_atr > 0 and ratio_to_atr < 0.5
    spread_scope_note = str((shadow_context_override or {}).get("spread_scope_note") or "")
    control_context = str((shadow_context_override or {}).get("control_context") or "")
    scope_spread_loss_to_archival_control = bool(
        (shadow_context_override or {}).get("scope_spread_loss_to_archival_control")
    )

    if spread_status == "SPREAD-LOSS":
        if scope_spread_loss_to_archival_control:
            manual_review_reasons.append("archival_spread_loss_not_current_control")
        else:
            hard_block_reasons.append("spread_loss")
    elif spread_status == "SPREAD-RISK":
        manual_review_reasons.append("spread_risk")

    if guardrail_status in {"blocked_by_guardrail", "uncovered"}:
        hard_block_reasons.append(guardrail_status)
    elif guardrail_status == "contradiction":
        manual_review_reasons.append("guardrail_contradiction")

    if atr_status == "conflict":
        hard_block_reasons.append("atr_regime_conflict")
    elif atr_status == "manual_review_required":
        manual_review_reasons.append("atr_manual_review")

    if micro_step and proof_closes < 20:
        hard_block_reasons.append("micro_step_without_20_forward_closes")
    elif micro_step:
        manual_review_reasons.append("micro_step_even_with_forward_proof")

    if hard_block_reasons:
        verdict = "hard_block"
    elif manual_review_reasons:
        verdict = "manual_review"
    else:
        verdict = "cleared_for_shadow_discussion"

    return {
        "symbol": symbol,
        "deployment_verdict": verdict,
        "spread_status": spread_status,
        "effective_spread_status": effective_spread_status,
        "guardrail_status": guardrail_status,
        "atr_status": atr_status,
        "proof_closes": proof_closes,
        "ratio_to_atr": round(ratio_to_atr, 3) if ratio_to_atr else 0.0,
        "micro_step": micro_step,
        "hard_block_reasons": hard_block_reasons,
        "manual_review_reasons": manual_review_reasons,
        "spread_verdict": str((spread_row or {}).get("verdict") or ""),
        "spread_scope_note": spread_scope_note,
        "guardrail_note": str(((guardrail_row or {}).get("notes") or [""])[0]),
        "atr_note": str((atr_audit_row or {}).get("note") or ""),
        "control_context": control_context,
    }


def build_payload(
    spread_robustness: dict[str, Any],
    atr_params: dict[str, Any],
    atr_audit: dict[str, Any],
    guardrail_audit: dict[str, Any],
    readiness_board: dict[str, Any],
    eth_first_pilot_board: dict[str, Any] | None = None,
    eth_control_state: dict[str, Any] | None = None,
    eth_control_gate: dict[str, Any] | None = None,
    shadow_context_sources: list[str] | None = None,
) -> dict[str, Any]:
    spread_rows = {str(symbol).upper(): dict(data or {}) for symbol, data in dict(spread_robustness).items()}
    atr_param_rows = normalize_rows(atr_params, "symbols")
    atr_audit_rows = normalize_rows(atr_audit, "rows")
    guardrail_rows = normalize_rows(guardrail_audit, "rows")
    readiness_rows = build_readiness_index(readiness_board)
    shadow_context_overrides = build_shadow_context_overrides(eth_first_pilot_board, eth_control_state, eth_control_gate)

    symbols = sorted(set(spread_rows) | set(atr_param_rows) | set(atr_audit_rows) | set(guardrail_rows) | set(readiness_rows))
    rows = [
        evaluate_symbol(
            symbol=symbol,
            spread_row=spread_rows.get(symbol),
            atr_param_row=atr_param_rows.get(symbol),
            atr_audit_row=atr_audit_rows.get(symbol),
            guardrail_row=guardrail_rows.get(symbol),
            readiness_row=readiness_rows.get(symbol),
            shadow_context_override=shadow_context_overrides.get(symbol),
        )
        for symbol in symbols
    ]

    severity_order = {"hard_block": 0, "manual_review": 1, "cleared_for_shadow_discussion": 2}
    rows.sort(key=lambda row: (severity_order.get(str(row["deployment_verdict"]), 9), str(row["symbol"])))

    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict = str(row["deployment_verdict"])
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SPREAD_ROBUSTNESS_PATH.relative_to(ROOT)),
            str(ATR_PARAMS_PATH.relative_to(ROOT)),
            str(ATR_AUDIT_PATH.relative_to(ROOT)),
            str(GUARDRAIL_AUDIT_PATH.relative_to(ROOT)),
            str(READINESS_BOARD_PATH.relative_to(ROOT)),
            *(shadow_context_sources or []),
        ],
        "leadership_read": [
            "Tonight's losses should be turned into hard launch gates, not remembered as lore.",
            "A config should not be discussed for deployment when it is spread-lossing, blocked by current guardrails, or directionally in conflict with the canonical regime surface.",
            "Micro-step shapes below 0.5x ATR need real forward proof before anyone treats them as serious deployment candidates.",
            "Symbol-level trauma has to stay scoped to the geometry that caused it; archival ETH step5 spread loss is not the same thing as a current ETH step14 shadow control.",
        ],
        "safety_rules": [
            "No spread-loss configs.",
            "No blocked or uncovered guardrail symbols.",
            "No ATR/regime conflict promoted blindly.",
            "No micro-step (<0.5x ATR) deployment without 20+ forward shadow closes.",
            "A running shadow control may keep collecting proof even while live deployment stays blocked.",
        ],
        "summary": {
            "symbol_count": len(rows),
            "deployment_verdict_counts": verdict_counts,
            "hard_block_symbols": [row["symbol"] for row in rows if row["deployment_verdict"] == "hard_block"],
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Hungry Hippo Deployment Safety Gate Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: merge spread, ATR, and guardrail truth into one hard deployment gate so bad launches stop hiding behind fragmented surfaces.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Safety Rules", ""])
    for item in list(payload.get("safety_rules") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Symbol count: `{summary.get('symbol_count', 0)}`")
    lines.append(f"- Deployment verdict counts: `{summary.get('deployment_verdict_counts', {})}`")
    lines.append(f"- Hard-block symbols: `{summary.get('hard_block_symbols', [])}`")

    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Symbol | Verdict | Spread View | Guardrail | ATR | Proof Closes | Step/ATR | Why | Context |",
            "|---|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for row in list(payload.get("rows") or []):
        reasons = list(row.get("hard_block_reasons") or []) + list(row.get("manual_review_reasons") or [])
        spread_view = str(row.get("effective_spread_status") or row.get("spread_status") or "unknown")
        raw_spread = str(row.get("spread_status") or "unknown")
        if spread_view != raw_spread:
            spread_view = f"{spread_view} (raw {raw_spread})"
        context_parts = [
            str(row.get("spread_scope_note") or ""),
            str(row.get("control_context") or ""),
        ]
        context = " ".join(part for part in context_parts if part).strip() or "none"
        lines.append(
            f"| {row['symbol']} | `{row['deployment_verdict']}` | `{spread_view}` | "
            f"`{row['guardrail_status']}` | `{row['atr_status']}` | {row['proof_closes']} | {row['ratio_to_atr']} | "
            f"{', '.join(reasons) if reasons else 'none'} | {context} |"
        )

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    first_pilot_board, control_state, control_gate, shadow_context_sources = discover_shadow_context_inputs()
    payload = build_payload(
        load_json(SPREAD_ROBUSTNESS_PATH),
        load_json(ATR_PARAMS_PATH),
        load_json(ATR_AUDIT_PATH),
        load_json(GUARDRAIL_AUDIT_PATH),
        load_json(READINESS_BOARD_PATH),
        first_pilot_board,
        control_state,
        control_gate,
        shadow_context_sources,
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
