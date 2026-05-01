#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

INCUMBENT_STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
BURST_BOARD_PATH = REPORTS / "burst_expansion_prevention_board.json"
SPREAD_BOARD_PATH = REPORTS / "spread_escape_threshold_board.json"
PREVENTION_ESCAPE_PATH = REPORTS / "prevention_escape_impact_board.json"
OUTPUT_JSON_PATH = REPORTS / "guarded_toxic_flow_contract_board.json"
OUTPUT_MD_PATH = REPORTS / "guarded_toxic_flow_contract_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return load_json(path)


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        text = raw_line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def display_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def symbol_burst_rows(symbol: str, burst_board: dict[str, Any]) -> list[dict[str, Any]]:
    symbol_key = str(symbol or "").lower()
    return [
        dict(row)
        for row in list(burst_board.get("lanes") or [])
        if symbol_key and symbol_key in str(row.get("lane") or "").lower()
    ]


def symbol_spread_row(symbol: str, spread_board: dict[str, Any]) -> dict[str, Any]:
    for row in list(spread_board.get("symbols") or []):
        if str(row.get("symbol") or "") == symbol:
            return dict(row)
    return {}


def symbol_cluster_rows(symbol: str, prevention_escape: dict[str, Any]) -> list[dict[str, Any]]:
    symbol_key = str(symbol or "").lower()
    return [
        dict(row)
        for row in list(prevention_escape.get("lanes") or [])
        if symbol_key and symbol_key in str(row.get("lane") or "").lower()
    ]


def lane_artifact_paths(lane_name: str) -> tuple[Path | None, Path | None]:
    lane = str(lane_name or "").strip()
    if not lane:
        return None, None
    return (
        REPORTS / f"penetration_lattice_{lane}_state.json",
        REPORTS / f"penetration_lattice_{lane}_events.jsonl",
    )


