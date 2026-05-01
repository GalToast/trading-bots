#!/usr/bin/env python3
"""Build a concrete BTC downtrend handoff surface from current adaptive artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
BTC_AUDIT_PATH = ROOT / "reports" / "btc_adaptive_runtime_audit.json"
DESIGN_VALIDATION_PATH = ROOT / "reports" / "design_spec_validation.json"
MTF_FLIP_PATH = ROOT / "reports" / "mtf_regime_flip_analysis.json"
OUTPUT_JSON = ROOT / "reports" / "btc_downtrend_handoff.json"
OUTPUT_MD = ROOT / "reports" / "btc_downtrend_handoff.md"


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_regime_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    raise KeyError(f"Missing regime signal row for {symbol}")


def find_validation_shape(payload: dict[str, Any], shape_name: str) -> dict[str, Any]:
    for row in list(payload.get("shapes") or []):
        if str(row.get("shape") or "") == shape_name:
            return row
    raise KeyError(f"Missing design validation shape: {shape_name}")


def find_flip_row(payload: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    for row in payload:
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    raise KeyError(f"Missing MTF flip row for {symbol}")


def build_payload() -> dict[str, Any]:
    regime_signal = load_json(REGIME_SIGNAL_PATH)
    btc_audit = load_json(BTC_AUDIT_PATH)
    design_validation = load_json(DESIGN_VALIDATION_PATH)
    mtf_flip = load_json(MTF_FLIP_PATH)

    btc_signal = find_regime_row(regime_signal, "BTCUSD")
    btc_validation = find_validation_shape(design_validation, "btc_m15_aggressive")
    btc_flip = find_flip_row(mtf_flip, "BTCUSD")
    runtime = dict((btc_audit or {}).get("runtime_lane") or {})

    proposed = {
        "shape_id": "btcusd_m15_bounce_down_v1",
        "family": "raw",
        "timeframe": "M15",
        "regime_target": "DOWNTREND",
        "entry_bias": "SELL",
        "buy_step_coeff": float(btc_signal.get("buy_step_coeff") or 0.0),
        "sell_step_coeff": float(btc_signal.get("sell_step_coeff") or 0.0),
        "computed_buy_step": float(btc_signal.get("computed_buy_step") or 0.0),
        "computed_sell_step": float(btc_signal.get("computed_sell_step") or 0.0),
        "alpha": float(btc_signal.get("alpha") or 0.0),
        "raw_close_style": "all_profitable",
        "sell_gap": 1,
        "buy_gap": 1,
        "rearm_variant": str(runtime.get("raw_rearm_variant") or "rearm_lvl2_exc1"),
        "max_open_per_side": int(runtime.get("max_open_per_side") or 6),
        "posture": "shadow_only_candidate",
    }

    hold_gate = {
        "current_runtime_lane": str(runtime.get("lane_name") or ""),
        "current_runtime_posture": "hybrid_runtime_under_review",
        "deploy_decision": "hold_current_bullish_shape",
        "release_conditions": [
            "Do not promote or widen the current bullish adaptive runtime while regime_signal says BTC action_bias=SELL.",
            "Only consider replacing geometry after a shadow-only downtrend candidate produces fresh clean closes.",
            "If BTC regime_signal realigns to BUY trend-follow or breakout-follow, rerun the runtime audit before any bullish promotion.",
        ],
    }

    completion_read = (
        "The next BTC action is no longer 'build any adaptive runner'. "
        "It is either to hold the current hybrid bullish runtime in review, or to build and shadow-proof a dedicated "
        "downtrend-aware BTC M15 candidate with sell-tight geometry and alpha=0.3."
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "handoff_ready",
        "summary": {
            "completion_read": completion_read,
            "regime_signal_read": {
                "control_mode": btc_signal["control_mode"],
                "action_bias": btc_signal["action_bias"],
                "consensus": btc_signal["consensus"],
            },
            "runtime_read": {
                "lane_name": runtime.get("lane_name"),
                "step": runtime.get("step"),
                "alpha": runtime.get("raw_close_alpha"),
                "max_open_per_side": runtime.get("max_open_per_side"),
            },
        },
        "inputs": {
            "regime_signal": str(REGIME_SIGNAL_PATH.relative_to(ROOT)),
            "btc_runtime_audit": str(BTC_AUDIT_PATH.relative_to(ROOT)),
            "design_spec_validation": str(DESIGN_VALIDATION_PATH.relative_to(ROOT)),
            "mtf_regime_flip_analysis": str(MTF_FLIP_PATH.relative_to(ROOT)),
        },
        "current_truth": {
            "regime_signal": btc_signal,
            "design_validation": btc_validation,
            "mtf_flip_analysis": btc_flip,
        },
        "proposed_downtrend_shape": proposed,
        "hold_gate": hold_gate,
        "notes": [
            "This is a handoff artifact only. It does not edit the running BTC lane or write into the adaptive shape library.",
            "The proposed candidate intentionally stays shadow-only until the regime conflict resolves through fresh forward proof.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    proposed = payload["proposed_downtrend_shape"]
    summary = payload["summary"]
    lines = [
        "# BTC Downtrend Handoff",
        "",
        "This artifact turns the current BTC HOLD verdict into a concrete next-build handoff.",
        "",
        "## Current Read",
        "",
        f"- status: `{payload['status']}`",
        f"- completion read: {summary['completion_read']}",
        f"- current control mode: `{summary['regime_signal_read']['control_mode']}`",
        f"- current action bias: `{summary['regime_signal_read']['action_bias']}`",
        f"- current runtime lane: `{summary['runtime_read']['lane_name']}`",
        "",
        "## Proposed Shadow Candidate",
        "",
        f"- shape id: `{proposed['shape_id']}`",
        f"- regime target: `{proposed['regime_target']}`",
        f"- entry bias: `{proposed['entry_bias']}`",
        f"- buy coeff / sell coeff: `{proposed['buy_step_coeff']}` / `{proposed['sell_step_coeff']}`",
        f"- computed buy / sell step: `{proposed['computed_buy_step']}` / `{proposed['computed_sell_step']}`",
        f"- alpha: `{proposed['alpha']}`",
        f"- max_open_per_side: `{proposed['max_open_per_side']}`",
        f"- posture: `{proposed['posture']}`",
        "",
        "## Hold Gate",
        "",
    ]
    for item in payload["hold_gate"]["release_conditions"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Notes", ""])
    for item in payload["notes"]:
        lines.append(f"- {item}")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
