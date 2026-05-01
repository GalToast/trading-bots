#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from copy import deepcopy

import adaptive_lattice_controller as controller


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
LEGACY_REGIME_PATH = ROOT / "reports" / "regime_adaptive_steps.json"
SHADOW_WATCHDOG_PATH = ROOT / "reports" / "watchdog" / "shadow_watchdog_report.json"
GAP2_ERR_PATH = ROOT / "reports" / "watchdog" / "shadow_usdjpy_gap2.err.log"
SHALLOW_ERR_PATH = ROOT / "reports" / "watchdog" / "shadow_usdjpy_shallow03.err.log"
CORE_PATH = ROOT / "scripts" / "tick_penetration_lattice_core.py"
JSON_PATH = ROOT / "reports" / "adaptive_lattice_proof_board.json"
MD_PATH = ROOT / "reports" / "adaptive_lattice_proof_board.md"
PACKET_PATH = ROOT / "reports" / "adaptive_overnight_launch_packet_board.json"
STALE_BLOCKER_HOURS = 12.0


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return {}
    return load_json(path)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def path_mtime_utc(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def latest_matching_blocker_log_time(path_matches: list[tuple[Path, bool]]) -> datetime | None:
    candidates = [path_mtime_utc(path) for path, matches in path_matches if matches]
    present = [item for item in candidates if item is not None]
    return max(present) if present else None


def detect_bounded_blocker() -> dict[str, Any]:
    log_paths = [GAP2_ERR_PATH, SHALLOW_ERR_PATH]
    combined_text = ""
    path_matches: list[tuple[Path, bool]] = []
    for path in log_paths:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="ignore")
            combined_text += text
            path_matches.append((path, "close_style" in text and "NameError" in text))

    shadow = load_json(SHADOW_WATCHDOG_PATH)
    rows = list(shadow.get("rows") or [])
    usdjpy_rows = [row for row in rows if str(row.get("name") or "") in {"shadow_usdjpy_gap2", "shadow_usdjpy_shallow03"}]
    statuses = sorted({str(row.get("status") or "") for row in usdjpy_rows})
    core_mtime = path_mtime_utc(CORE_PATH)
    latest_error_at = latest_matching_blocker_log_time(path_matches)
    has_matching_error = "close_style" in combined_text and "NameError" in combined_text
    fresh_vs_code = bool(
        latest_error_at
        and (
            core_mtime is None
            or latest_error_at >= core_mtime
        )
    )
    fresh_vs_clock = bool(
        latest_error_at
        and ((datetime.now(timezone.utc) - latest_error_at).total_seconds() / 3600.0) <= STALE_BLOCKER_HOURS
    )
    active = has_matching_error and fresh_vs_code and fresh_vs_clock

    if active:
        read = "bounded close-style runtime fault active"
    elif has_matching_error and latest_error_at and core_mtime and latest_error_at < core_mtime:
        read = "historical bounded close-style fault only; core code is newer than the last matching USDJPY err log"
    elif has_matching_error and latest_error_at and not fresh_vs_clock:
        read = "historical bounded close-style fault only; no fresh matching USDJPY err log remains inside the blocker window"
    else:
        read = "no active bounded close-style runtime fault detected from current logs"

    return {
        "blocker_id": "bounded_close_style_runtime_fault",
        "active": active,
        "watchdog_statuses": statuses,
        "read": read,
        "latest_error_at": latest_error_at.isoformat() if latest_error_at else "",
        "core_mtime": core_mtime.isoformat() if core_mtime else "",
    }


def current_regimes() -> dict[str, dict[str, Any]]:
    payload = load_json(REGIME_PATH)
    regimes: dict[str, dict[str, Any]] = {}
    if isinstance(payload, dict):
        for row in list(payload.get("symbols") or []):
            symbol = str(row.get("symbol") or "").upper()
            if symbol:
                regimes[symbol] = dict(row)
    elif isinstance(payload, list):
        for row in payload:
            symbol = str(row.get("symbol") or "").upper()
            regime = str(row.get("current_regime") or row.get("regime") or "")
            if symbol:
                regimes[symbol] = {"symbol": symbol, "regime": regime}
    if regimes:
        return regimes

    payload = load_json(LEGACY_REGIME_PATH)
    for row in payload if isinstance(payload, list) else []:
        symbol = str(row.get("symbol") or "").upper()
        timeframe = str(row.get("timeframe") or "")
        regime = str(row.get("current_regime") or "")
        if not symbol:
            continue
        candidate = {"symbol": symbol, "regime": regime}
        if timeframe == "M15":
            regimes[symbol] = candidate
        elif symbol not in regimes:
            regimes[symbol] = candidate
    return regimes


