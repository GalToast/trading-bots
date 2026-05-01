#!/usr/bin/env python3
"""Audit hungry_hippo_atr_step_params against the canonical regime signal surface."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ATR_PARAMS_PATH = ROOT / "reports" / "hungry_hippo_atr_step_params.json"
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
OUTPUT_JSON = ROOT / "reports" / "hungry_hippo_atr_step_audit.json"
OUTPUT_MD = ROOT / "reports" / "hungry_hippo_atr_step_audit.md"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def asymmetry_direction(row: dict[str, Any]) -> str:
    step_buy = float(row.get("step_buy") or 0.0)
    step_sell = float(row.get("step_sell") or 0.0)
    if step_buy > step_sell:
        return "BUY"
    if step_sell > step_buy:
        return "SELL"
    return "NEUTRAL"


def evaluate_row(step_row: dict[str, Any], regime_row: dict[str, Any]) -> dict[str, Any]:
    action_bias = str(regime_row.get("action_bias") or "NEUTRAL")
    control_mode = str(regime_row.get("control_mode") or "")
    asym_bias = asymmetry_direction(step_row)
    ratio = float(step_row.get("asymmetry_ratio") or 1.0)

    status = "aligned"
    note = "ATR step output is directionally compatible with the regime signal."

    if action_bias == "NEUTRAL":
        if ratio > 1.05:
            status = "manual_review_required"
            note = "Regime signal is neutral/wait, but ATR surface is already directional."
    elif action_bias != asym_bias and asym_bias == "NEUTRAL":
        status = "manual_review_required"
        note = "Regime signal is directional, but ATR surface stays symmetric."
    elif action_bias != asym_bias:
        status = "conflict"
        note = "ATR surface biases the opposite side from the canonical regime signal."
    elif control_mode == "breakout_follow" and ratio <= 1.05:
        status = "manual_review_required"
        note = "Breakout-follow read is aligned, but the ATR surface stays symmetric."
    elif control_mode == "bounce_reversal" and ratio <= 1.05:
        status = "manual_review_required"
        note = "Bounce-reversal read is aligned, but the ATR surface does not tighten the reversal side."

    return {
        "symbol": step_row["symbol"],
        "regime": step_row["regime"],
        "action_bias": action_bias,
        "control_mode": control_mode,
        "atr_asymmetry_direction": asym_bias,
        "asymmetry_ratio": ratio,
        "step": float(step_row.get("step") or 0.0),
        "step_buy": float(step_row.get("step_buy") or 0.0),
        "step_sell": float(step_row.get("step_sell") or 0.0),
        "session_weight": float(step_row.get("session_weight") or 0.0),
        "status": status,
        "note": note,
    }


def build_payload() -> dict[str, Any]:
    atr_payload = load_json(ATR_PARAMS_PATH)
    regime_payload = load_json(REGIME_SIGNAL_PATH)
    regime_rows = {str(row.get("symbol") or "").upper(): row for row in regime_payload.get("rows", [])}

    rows = []
    for step_row in atr_payload.get("symbols", []):
        symbol = str(step_row.get("symbol") or "").upper()
        regime_row = regime_rows.get(symbol)
        if not regime_row:
            continue
        rows.append(evaluate_row(step_row, regime_row))

    summary = {
        "symbol_count": len(rows),
        "status_counts": {
            status: sum(1 for row in rows if row["status"] == status)
            for status in sorted({row["status"] for row in rows})
        },
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "rows": rows,
        "headline_findings": [
            next((row for row in rows if row["symbol"] == "BTCUSD"), {}),
            next((row for row in rows if row["symbol"] == "ETHUSD"), {}),
            next((row for row in rows if row["symbol"] == "NAS100"), {}),
        ],
        "notes": [
            "This audit prevents the ATR-scaled step surface from being integrated as if it already encoded the canonical regime signal.",
            "Rows marked conflict or manual_review_required should not be promoted blindly into controller defaults.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Hungry Hippo ATR Step Audit",
        "",
        "This surface audits the new ATR-scaled step output against the canonical regime signal.",
        "",
        "## Current Read",
        "",
        f"- symbols: `{payload['summary']['symbol_count']}`",
        f"- status counts: `{payload['summary']['status_counts']}`",
        "",
        "## Rows",
        "",
        "| Symbol | Action | Control Mode | ATR Bias | Ratio | Status | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['symbol']} | {row['action_bias']} | {row['control_mode']} | {row['atr_asymmetry_direction']} | "
            f"{row['asymmetry_ratio']} | `{row['status']}` | {row['note']} |"
        )

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
