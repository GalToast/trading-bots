#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

RECON_PATH = REPORTS / "btc_downtrend_config_reconciliation_board.json"
PROMOTION_GATE_PATH = REPORTS / "shadow_to_live_promotion_gate_board.json"
RUBRIC_PATH = REPORTS / "graduation_rubric_board.json"

OUTPUT_JSON_PATH = REPORTS / "btc_downtrend_override_decision_board.json"
OUTPUT_MD_PATH = REPORTS / "btc_downtrend_override_decision_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def gate_row(payload: dict[str, Any], candidate: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("candidate") or "") == candidate:
            return row
    raise KeyError(f"candidate not found: {candidate}")


def rubric_row(payload: dict[str, Any], candidate: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("candidate") or "") == candidate:
            return row
    raise KeyError(f"candidate not found: {candidate}")


def field_map(recon: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("field") or ""): row for row in list(recon.get("comparisons") or []) if isinstance(row, dict)}


def build_payload(recon: dict[str, Any], promotion_gate: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    btc_gate = gate_row(promotion_gate, "BTCUSD M15 sell-tight downtrend shape")
    btc_rubric = rubric_row(rubric, "BTCUSD M15 sell-tight downtrend shape")
    fields = field_map(recon)

    conform_option = {
        "option": "conform_to_handoff",
        "what_changes": {
            "enabled": False,
            "max_open_per_side": 6,
            "rearm_variant": "rearm_lvl2_exc1",
            "step_buy": fields["step_buy"]["handoff"],
            "step_sell": fields["step_sell"]["handoff"],
        },
        "benefits": [
            "restores one canonical truth across handoff, readiness, promotion, and rubric surfaces",
            "keeps the proof candidate conservative while it is still pre-forward-proof",
            "makes shadow-to-live gates simpler because config and governance no longer disagree",
        ],
        "costs": [
            "throws away the stronger current runtime posture unless that choice is reintroduced later with evidence",
            "may reduce opportunity capture if the stronger settings were intentional and superior",
        ],
    }

    ratify_option = {
        "option": "ratify_current_override",
        "what_changes": {
            "handoff_or_governance_target": {
                "enabled": True,
                "max_open_per_side": fields["max_open_per_side"]["config"],
                "rearm_variant": fields["rearm_variant"]["config"],
                "step_buy": fields["step_buy"]["config"],
                "step_sell": fields["step_sell"]["config"],
            }
        },
        "benefits": [
            "preserves the stronger current config if the room believes it is a deliberate improvement",
            "avoids reconfiguring the candidate backward before proof",
        ],
        "costs": [
            "requires explicit governance updates across the handoff and downstream boards",
            "raises the burden of proof because the repo would be ratifying a more aggressive candidate than the handoff intended",
            "keeps enabled=true in a surface that governance has been treating as shadow-proof-first",
        ],
    }

    recommendation = {
        "preferred_option": "conform_to_handoff",
        "why": [
            "The candidate is still in a `reconcile_shadow_then_judge` stage, not a forward-validated stage.",
            "The current mismatches are on risk-bearing controls rather than just cosmetic fields.",
            "Canonical governance should stay conservative until evidence justifies the override.",
        ],
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(RECON_PATH.relative_to(ROOT)),
            str(PROMOTION_GATE_PATH.relative_to(ROOT)),
            str(RUBRIC_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The BTC sell-tight candidate is now a decision problem, not a discovery problem.",
            "There are two coherent paths: conform the config to the handoff, or ratify the stronger current config as an override.",
            "Because the candidate is still pre-proof, conservative canonicalization is the cleaner default unless the room explicitly wants the stronger version.",
        ],
        "current_truth": {
            "reconciliation_status": str((recon.get("summary") or {}).get("status") or ""),
            "promotion_verdict": str(btc_gate.get("promotion_verdict") or ""),
            "current_stage": str(btc_gate.get("current_stage") or ""),
            "required_config_state": str((btc_rubric.get("shadow_to_live_rubric") or {}).get("required_config_state") or ""),
        },
        "decision_options": [conform_option, ratify_option],
        "recommendation": recommendation,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    current_truth = dict(payload.get("current_truth") or {})
    lines = [
        "# BTC Downtrend Override Decision Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: convert the BTC sell-tight reconciliation into an explicit decision between conforming to the handoff and ratifying the stronger current config.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Current Truth", ""])
    for key, value in current_truth.items():
        lines.append(f"- {key}: `{value}`")

    lines.extend(["", "## Decision Options", ""])
    for option in list(payload.get("decision_options") or []):
        lines.append(f"### {option['option']}")
        lines.append("")
        if "what_changes" in option:
            changes = option["what_changes"]
            if isinstance(changes, dict):
                rendered = "; ".join(f"{k}={v}" for k, v in changes.items())
                lines.append(f"- What changes: `{rendered}`")
        for key in ("benefits", "costs"):
            values = list(option.get(key) or [])
            if values:
                lines.append(f"- {key.capitalize()}: `{'; '.join(values)}`")
        lines.append("")

    recommendation = dict(payload.get("recommendation") or {})
    lines.extend(["## Recommendation", ""])
    lines.append(f"- Preferred option: `{recommendation.get('preferred_option', '')}`")
    lines.append(f"- Why: `{'; '.join(list(recommendation.get('why') or []))}`")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(RECON_PATH),
        load_json(PROMOTION_GATE_PATH),
        load_json(RUBRIC_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
