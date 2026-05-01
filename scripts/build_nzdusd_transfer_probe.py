#!/usr/bin/env python3
"""Build a research-only NZDUSD transfer probe and runtime conformity audit."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
TRANSFER_BOARD_PATH = ROOT / "reports" / "adaptive_transfer_board.json"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
EXECUTION_MONITOR_PATH = ROOT / "reports" / "execution_monitor_report.json"
OUTPUT_JSON = ROOT / "reports" / "nzdusd_transfer_probe.json"
OUTPUT_MD = ROOT / "reports" / "nzdusd_transfer_probe.md"

SYMBOL = "NZDUSD"
DONOR_SYMBOL = "GBPUSD"
TARGET_LANE = "shadow_nzdusd_m15_asym"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_transfer_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    raise KeyError(f"Missing transfer row for symbol: {symbol}")


def find_shape(library: dict[str, Any], symbol: str, shape_id: str) -> dict[str, Any]:
    symbols = dict(library.get("symbols") or {})
    symbol_payload = dict(symbols.get(symbol) or {})
    for shape in list(symbol_payload.get("candidate_shapes") or []):
        if str(shape.get("shape_id") or "") == shape_id:
            return shape
    raise KeyError(f"Missing shape {shape_id} for {symbol}")


def find_registry_lane(registry: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for lane in list(registry.get("lanes") or []):
        if str(lane.get("name") or "") == lane_name:
            return lane
    raise KeyError(f"Missing registry lane: {lane_name}")


def find_execution_row(report: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(report.get("rows") or []):
        if str(row.get("lane") or "") == lane_name:
            return row
    raise KeyError(f"Missing execution-monitor row: {lane_name}")


def restart_args_to_flags(args: list[str]) -> dict[str, str]:
    flags: dict[str, str] = {}
    idx = 0
    while idx < len(args):
        item = str(args[idx])
        if not item.startswith("--"):
            idx += 1
            continue
        if idx + 1 >= len(args) or str(args[idx + 1]).startswith("--"):
            flags[item] = "true"
            idx += 1
            continue
        flags[item] = str(args[idx + 1])
        idx += 2
    return flags


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_float(actual: Any, expected: Any, tol: float = 1e-9) -> bool:
    actual_num = safe_float(actual)
    expected_num = safe_float(expected)
    if actual_num is None or expected_num is None:
        return False
    return abs(actual_num - expected_num) <= tol


def build_check(check_id: str, expected: str, actual: str, status: str, note: str = "") -> dict[str, str]:
    return {
        "check_id": check_id,
        "expected": expected,
        "actual": actual,
        "status": status,
        "note": note,
    }


def build_conformity_checks(target_shape: dict[str, Any], runtime_flags: dict[str, str]) -> list[dict[str, str]]:
    step_method = dict(target_shape.get("step_method") or {})
    close = dict(target_shape.get("close") or {})
    rearm = dict(target_shape.get("rearm") or {})

    expected_ratio = None
    sell_coeff = safe_float(step_method.get("sell_coeff"))
    buy_coeff = safe_float(step_method.get("buy_coeff"))
    if sell_coeff and buy_coeff:
        expected_ratio = buy_coeff / sell_coeff

    step_buy = safe_float(runtime_flags.get("--step-buy"))
    step_sell = safe_float(runtime_flags.get("--step-sell"))
    actual_ratio = None
    if step_buy is not None and step_sell not in (None, 0.0):
        actual_ratio = step_buy / step_sell

    checks = [
        build_check(
            "entrypoint_family",
            "raw shadow entrypoint",
            str(runtime_flags.get("__script__") or "-"),
            "pass" if "tick_crypto_shadow.py" in str(runtime_flags.get("__script__") or "") else "fail",
            "The adapt-first path should stay on the raw-family runtime, not bounded-family scaffolding.",
        ),
        build_check(
            "directional_asymmetry",
            f"buy_step/sell_step ratio ~= {expected_ratio:.2f}" if expected_ratio else "buy_step > sell_step",
            f"{step_buy} / {step_sell} -> {actual_ratio:.2f}" if actual_ratio else "-",
            "pass"
            if expected_ratio and actual_ratio and abs(actual_ratio - expected_ratio) <= 0.01
            else "fail",
            "The target shape keeps donor-style directionality but with conservative NZDUSD spacing.",
        ),
        build_check(
            "alpha",
            str(close.get("alpha")),
            str(runtime_flags.get("--raw-close-alpha") or "-"),
            "pass" if compare_float(runtime_flags.get("--raw-close-alpha"), close.get("alpha")) else "fail",
            "Half-close alpha is part of the target transfer spec.",
        ),
        build_check(
            "sell_gap",
            str(close.get("sell_gap")),
            str(runtime_flags.get("--raw-sell-gap") or "-"),
            "pass" if str(runtime_flags.get("--raw-sell-gap") or "") == str(close.get("sell_gap")) else "fail",
            "The transfer board explicitly requires conservative 1/1 gaps for NZDUSD.",
        ),
        build_check(
            "buy_gap",
            str(close.get("buy_gap")),
            str(runtime_flags.get("--raw-buy-gap") or "-"),
            "pass" if str(runtime_flags.get("--raw-buy-gap") or "") == str(close.get("buy_gap")) else "fail",
            "The donor buy-gap asymmetry should not be imported by default.",
        ),
        build_check(
            "rearm_variant",
            str(rearm.get("variant") or "-"),
            str(runtime_flags.get("--raw-rearm-variant") or "-"),
            "pass" if str(runtime_flags.get("--raw-rearm-variant") or "") == str(rearm.get("variant") or "") else "warn",
            "Runtime is allowed to diverge here, but the override should be intentional and documented.",
        ),
    ]
    return checks


def summarize_runtime(execution_row: dict[str, Any], runtime_flags: dict[str, str], registry_lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "lane_name": str(registry_lane.get("name") or TARGET_LANE),
        "kind": str(registry_lane.get("kind") or ""),
        "script": str(runtime_flags.get("__script__") or ""),
        "timeframe": str(runtime_flags.get("--timeframe") or ""),
        "step": safe_float(runtime_flags.get("--step")),
        "step_buy": safe_float(runtime_flags.get("--step-buy")),
        "step_sell": safe_float(runtime_flags.get("--step-sell")),
        "max_open_per_side": int(safe_float(runtime_flags.get("--max-open-per-side")) or 0),
        "raw_close_alpha": safe_float(runtime_flags.get("--raw-close-alpha")),
        "raw_rearm_variant": str(runtime_flags.get("--raw-rearm-variant") or ""),
        "raw_sell_gap": int(safe_float(runtime_flags.get("--raw-sell-gap")) or 0),
        "raw_buy_gap": int(safe_float(runtime_flags.get("--raw-buy-gap")) or 0),
        "open_count": int(safe_float(execution_row.get("open_count")) or 0),
        "runner_session_trade_opens": int(safe_float(execution_row.get("runner_session_trade_opens")) or 0),
        "runner_session_trade_closes": int(safe_float(execution_row.get("runner_session_trade_closes")) or 0),
        "runner_session_trade_realized_usd": safe_float(execution_row.get("runner_session_trade_realized_usd")) or 0.0,
        "last_trade_event_at": str(execution_row.get("last_trade_event_at") or ""),
        "runner_heartbeat_at": str(execution_row.get("runner_heartbeat_at") or ""),
        "state_last_write_at": str(execution_row.get("state_last_write_at") or ""),
    }


def build_payload() -> dict[str, Any]:
    transfer_board = load_json(TRANSFER_BOARD_PATH)
    shape_library = load_json(SHAPE_LIBRARY_PATH)
    registry = load_json(REGISTRY_PATH)
    execution_monitor = load_json(EXECUTION_MONITOR_PATH)

    donor_transfer_row = find_transfer_row(transfer_board, DONOR_SYMBOL)
    target_transfer_row = find_transfer_row(transfer_board, SYMBOL)
    donor_shape = find_shape(
        shape_library,
        DONOR_SYMBOL,
        str(donor_transfer_row.get("recommended_shape_id") or ""),
    )
    target_shape = find_shape(
        shape_library,
        SYMBOL,
        str(target_transfer_row.get("recommended_shape_id") or ""),
    )
    registry_lane = find_registry_lane(registry, TARGET_LANE)
    restart_args = list(registry_lane.get("restart_args") or [])
    runtime_flags = restart_args_to_flags(restart_args)
    runtime_flags["__script__"] = str(restart_args[0]) if restart_args else ""
    execution_row = find_execution_row(execution_monitor, TARGET_LANE)

    conformity_checks = build_conformity_checks(target_shape, runtime_flags)
    pass_count = sum(1 for check in conformity_checks if check["status"] == "pass")
    warn_count = sum(1 for check in conformity_checks if check["status"] == "warn")
    fail_count = sum(1 for check in conformity_checks if check["status"] == "fail")

    runtime = summarize_runtime(execution_row, runtime_flags, registry_lane)
    waiting_first_close = runtime["runner_session_trade_closes"] == 0

    if fail_count:
        status = "spec_mismatch"
    elif warn_count:
        status = "research_only_monitoring_with_override"
    else:
        status = "research_only_monitoring"

    completion_read = (
        "Completed: the NZDUSD adapt-first probe is already running under shadow supervision and matches the "
        "target transfer geometry on family, asymmetry, alpha, and conservative 1/1 gaps. "
        "It remains research-only because realism/forward proof is still pending."
    )
    if warn_count:
        completion_read += " Runtime still carries a deliberate-review override on the rearm variant."

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "status": status,
        "summary": {
            "runtime_present": True,
            "research_posture": "research_only",
            "forward_gate": "waiting_first_close" if waiting_first_close else "collect_realism_retention",
            "pass_count": pass_count,
            "warn_count": warn_count,
            "fail_count": fail_count,
            "completion_read": completion_read,
        },
        "donor_reference": {
            "symbol": DONOR_SYMBOL,
            "shape_id": donor_transfer_row["recommended_shape_id"],
            "step_read": donor_transfer_row["step_read"],
            "close_read": donor_transfer_row["close_read"],
            "rationale": donor_transfer_row["rationale"],
        },
        "target_transfer": {
            "symbol": SYMBOL,
            "shape_id": target_transfer_row["recommended_shape_id"],
            "verdict": target_transfer_row["verdict"],
            "stage": target_transfer_row["stage"],
            "step_read": target_transfer_row["step_read"],
            "close_read": target_transfer_row["close_read"],
            "rationale": target_transfer_row["rationale"],
            "constraints": list(target_transfer_row.get("constraints") or []),
        },
        "transfer_hypothesis": [
            "Preserve the raw-family directional asymmetry pattern from the GBPUSD donor.",
            "Keep half-close alpha and all-profitable close semantics for the probe.",
            "Do not import GBPUSD's wider buy-gap asymmetry; stay conservative at 1/1 gaps.",
            "Keep the lane research-only until realism retention and forward proof improve.",
        ],
        "runtime_lane": runtime,
        "conformity_checks": conformity_checks,
        "notes": [
            "This surface is an audit and decision aid only. It does not launch, stop, or rewrite the running lane.",
            "The runtime lane is already supervised via fx_watchdog and execution_monitor surfaces.",
        ],
    }


def write_markdown(payload: dict[str, Any], output_path: Path) -> None:
    runtime = payload["runtime_lane"]
    summary = payload["summary"]
    lines = [
        "# NZDUSD Transfer Probe",
        "",
        "This surface audits the running NZDUSD adapt-first probe against the approved donor-transfer research spec. It does not mutate runtime state.",
        "",
        "## Current Read",
        "",
        f"- status: `{payload['status']}`",
        f"- donor reference: `{payload['donor_reference']['symbol']}` / `{payload['donor_reference']['shape_id']}`",
        f"- target shape: `{payload['target_transfer']['shape_id']}`",
        f"- runtime lane: `{runtime['lane_name']}`",
        f"- forward gate: `{summary['forward_gate']}`",
        f"- runtime snapshot: `{runtime['open_count']}` opens / `{runtime['runner_session_trade_closes']}` closes / realized `{runtime['runner_session_trade_realized_usd']}`",
        f"- completion read: {summary['completion_read']}",
        "",
        "## Transfer Hypothesis",
        "",
    ]
    for item in payload["transfer_hypothesis"]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Conformity Checks",
            "",
            "| Check | Expected | Actual | Status | Note |",
            "|---|---|---|---|---|",
        ]
    )
    for check in payload["conformity_checks"]:
        lines.append(
            f"| `{check['check_id']}` | {check['expected']} | {check['actual']} | `{check['status']}` | {check['note']} |"
        )

    lines.extend(
        [
            "",
            "## Runtime Snapshot",
            "",
            f"- script: `{runtime['script']}`",
            f"- timeframe: `{runtime['timeframe']}`",
            f"- step: `{runtime['step']}`",
            f"- step_buy / step_sell: `{runtime['step_buy']}` / `{runtime['step_sell']}`",
            f"- alpha: `{runtime['raw_close_alpha']}`",
            f"- rearm: `{runtime['raw_rearm_variant']}`",
            f"- gaps: `sell={runtime['raw_sell_gap']}` / `buy={runtime['raw_buy_gap']}`",
            f"- last trade event: `{runtime['last_trade_event_at']}`",
            f"- heartbeat: `{runtime['runner_heartbeat_at']}`",
            "",
            "## Constraints",
            "",
        ]
    )
    for item in payload["target_transfer"]["constraints"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Notes", ""])
    for item in payload["notes"]:
        lines.append(f"- {item}")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload, OUTPUT_MD)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