def inspect_guard_open_runtime_evidence(study_row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(study_row.get("symbol") or "")
    lane_name = str(study_row.get("adaptive_lane") or "")
    runtime_overlays = [str(item or "") for item in list(study_row.get("adaptive_runtime_overlays") or [])]
    guard_requested = "guard_open_admission" in runtime_overlays
    state_path, event_path = lane_artifact_paths(lane_name)
    state_payload = load_json_if_exists(state_path)
    event_rows = load_jsonl(event_path)

    metadata = dict(state_payload.get("metadata") or {})
    symbol_state = dict(dict(state_payload.get("symbols") or {}).get(symbol) or {})
    guard_enabled = metadata.get("guard_open_admission")
    if guard_enabled is None and "guard_open_admission" in symbol_state:
        guard_enabled = symbol_state.get("guard_open_admission")
    if guard_enabled is not None:
        guard_enabled = bool(guard_enabled)

    guarded_events = [
        row for row in event_rows if str(row.get("action") or "") == "open_guarded_admission"
    ]
    guarded_event_count = len(guarded_events)
    latest_guarded_event = dict(guarded_events[-1]) if guarded_events else {}
    latest_event_ts = ""
    for row in reversed(event_rows):
        ts_utc = str(row.get("ts_utc") or "")
        if ts_utc:
            latest_event_ts = ts_utc
            break

    artifact_visible = bool(state_payload) or bool(event_rows)
    if not guard_requested:
        verdict = "guard_not_requested"
        read = "This row does not currently request `guard_open_admission`."
    elif guarded_event_count > 0:
        verdict = "guarded_open_observed"
        read = (
            f"Runtime evidence exists for `{lane_name}`: `open_guarded_admission` fired "
            f"`{guarded_event_count}` time(s), most recently at "
            f"`{latest_guarded_event.get('ts_utc', '')}` during stage "
            f"`{latest_guarded_event.get('stage', '') or 'n/a'}`."
        )
    elif guard_enabled is True:
        verdict = "guard_enabled_waiting_trigger"
        read = (
            f"`{lane_name}` exposes `guard_open_admission=true` in the current artifact, "
            "but no `open_guarded_admission` event has fired yet."
        )
    elif guard_enabled is False:
        verdict = "runtime_explicitly_not_guarded"
        read = (
            f"`{lane_name}` is currently running with `guard_open_admission=false`, so the "
            "checked-in artifact is not yet honoring the guarded-open contract."
        )
    elif artifact_visible:
        verdict = "runtime_visibility_missing"
        read = (
            f"`{lane_name}` has a visible artifact, but the current state/event files do not expose "
            "`guard_open_admission` or any `open_guarded_admission` event yet."
        )
    else:
        verdict = "no_runtime_artifact"
        read = (
            f"No runtime artifact is currently visible for `{lane_name}`, so guarded-open remains a "
            "passive contract rather than an observed runtime behavior."
        )

    return {
        "verdict": verdict,
        "guard_requested": guard_requested,
        "guard_open_admission_enabled": guard_enabled,
        "guarded_admission_event_count": guarded_event_count,
        "latest_guarded_admission_ts_utc": str(latest_guarded_event.get("ts_utc") or ""),
        "latest_guarded_stage": str(latest_guarded_event.get("stage") or ""),
        "latest_event_ts_utc": latest_event_ts,
        "state_path": display_path(state_path),
        "state_path_present": bool(state_path and state_path.exists()),
        "event_path": display_path(event_path),
        "event_path_present": bool(event_path and event_path.exists()),
        "read": read,
    }


def build_contract_row(
    *,
    study_row: dict[str, Any],
    burst_board: dict[str, Any],
    spread_board: dict[str, Any],
    prevention_escape: dict[str, Any],
) -> dict[str, Any]:
    symbol = str(study_row.get("symbol") or "")
    burst_rows = symbol_burst_rows(symbol, burst_board)
    spread_row = symbol_spread_row(symbol, spread_board)
    cluster_rows = symbol_cluster_rows(symbol, prevention_escape)
    runtime_evidence = inspect_guard_open_runtime_evidence(study_row)

    burst_opens = sum(parse_int(row.get("burst_expansion_opens")) for row in burst_rows)
    burst_escapes = sum(parse_int(row.get("burst_expansion_escapes")) for row in burst_rows)
    burst_pnl = round(sum(parse_float(row.get("burst_expansion_pnl")) for row in burst_rows), 2)
    max_burst_escape_rate = max((parse_float(row.get("burst_escape_rate")) for row in burst_rows), default=0.0)
    max_non_burst_escape_rate = max((parse_float(row.get("non_burst_escape_rate")) for row in burst_rows), default=0.0)

    escapes_above_2x = parse_int(spread_row.get("escapes_above_2x"))
    median_spread = parse_float(spread_row.get("median_spread"))
    median_escape_spread = parse_float(spread_row.get("median_escape_spread"), default=-1.0)
    has_spread_evidence = bool(spread_row)
    spread_gate_demoted = has_spread_evidence and escapes_above_2x == 0
    normal_spread_escapes = has_spread_evidence and median_escape_spread >= 0 and abs(median_escape_spread - median_spread) < 1e-9

    total_cluster_savable = round(abs(parse_float(prevention_escape.get("total_cluster_savable"))), 2)
    total_prevention_savable = round(abs(parse_float(prevention_escape.get("total_prevention_savable"))), 2)
    symbol_cluster_savable = round(sum(abs(parse_float(row.get("cluster_escape_pnl"))) for row in cluster_rows), 2)
    cluster_escape_promoted = total_cluster_savable > 0 and total_cluster_savable > total_prevention_savable

    summary = dict(burst_board.get("summary") or {})
    step_widening_supported = any(
        parse_int(summary.get(field)) > 0 for field in ("prevent_with_2x_step", "prevent_with_3x_step", "prevent_with_5x_step")
    )

    spread_verdict = "demoted_as_primary_guard" if spread_gate_demoted else "unresolved"
    if has_spread_evidence and not spread_gate_demoted and escapes_above_2x > 0:
        spread_verdict = "threshold_gate_has_support"

    burst_verdict = "regime_guard_required" if burst_opens > 0 and burst_escapes > 0 else "insufficient_symbol_burst_evidence"
    cluster_verdict = "promote_cluster_escape" if cluster_escape_promoted else "insufficient_cluster_escape_evidence"
    step_verdict = "unproven_from_checked_in_board" if not step_widening_supported else "widening_has_checked_in_support"

    contract_verdict = "cluster_escape_primary_spread_demoted"
    if cluster_verdict != "promote_cluster_escape":
        contract_verdict = "guarded_toxic_flow_contract_incomplete"
    elif spread_verdict != "demoted_as_primary_guard":
        contract_verdict = "cluster_escape_primary_spread_unresolved"

    spread_read = "No spread-threshold row exists for this symbol yet."
    if has_spread_evidence:
        spread_read = (
            f"`{symbol}` has median spread `{median_spread}` and median escape spread "
            f"`{median_escape_spread if median_escape_spread >= 0 else 'n/a'}` with "
            f"`escapes_above_2x={escapes_above_2x}`."
        )
        if normal_spread_escapes and spread_gate_demoted:
            spread_read += " Escapes are happening at normal spread, so spread gating should not be the primary guard."

    burst_read = "No burst-expansion lane row exists for this symbol yet."
    if burst_rows:
        burst_read = (
            f"`{symbol}` burst rows show `{burst_opens}` burst-expansion opens, `{burst_escapes}` burst escapes, "
            f"burst P/L `{burst_pnl:+.2f}`, and max burst escape rate `{max_burst_escape_rate:.3f}` "
            f"vs max non-burst escape rate `{max_non_burst_escape_rate:.3f}`."
        )

    cluster_read = (
        f"Checked-in prevention/escape evidence currently shows `cluster_savable={total_cluster_savable}` "
        f"vs `prevention_savable={total_prevention_savable}`."
    )
    if symbol_cluster_savable > 0:
        cluster_read += f" Direct symbol-matched cluster support contributes `{symbol_cluster_savable}`."
    elif cluster_escape_promoted:
        cluster_read += " Direct symbol-matched cluster evidence is absent, so the contract relies on cross-lane doctrine support."

    step_read = (
        f"Current burst-prevention board reports `prevent_with_2x_step={parse_int(summary.get('prevent_with_2x_step'))}`, "
        f"`prevent_with_3x_step={parse_int(summary.get('prevent_with_3x_step'))}`, and "
        f"`prevent_with_5x_step={parse_int(summary.get('prevent_with_5x_step'))}`."
    )
    if not step_widening_supported:
        step_read += " Treat step widening as a secondary hypothesis until checked-in prevention evidence actually supports it."

    return {
        "symbol": symbol,
        "adaptive_shape_id": str(study_row.get("adaptive_shape_id") or ""),
        "adaptive_lane": str(study_row.get("adaptive_lane") or ""),
        "study_status": str(study_row.get("study_status") or ""),
        "adaptive_profit_mode": str(study_row.get("adaptive_profit_mode") or ""),
        "adaptive_profit_mode_read": str(study_row.get("adaptive_profit_mode_read") or ""),
        "adaptive_objective_read": str(study_row.get("adaptive_objective_read") or ""),
        "spread_evidence": {
            "verdict": spread_verdict,
            "median_spread": median_spread if has_spread_evidence else None,
            "median_escape_spread": None if median_escape_spread < 0 else median_escape_spread,
            "escapes_above_2x": escapes_above_2x if has_spread_evidence else None,
            "read": spread_read,
        },
        "burst_evidence": {
            "verdict": burst_verdict,
            "burst_rows": [str(row.get("lane") or "") for row in burst_rows],
            "burst_expansion_opens": burst_opens,
            "burst_expansion_escapes": burst_escapes,
            "burst_expansion_pnl": burst_pnl,
            "read": burst_read,
        },
        "escape_evidence": {
            "verdict": cluster_verdict,
            "total_cluster_savable": total_cluster_savable,
            "total_prevention_savable": total_prevention_savable,
            "symbol_cluster_savable": symbol_cluster_savable,
            "read": cluster_read,
        },
        "step_evidence": {
            "verdict": step_verdict,
            "prevent_with_2x_step": parse_int(summary.get("prevent_with_2x_step")),
            "prevent_with_3x_step": parse_int(summary.get("prevent_with_3x_step")),
            "prevent_with_5x_step": parse_int(summary.get("prevent_with_5x_step")),
            "read": step_read,
        },
        "runtime_evidence": runtime_evidence,
        "contract": {
            "verdict": contract_verdict,
            "primary_entry_guard": "same_bar_open_burst_count_at_open + regime_at_entry",
            "spread_gate_role": "secondary_only" if spread_gate_demoted else "unresolved",
            "escape_role": "cluster_aware_escape_when_burst_clusters_form" if cluster_escape_promoted else "unresolved",
            "step_widening_role": "secondary_hypothesis_until_checked_in_support" if not step_widening_supported else "eligible_follow_on_control",
            "read": (
                f"For `{symbol}`, guarded-toxic-flow should treat burst context as the primary entry/escape signal, "
                f"demote spread thresholds to secondary status, and {'promote' if cluster_escape_promoted else 'delay'} "
                f"cluster-aware escape as the main runtime intervention."
            ),
        },
    }


def build_payload(
    *,
    incumbent_study: dict[str, Any],
    burst_board: dict[str, Any],
    spread_board: dict[str, Any],
    prevention_escape: dict[str, Any],
) -> dict[str, Any]:
    guarded_rows = [
        dict(row)
        for row in list(incumbent_study.get("rows") or [])
        if str(row.get("adaptive_profit_mode") or "") == "guarded_toxic_flow"
    ]
    rows = [
        build_contract_row(
            study_row=row,
            burst_board=burst_board,
            spread_board=spread_board,
            prevention_escape=prevention_escape,
        )
        for row in guarded_rows
    ]

    spread_gate_demoted_count = sum(1 for row in rows if dict(row.get("spread_evidence") or {}).get("verdict") == "demoted_as_primary_guard")
    cluster_escape_promoted_count = sum(1 for row in rows if dict(row.get("escape_evidence") or {}).get("verdict") == "promote_cluster_escape")
    unresolved_step_count = sum(1 for row in rows if dict(row.get("step_evidence") or {}).get("verdict") == "unproven_from_checked_in_board")
    runtime_guard_observed_count = sum(
        1 for row in rows if dict(row.get("runtime_evidence") or {}).get("verdict") == "guarded_open_observed"
    )
    runtime_guard_enabled_count = sum(
        1 for row in rows if dict(row.get("runtime_evidence") or {}).get("verdict") == "guard_enabled_waiting_trigger"
    )
    runtime_guard_disabled_count = sum(
        1 for row in rows if dict(row.get("runtime_evidence") or {}).get("verdict") == "runtime_explicitly_not_guarded"
    )
    runtime_guard_blind_count = sum(
        1
        for row in rows
        if dict(row.get("runtime_evidence") or {}).get("verdict") in {"runtime_visibility_missing", "no_runtime_artifact"}
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(INCUMBENT_STUDY_PATH.relative_to(ROOT)),
            str(BURST_BOARD_PATH.relative_to(ROOT)),
            str(SPREAD_BOARD_PATH.relative_to(ROOT)),
            str(PREVENTION_ESCAPE_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "guarded_symbol_count": len(rows),
            "guarded_symbols": [row["symbol"] for row in rows],
            "spread_gate_verdict": "demoted" if rows and spread_gate_demoted_count == len(rows) else "mixed_or_unresolved",
            "cluster_escape_verdict": "promoted" if rows and cluster_escape_promoted_count == len(rows) else "mixed_or_unresolved",
            "step_widening_verdict": "unproven" if rows and unresolved_step_count == len(rows) else "mixed_or_supported",
            "guard_runtime_observed_count": runtime_guard_observed_count,
            "guard_runtime_enabled_waiting_count": runtime_guard_enabled_count,
            "guard_runtime_blind_count": runtime_guard_blind_count,
            "guard_runtime_explicitly_disabled_count": runtime_guard_disabled_count,
            "contract_read": (
                "Current guarded-toxic-flow doctrine says spread thresholds are not the primary guard, "
                "cluster-aware escape is the main checked-in runtime intervention, and step widening remains a "
                "secondary hypothesis until the checked-in prevention board actually supports it."
                if rows
                else "No current guarded-toxic-flow symbols exist in the incumbent study."
            ),
            "runtime_read": (
                "Guarded-open runtime evidence is now part of the contract: distinguish overlays that have actually "
                "fired from rows that are still passive, blind, or explicitly unguarded in the current artifact."
                if rows
                else "No current guarded-toxic-flow runtime evidence is available because no guarded symbols exist."
            ),
        },
        "leadership_read": [
            (
                "Guarded-toxic-flow symbols are now explicit contract rows: "
                f"`{[row['symbol'] for row in rows]}`."
                if rows
                else "No guarded-toxic-flow symbols are present."
            ),
            (
                "Spread-threshold gating is demoted as a primary intervention where escapes are happening at normal spread."
                if spread_gate_demoted_count
                else "Spread-threshold evidence is still mixed or missing."
            ),
            (
                "Cluster-aware escape is promoted because checked-in impact evidence shows more savings there than from prevention gating."
                if cluster_escape_promoted_count
                else "Cluster-aware escape does not yet dominate the checked-in evidence."
            ),
            (
                "Step widening remains unresolved because the current burst-prevention board does not show any checked-in prevented entries at 2x/3x/5x."
                if unresolved_step_count
                else "Step widening now has some checked-in support."
            ),
            (
                f"Guarded-open has already been observed in runtime on `{runtime_guard_observed_count}` guarded row(s); "
                f"`{runtime_guard_enabled_count}` more are enabled but waiting for a trigger."
                if runtime_guard_observed_count or runtime_guard_enabled_count
                else "No guarded row has yet shown confirmed guarded-open runtime evidence; current artifacts are still blind, missing, or explicitly unguarded."
            ),
        ],
        "rows": rows,
        "notes": [
            "This board is passive doctrine. It does not change runtime behavior; it only translates current checked-in evidence into an explicit guarded-toxic-flow contract.",
            "Use this board to keep guarded-toxic-flow honest: burst context first, spread thresholds second, cluster-aware escape before broad step-widening claims.",
            "Adversarial pass rule: do not treat `guard_open_admission` as satisfied just because the overlay appears on a passive board; confirm whether the artifact exposes it and whether `open_guarded_admission` has ever actually fired.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Guarded Toxic Flow Contract Board",
        "",
        "This board turns current checked-in burst/spread/escape evidence into an explicit passive contract for guarded-toxic-flow symbols.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- guarded_symbols: `{summary.get('guarded_symbols', [])}`",
        f"- spread_gate_verdict: `{summary.get('spread_gate_verdict', '')}`",
        f"- cluster_escape_verdict: `{summary.get('cluster_escape_verdict', '')}`",
        f"- step_widening_verdict: `{summary.get('step_widening_verdict', '')}`",
        f"- guard_runtime_observed_count: `{summary.get('guard_runtime_observed_count', '')}`",
        f"- guard_runtime_enabled_waiting_count: `{summary.get('guard_runtime_enabled_waiting_count', '')}`",
        f"- guard_runtime_blind_count: `{summary.get('guard_runtime_blind_count', '')}`",
        f"- guard_runtime_explicitly_disabled_count: `{summary.get('guard_runtime_explicitly_disabled_count', '')}`",
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
            f"- contract_read: {summary.get('contract_read', '')}",
            f"- runtime_read: {summary.get('runtime_read', '')}",
            "",
            "## Rows",
            "",
        ]
    )
    for row in list(payload.get("rows") or []):
        contract = dict(row.get("contract") or {})
        spread = dict(row.get("spread_evidence") or {})
        burst = dict(row.get("burst_evidence") or {})
        escape = dict(row.get("escape_evidence") or {})
        step = dict(row.get("step_evidence") or {})
        runtime = dict(row.get("runtime_evidence") or {})
        lines.extend(
            [
                f"### {row.get('symbol', '')}",
                "",
                f"- adaptive_shape_id: `{row.get('adaptive_shape_id', '')}`",
                f"- adaptive_lane: `{row.get('adaptive_lane', '')}`",
                f"- study_status: `{row.get('study_status', '')}`",
                f"- adaptive_profit_mode_read: {row.get('adaptive_profit_mode_read', '')}",
                f"- adaptive_objective_read: {row.get('adaptive_objective_read', '')}",
                f"- contract_verdict: `{contract.get('verdict', '')}`",
                f"- primary_entry_guard: `{contract.get('primary_entry_guard', '')}`",
                f"- spread_gate_role: `{contract.get('spread_gate_role', '')}`",
                f"- escape_role: `{contract.get('escape_role', '')}`",
                f"- step_widening_role: `{contract.get('step_widening_role', '')}`",
                f"- contract_read: {contract.get('read', '')}",
                f"- spread_evidence: `{spread.get('verdict', '')}` | {spread.get('read', '')}",
                f"- burst_evidence: `{burst.get('verdict', '')}` | {burst.get('read', '')}",
                f"- escape_evidence: `{escape.get('verdict', '')}` | {escape.get('read', '')}",
                f"- step_evidence: `{step.get('verdict', '')}` | {step.get('read', '')}",
                f"- runtime_evidence: `{runtime.get('verdict', '')}` | {runtime.get('read', '')}",
                f"- runtime_paths: state=`{runtime.get('state_path', '') or '-'}` event=`{runtime.get('event_path', '') or '-'}`",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        incumbent_study=load_json(INCUMBENT_STUDY_PATH),
        burst_board=load_json(BURST_BOARD_PATH),
        spread_board=load_json(SPREAD_BOARD_PATH),
        prevention_escape=load_json(PREVENTION_ESCAPE_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