def apply_runtime_blocker_state(library: dict[str, Any], blocker_state: dict[str, Any]) -> dict[str, Any]:
    adjusted = deepcopy(library)
    for blocker in list(adjusted.get("blockers") or []):
        if str(blocker.get("blocker_id") or "") != str(blocker_state.get("blocker_id") or ""):
            continue
        blocker["status"] = "active" if blocker_state.get("active") else "inactive"
    return adjusted


def packet_context_by_symbol(packet_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in list(packet_payload.get("rows") or []):
        lane_name = str(row.get("lane_name") or "")
        symbol = ""
        if "_btcusd_" in lane_name:
            symbol = "BTCUSD"
        elif "_ethusd_" in lane_name:
            symbol = "ETHUSD"
        elif "_eurusd_" in lane_name:
            symbol = "EURUSD"
        elif "_gbpusd_" in lane_name:
            symbol = "GBPUSD"
        elif "_nzdusd_" in lane_name:
            symbol = "NZDUSD"
        elif "_usdjpy_" in lane_name:
            symbol = "USDJPY"
        if not symbol:
            continue
        existing = by_symbol.get(symbol, {})
        current_score = 1 if str(row.get("first_path_verdict") or "") else 0
        existing_score = 1 if str(existing.get("first_path_verdict") or "") else 0
        if current_score >= existing_score:
            by_symbol[symbol] = {
                "first_path_verdict": str(row.get("first_path_verdict") or ""),
                "first_path_open_entry_context": str(row.get("first_path_open_entry_context") or ""),
                "first_path_open_regime_at_entry": str(row.get("first_path_open_regime_at_entry") or ""),
                "same_bar_open_burst_count_at_open": int(row.get("first_path_open_same_bar_open_burst_count") or 0),
                "same_tick_open_burst_count_at_open": int(row.get("first_path_open_same_tick_open_burst_count") or 0),
            }
    return by_symbol


def current_stage(symbol_payload: dict[str, Any], recommendation: dict[str, Any], blocker_state: dict[str, Any]) -> str:
    source_stage = str(symbol_payload.get("stage") or "")
    family = str(recommendation.get("family") or "")
    status = str(recommendation.get("status") or "")
    blockers = list(recommendation.get("blockers") or [])
    if (
        source_stage == "blocked_runtime"
        and family == "bounded"
        and status == "ok"
        and not blockers
        and not blocker_state.get("active")
    ):
        return "bounded_proof_pending"
    return source_stage


def build_rows(library: dict[str, Any], blocker_state: dict[str, Any], regimes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    adjusted_library = apply_runtime_blocker_state(library, blocker_state)
    packet_context = packet_context_by_symbol(load_json_if_exists(PACKET_PATH))
    for symbol, symbol_payload in sorted(dict(adjusted_library.get("symbols") or {}).items()):
        regime_row = dict(regimes.get(symbol.upper()) or {})
        fallback_regime = str(adjusted_library.get("controller_defaults", {}).get("fallback_regime", "mixed"))
        if not regime_row:
            regime_row = {"symbol": symbol, "regime": fallback_regime}
        packet_row = dict(packet_context.get(symbol.upper()) or {})
        if packet_row:
            regime_row.update(
                {
                    "first_path_verdict": packet_row.get("first_path_verdict"),
                    "first_path_open_entry_context": packet_row.get("first_path_open_entry_context"),
                    "first_path_open_regime_at_entry": packet_row.get("first_path_open_regime_at_entry"),
                    "same_bar_open_burst_count_at_open": packet_row.get("same_bar_open_burst_count_at_open"),
                    "same_tick_open_burst_count_at_open": packet_row.get("same_tick_open_burst_count_at_open"),
                }
            )
        context = controller.context_from_regime_row(
            regime_row,
            high_friction=(
                True
                if "wide_spread" in str(packet_row.get("first_path_open_entry_context") or "")
                or "wide_spread" in str(packet_row.get("first_path_open_regime_at_entry") or "")
                else False
            ),
            high_churn=True if symbol == "USDJPY" and blocker_state["active"] else None,
            portfolio_pressure=symbol in {"BTCUSD", "ETHUSD"},
            allow_blocked_families=False,
        )
        recommendation = controller.recommend_shape(
            adjusted_library,
            symbol,
            context,
        )
        if (
            symbol == "USDJPY"
            and not blocker_state["active"]
            and recommendation.get("status") == "ok"
            and not list(recommendation.get("blockers") or [])
            and recommendation.get("family") == "bounded"
        ):
            recommendation["why"] = (
                "The old bounded close_style constructor fault is now historical only; "
                "USDJPY can return to bounded-proof evaluation and should be judged from fresh runtime evidence."
            )
        stage = current_stage(symbol_payload, recommendation, blocker_state)
        rows.append(
            {
                "symbol": symbol,
                "stage": stage,
                "source_stage": symbol_payload.get("stage"),
                "observed_regime": context.regime,
                "recommended_shape_id": recommendation.get("recommended_shape_id"),
                "family": recommendation.get("family"),
                "step_read": recommendation.get("step_read"),
                "close_read": recommendation.get("close_read"),
                "status": recommendation.get("status"),
                "blockers": recommendation.get("blockers"),
                "extractability_state": recommendation.get("extractability_state") or recommendation.get("motion_state"),
                "extractability_read": recommendation.get("extractability_read") or recommendation.get("motion_read"),
                "profit_mode": recommendation.get("profit_mode") or recommendation.get("controller_mode"),
                "profit_mode_read": recommendation.get("profit_mode_read") or recommendation.get("controller_mode_read"),
                "runtime_overlays": recommendation.get("runtime_overlays") or [],
                "runtime_overlay_read": recommendation.get("runtime_overlay_read") or "",
                "objective_read": recommendation.get("objective_read"),
                "first_path_verdict": packet_row.get("first_path_verdict", ""),
                "why": recommendation.get("why"),
            }
        )
    return rows


def build_payload() -> dict[str, Any]:
    library = load_json(CONFIG_PATH)
    blocker_state = detect_bounded_blocker()
    regimes = current_regimes()
    rows = build_rows(library, blocker_state, regimes)
    bounded_read = (
        "Raw-family shapes are currently the cleanest implementation path because bounded-family research remains blocked by the active close-style runtime fault."
        if blocker_state["active"]
        else "Bounded-family adaptive work is no longer blocked by the old close-style constructor fault; current promotion choices should follow fresh proof, not archival USDJPY err logs."
    )
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": [
            "Adaptive lattice work should proceed as a shape-library plus controller problem, not another one-off retune wave.",
            bounded_read,
            "BTCUSD and ETHUSD should be treated as portfolio-heavy sleeves, while GBPUSD remains the strongest clean FX geometry survivor in the current evidence stack.",
        ],
        "blockers": [
            blocker_state,
        ],
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Adaptive Lattice Proof Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(["", "## Blockers", ""])
    for blocker in payload["blockers"]:
        lines.append(
            f"- `{blocker['blocker_id']}`: `{str(blocker['active']).lower()}` | {blocker['read']} | watchdog={','.join(blocker.get('watchdog_statuses') or ['-'])}"
        )
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Symbol | Stage | Observed Regime | Extractability | Profit Mode | Runtime Overlay | First Path | Recommended Shape | Family | Step Read | Close Read | Status | Blockers | Why |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        blockers = ",".join(str(item) for item in row.get("blockers") or []) or "-"
        extractability = str(row.get("extractability_state") or "-")
        profit_mode = str(row.get("profit_mode") or "-")
        runtime_overlay = str(row.get("runtime_overlay_read") or "-")
        first_path = str(row.get("first_path_verdict") or "-")
        lines.append(
            f"| {row['symbol']} | {row['stage']} | {row['observed_regime']} | {extractability} | {profit_mode} | {runtime_overlay} | {first_path} | {row['recommended_shape_id'] or '-'} | "
            f"{row['family'] or '-'} | {row['step_read'] or '-'} | {row['close_read'] or '-'} | "
            f"{row['status'] or '-'} | {blockers} | {row['why'] or '-'} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
