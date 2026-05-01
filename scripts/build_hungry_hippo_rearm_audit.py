#!/usr/bin/env python3
"""Audit hungry_hippo_rearm_params against canonical control surfaces."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import hungry_hippo_rearm as rearm


ROOT = Path(__file__).resolve().parent.parent
REARM_PARAMS_PATH = ROOT / "reports" / "hungry_hippo_rearm_params.json"
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
SESSION_TABLE_PATH = ROOT / "reports" / "session_regime_step_table_v2.json"
RECONCILED_STEPS_PATH = ROOT / "reports" / "hungry_hippo_reconciled_steps.json"
BTC_HANDOFF_PATH = ROOT / "reports" / "btc_downtrend_handoff.json"
OUTPUT_JSON = ROOT / "reports" / "hungry_hippo_rearm_audit.json"
OUTPUT_MD = ROOT / "reports" / "hungry_hippo_rearm_audit.md"


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_window_hours(window: str | None) -> set[int] | None:
    if not window or window == "None":
        return None

    hours: set[int] = set()
    for segment in window.split("+"):
        start_text, end_text = segment.split("-", 1)
        start_hour = int(start_text.split(":", 1)[0])
        end_hour = int(end_text.split(":", 1)[0])
        hours.update(range(start_hour, end_hour))
    return hours


def representative_active_hour(session_meta: dict[str, Any], fallback: int) -> int:
    peak_hour = session_meta.get("peak_hour")
    if peak_hour is not None:
        return int(peak_hour)

    hours = parse_window_hours(str(session_meta.get("window") or ""))
    if hours:
        return min(hours)

    return fallback


def find_regime_row(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    return None


def find_reconciled_row(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    return dict((payload.get("symbols") or {}).get(symbol) or {})


def select_scenario(rows: list[dict[str, Any]], kill_reason: str, consecutive_failures: int = 0) -> dict[str, Any]:
    for row in rows:
        failure_count = row.get("consecutive_failures")
        if failure_count is None:
            continue
        if row.get("kill_reason") == kill_reason and int(failure_count) == consecutive_failures:
            return row
    raise KeyError(f"Missing scenario {kill_reason}/{consecutive_failures}")


def severity(status: str) -> int:
    order = {"aligned": 0, "manual_review_required": 1, "conflict": 2}
    return order[status]


def evaluate_symbol(
    symbol: str,
    current_row: dict[str, Any],
    scenario_rows: list[dict[str, Any]],
    regime_row: dict[str, Any],
    session_meta: dict[str, Any],
    reconciled_row: dict[str, Any],
    btc_handoff: dict[str, Any],
    current_hour_utc: int,
) -> dict[str, Any]:
    session_window = str(session_meta.get("window") or "None")
    parsed_hours = parse_window_hours(session_window)
    empirical_active_now = True if parsed_hours is None else current_hour_utc in parsed_hours
    session_status = "aligned"
    notes: list[str] = []

    if bool(current_row.get("is_active_hour")) != empirical_active_now:
        session_status = "conflict"
        notes.append(
            f"Rearmer treats hour `{current_hour_utc}` as active, but empirical session window `{session_window}` does not."
        )

    peak_hour = representative_active_hour(session_meta, current_hour_utc)
    warm_history = [2.0, 3.0, 1.5]
    projected_manual = rearm.compute_rearm_params(symbol, "manual_kill", 0, peak_hour, warm_history)
    projected_regime_mismatch = rearm.compute_rearm_params(symbol, "regime_mismatch", 0, peak_hour, warm_history)
    projected_session_end = rearm.compute_rearm_params(symbol, "session_end", 0, peak_hour, warm_history)

    control_mode = str(regime_row.get("control_mode") or "")
    action_bias = str(regime_row.get("action_bias") or "NEUTRAL")
    control_status = "aligned"

    if control_mode in {"wait_extreme_confirmation", "mixed_hold"}:
        projected_any_rearm = any(
            bool(row.get("should_rearm_now"))
            for row in (projected_manual, projected_regime_mismatch, projected_session_end)
        )
        if projected_any_rearm:
            control_status = "conflict"
            notes.append(
                f"Control mode `{control_mode}` is a wait/hold state, but active-hour rearm policy still allows immediate reinjection."
            )

    hold_gate = dict(btc_handoff.get("hold_gate") or {})
    hold_gate_status = "aligned"
    if symbol == "BTCUSD" and hold_gate.get("deploy_decision") == "hold_current_bullish_shape":
        if int(projected_regime_mismatch.get("max_injections") or 0) > 0:
            hold_gate_status = "conflict"
            notes.append(
                "BTC hold gate says not to re-promote the bullish runtime while action_bias stays SELL, "
                f"but regime-mismatch rearm still permits `{projected_regime_mismatch['max_injections']}` reinjection on "
                f"`{projected_regime_mismatch['rearm_variant']}`."
            )

    overall_status = "aligned"
    for candidate in (session_status, control_status, hold_gate_status):
        if severity(candidate) > severity(overall_status):
            overall_status = candidate

    if not notes:
        notes.append("Current rearm surface is directionally compatible with the canonical control read.")

    current_regime_mismatch = select_scenario(scenario_rows, "regime_mismatch")
    current_session_end = select_scenario(scenario_rows, "session_end")

    return {
        "symbol": symbol,
        "control_mode": control_mode,
        "action_bias": action_bias,
        "consensus": str(regime_row.get("consensus") or ""),
        "session_window": session_window,
        "empirical_active_now": empirical_active_now,
        "rearm_active_now": bool(current_row.get("is_active_hour")),
        "current_should_rearm_now": bool(current_row.get("should_rearm_now")),
        "current_cooldown_seconds": float(current_row.get("cooldown_seconds") or 0.0),
        "current_rearm_variant": str(current_row.get("rearm_variant") or ""),
        "current_max_injections": int(current_row.get("max_injections") or 0),
        "current_guardrail_status": str(current_row.get("canonical_guardrail_status") or ""),
        "current_auto_rearm_allowed": bool(current_row.get("auto_rearm_allowed")),
        "projected_active_hour_utc": peak_hour,
        "projected_manual_kill": {
            "should_rearm_now": bool(projected_manual.get("should_rearm_now")),
            "cooldown_seconds": float(projected_manual.get("cooldown_seconds") or 0.0),
            "rearm_variant": str(projected_manual.get("rearm_variant") or ""),
            "max_injections": int(projected_manual.get("max_injections") or 0),
        },
        "projected_regime_mismatch": {
            "should_rearm_now": bool(projected_regime_mismatch.get("should_rearm_now")),
            "cooldown_seconds": float(projected_regime_mismatch.get("cooldown_seconds") or 0.0),
            "rearm_variant": str(projected_regime_mismatch.get("rearm_variant") or ""),
            "max_injections": int(projected_regime_mismatch.get("max_injections") or 0),
        },
        "current_regime_mismatch": {
            "should_rearm_now": bool(current_regime_mismatch.get("should_rearm_now")),
            "cooldown_seconds": float(current_regime_mismatch.get("cooldown_seconds") or 0.0),
            "rearm_variant": str(current_regime_mismatch.get("rearm_variant") or ""),
            "max_injections": int(current_regime_mismatch.get("max_injections") or 0),
        },
        "current_session_end": {
            "should_rearm_now": bool(current_session_end.get("should_rearm_now")),
            "cooldown_seconds": float(current_session_end.get("cooldown_seconds") or 0.0),
            "rearm_variant": str(current_session_end.get("rearm_variant") or ""),
            "max_injections": int(current_session_end.get("max_injections") or 0),
        },
        "reconciled_weights": {
            "buy_weight": float(reconciled_row.get("buy_weight") or 0.0),
            "sell_weight": float(reconciled_row.get("sell_weight") or 0.0),
            "regime": str(reconciled_row.get("regime") or ""),
        },
        "session_status": session_status,
        "control_status": control_status,
        "hold_gate_status": hold_gate_status,
        "overall_status": overall_status,
        "notes": notes,
    }


def build_payload() -> dict[str, Any]:
    rearm_payload = load_json(REARM_PARAMS_PATH)
    regime_payload = load_json(REGIME_SIGNAL_PATH)
    session_table = load_json(SESSION_TABLE_PATH)
    reconciled_steps = load_json(RECONCILED_STEPS_PATH)
    btc_handoff = load_json(BTC_HANDOFF_PATH)

    current_hour_utc = int(rearm_payload.get("current_hour_utc") or 0)
    current_state = dict(rearm_payload.get("current_state_rearm_params") or {})
    scenario_matrix = dict(rearm_payload.get("scenario_matrix") or {})
    session_windows = dict((session_table.get("session_windows") or {}))
    guardrail_metadata = dict(rearm_payload.get("guardrail_metadata") or {})

    rows = []
    uncovered_symbols = []
    for symbol, current_row in current_state.items():
        regime_row = find_regime_row(regime_payload, symbol)
        if regime_row is None:
            uncovered_symbols.append(symbol)
            continue
        scenario_rows = list(scenario_matrix.get(symbol) or [])
        rows.append(
            evaluate_symbol(
                symbol=symbol,
                current_row=current_row,
                scenario_rows=scenario_rows,
                regime_row=regime_row,
                session_meta=dict(session_windows.get(symbol) or {}),
                reconciled_row=find_reconciled_row(reconciled_steps, symbol),
                btc_handoff=dict(btc_handoff or {}),
                current_hour_utc=current_hour_utc,
            )
        )

    summary = {
        "symbol_count": len(rows),
        "status_counts": {
            status: sum(1 for row in rows if row["overall_status"] == status)
            for status in sorted({row["overall_status"] for row in rows})
        },
        "uncovered_symbol_count": len(uncovered_symbols),
    }

    headline_symbols = ["BTCUSD", "XAUUSD", "NZDUSD", "GBPUSD"]
    headline_findings = [row for symbol in headline_symbols for row in rows if row["symbol"] == symbol]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {
            "rearm_params": str(REARM_PARAMS_PATH.relative_to(ROOT)),
            "regime_signal": str(REGIME_SIGNAL_PATH.relative_to(ROOT)),
            "session_table": str(SESSION_TABLE_PATH.relative_to(ROOT)),
            "reconciled_steps": str(RECONCILED_STEPS_PATH.relative_to(ROOT)),
            "btc_downtrend_handoff": str(BTC_HANDOFF_PATH.relative_to(ROOT)),
        },
        "summary": summary,
        "guardrail_summary": guardrail_metadata,
        "rows": rows,
        "headline_findings": headline_findings,
        "uncovered_symbols": uncovered_symbols,
        "notes": [
            "Aligned here means the rearm surface now matches canonical control truth, not that every symbol is eligible for auto-rearm.",
            "Use current_guardrail_status and auto_rearm_allowed to decide whether a row can relaunch automatically.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Hungry Hippo Rearm Audit",
        "",
        "This surface audits the rearm policy against the canonical control read, empirical session windows, and the BTC hold gate.",
        "",
        "## Current Read",
        "",
        f"- symbols: `{payload['summary']['symbol_count']}`",
        f"- status counts: `{payload['summary']['status_counts']}`",
        f"- uncovered symbols: `{payload['uncovered_symbols']}`",
        f"- current guardrail counts: `{payload['guardrail_summary'].get('current_state_status_counts', {})}`",
        "",
        "## Rows",
        "",
        "| Symbol | Control | Bias | Guardrail | Auto Rearm | Rearm Active Now | Empirical Active Now | Overall | Key Note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for row in payload["rows"]:
        lines.append(
            f"| {row['symbol']} | {row['control_mode']} | {row['action_bias']} | `{row['current_guardrail_status']}` | "
            f"{row['current_auto_rearm_allowed']} | {row['rearm_active_now']} | {row['empirical_active_now']} | "
            f"`{row['overall_status']}` | {row['notes'][0]} |"
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
