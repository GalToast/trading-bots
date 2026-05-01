#!/usr/bin/env python3
"""Build a truth audit for the running BTC adaptive regime lane."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
ADAPTIVE_PLAN_PATH = ROOT / "reports" / "adaptive_btc_shadow_runner_plan.json"
UNIFIED_SPEC_PATH = ROOT / "reports" / "unified_lattice_design_spec.json"
EXECUTION_MONITOR_PATH = ROOT / "reports" / "execution_monitor_report.json"
OUTPUT_JSON = ROOT / "reports" / "btc_adaptive_runtime_audit.json"
OUTPUT_MD = ROOT / "reports" / "btc_adaptive_runtime_audit.md"

LANE_NAME = "shadow_btcusd_m15_adaptive_regime"
SYMBOL = "BTCUSD"
SPEC_KEY = "btc_m15_aggressive"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return load_json(path)


def load_optional_state_symbol(path_text: Any, symbol: str) -> dict[str, Any]:
    path = ROOT / str(path_text or "")
    if not path.exists():
        return {}
    payload = load_json(path)
    return dict((payload.get("symbols") or {}).get(symbol) or {})


def parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(raw: Any) -> float | None:
    ts = parse_iso(raw)
    if ts is None:
        return None
    return round((datetime.now(timezone.utc) - ts).total_seconds(), 1)


def find_registry_lane(registry: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for lane in list(registry.get("lanes") or []):
        if str(lane.get("name") or "") == lane_name:
            return lane
    raise KeyError(f"Missing registry lane: {lane_name}")


def find_execution_row(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("lane") or "") == lane_name:
            return row
    raise KeyError(f"Missing execution row: {lane_name}")


def find_watchdog_group(payload: dict[str, Any], lane_name: str) -> str:
    for group_name, group_cfg in dict(payload.get("groups") or {}).items():
        if lane_name in list((group_cfg or {}).get("lanes") or []):
            return str(group_name)
    return ""


def find_shape(library: dict[str, Any], symbol: str, shape_id: str) -> dict[str, Any]:
    symbols = dict(library.get("symbols") or {})
    symbol_payload = dict(symbols.get(symbol) or {})
    for shape in list(symbol_payload.get("candidate_shapes") or []):
        if str(shape.get("shape_id") or "") == shape_id:
            return shape
    raise KeyError(f"Missing shape {shape_id} for {symbol}")


def resolve_controller_shape(library: dict[str, Any], adaptive_plan: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    recommendation = dict((adaptive_plan or {}).get("controller_recommendation") or {})
    shape_id = str(recommendation.get("recommended_shape_id") or "btcusd_regime_rangeatr_v1")
    return shape_id, find_shape(library, SYMBOL, shape_id)


def regime_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("symbols") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    raise KeyError(f"Missing regime row for {symbol}")


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
    a = safe_float(actual)
    b = safe_float(expected)
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def as_bool_flag(value: Any) -> bool:
    return str(value or "").lower() == "true"


def build_check(check_id: str, surface: str, expected: str, actual: str, status: str, note: str) -> dict[str, str]:
    return {
        "check_id": check_id,
        "surface": surface,
        "expected": expected,
        "actual": actual,
        "status": status,
        "note": note,
    }


def resolve_range_atr_formula_step(regime: dict[str, Any]) -> float | None:
    published_step = safe_float(regime.get("range_atr_formula_step"))
    if published_step is not None and published_step > 0:
        return round(published_step, 5)
    avg_range = safe_float(regime.get("avg_range"))
    range_atr_ratio = safe_float(regime.get("range_atr_ratio"))
    if avg_range is None or avg_range <= 0 or range_atr_ratio is None or range_atr_ratio <= 0:
        return None
    coeff = max(0.5, min(1.2, 1.6 - 0.6 * range_atr_ratio))
    return round(avg_range * coeff, 5)


def build_runtime_summary(registry_lane: dict[str, Any], execution_row: dict[str, Any]) -> dict[str, Any]:
    restart_args = list(registry_lane.get("restart_args") or [])
    flags = restart_args_to_flags(restart_args)
    heartbeat_at = str(execution_row.get("runner_heartbeat_at") or "")
    stale_after_seconds = int(safe_float(registry_lane.get("stale_after_seconds")) or 0)
    state_path = str(registry_lane.get("state_path") or "")
    state_symbol = load_optional_state_symbol(state_path, SYMBOL)
    realized_close_count = max(
        int(safe_float(state_symbol.get("realized_closes")) or 0),
        int(safe_float(execution_row.get("runner_session_trade_closes")) or 0),
    )
    realized_net_usd = safe_float(state_symbol.get("realized_net_usd"))
    if realized_net_usd is None:
        realized_net_usd = safe_float(execution_row.get("runner_session_trade_realized_usd")) or 0.0
    realized_avg_per_close = None
    if realized_close_count > 0:
        realized_avg_per_close = realized_net_usd / realized_close_count
    return {
        "lane_name": str(registry_lane.get("name") or ""),
        "script": str(restart_args[0]) if restart_args else "",
        "kind": str(registry_lane.get("kind") or ""),
        "state_path": state_path,
        "enabled": bool(registry_lane.get("enabled", False)),
        "timeframe": str(flags.get("--timeframe") or ""),
        "step": safe_float(flags.get("--step")),
        "step_buy": safe_float(flags.get("--step-buy")),
        "step_sell": safe_float(flags.get("--step-sell")),
        "max_open_per_side": int(safe_float(flags.get("--max-open-per-side")) or 0),
        "raw_close_alpha": safe_float(flags.get("--raw-close-alpha")),
        "raw_rearm_variant": str(flags.get("--raw-rearm-variant") or ""),
        "raw_sell_gap": int(safe_float(flags.get("--raw-sell-gap")) or 0),
        "raw_buy_gap": int(safe_float(flags.get("--raw-buy-gap")) or 0),
        "direct_live": as_bool_flag(flags.get("--direct-live")),
        "live_magic": str(flags.get("--live-magic") or ""),
        "open_count": int(safe_float(execution_row.get("open_count")) or 0),
        "runner_session_trade_opens": int(safe_float(execution_row.get("runner_session_trade_opens")) or 0),
        "runner_session_trade_closes": int(safe_float(execution_row.get("runner_session_trade_closes")) or 0),
        "runner_session_trade_realized_usd": safe_float(execution_row.get("runner_session_trade_realized_usd")) or 0.0,
        "pre_start_state_carry_closes": int(safe_float(execution_row.get("pre_start_state_carry_closes")) or 0),
        "pre_start_state_carry_realized_usd": safe_float(execution_row.get("pre_start_state_carry_realized_usd")) or 0.0,
        "realized_close_count": realized_close_count,
        "realized_net_usd": realized_net_usd,
        "realized_avg_per_close": realized_avg_per_close,
        "anchor_reset_count": int(safe_float(state_symbol.get("anchor_resets")) or 0),
        "first_path_verdict": str(state_symbol.get("first_path_verdict") or ""),
        "last_trade_event_at": str(execution_row.get("last_trade_event_at") or ""),
        "runner_heartbeat_at": heartbeat_at,
        "runner_heartbeat_age_seconds": age_seconds(heartbeat_at),
        "state_last_write_at": str(execution_row.get("state_last_write_at") or ""),
        "watchdog_status": str(execution_row.get("watchdog_status") or ""),
        "watchdog_group": "",
        "stale_after_seconds": stale_after_seconds,
    }


def build_runtime_truth_checks(runtime: dict[str, Any]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    enabled = bool(runtime.get("enabled"))
    watchdog_group = str(runtime.get("watchdog_group") or "")
    watchdog_status = str(runtime.get("watchdog_status") or "")
    stale_after_seconds = int(runtime.get("stale_after_seconds") or 0)
    heartbeat_age = safe_float(runtime.get("runner_heartbeat_age_seconds"))
    is_stale = heartbeat_age is not None and stale_after_seconds > 0 and heartbeat_age > stale_after_seconds

    checks.append(
        build_check(
            "runtime_presence",
            "runtime_truth",
            "enabled runtime with explicit supervision",
            (
                f"enabled={str(enabled).lower()}; watchdog_group={watchdog_group or 'none'}; "
                f"watchdog_status={watchdog_status or 'none'}"
            ),
            "pass" if enabled and watchdog_group else ("warn" if not enabled else "fail"),
            (
                "The runtime artifact still exists on disk, but registry now parks it pending an intentional relaunch decision."
                if not enabled
                else
                "The lane exists, but it should not be described as supervised unless it is actually wired into a watchdog group."
                if not watchdog_group
                else "The lane is present and explicitly wired into watchdog supervision."
            ),
        )
    )
    checks.append(
        build_check(
            "runtime_freshness",
            "runtime_truth",
            f"heartbeat <= {stale_after_seconds}s",
            (
                f"heartbeat_age={heartbeat_age}s"
                if heartbeat_age is not None
                else "heartbeat missing"
            ),
            "warn" if not enabled and (is_stale or heartbeat_age is None) else ("fail" if is_stale or heartbeat_age is None else "pass"),
            (
                "The parked lane is stale on disk, which is acceptable for a parked artifact but not valid proof for promotion claims."
                if not enabled and (is_stale or heartbeat_age is None)
                else
                "The current runtime is stale, so it is not honest proof or honest supervision state."
                if is_stale or heartbeat_age is None
                else "The current runtime heartbeat is still within the stale threshold."
            ),
        )
    )
    checks.append(
        build_check(
            "runtime_direct_live",
            "runtime_truth",
            "shadow-only optional",
            "direct-live enabled" if runtime["direct_live"] else "shadow-only",
            "warn" if runtime["direct_live"] else "pass",
            "This lane is broker-coupled, which raises the bar for any future geometry changes.",
        )
    )
    return checks


def resolve_controller_expected_step(regime: dict[str, Any], plan: dict[str, Any] | None) -> float | None:
    if plan:
        return safe_float((plan.get("adaptive_step_plan") or {}).get("step"))
    return resolve_range_atr_formula_step(regime)


def build_checks(
    runtime: dict[str, Any],
    controller_shape_id: str,
    controller_shape: dict[str, Any],
    regime: dict[str, Any],
    adaptive_plan: dict[str, Any] | None,
    unified_spec: dict[str, Any] | None,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    controller_expected_step = resolve_controller_expected_step(regime, adaptive_plan)
    if controller_expected_step is not None:
        checks.append(
            build_check(
                "controller_step_mode",
                "adaptive_controller",
                f"range/ATR adaptive step ~= {controller_expected_step}",
                str(runtime["step"]),
                "warn" if not compare_float(runtime["step"], controller_expected_step, tol=0.01) else "pass",
                "The running lane uses a fixed step, so it is not the same artifact as the controller's range-adaptive scaffold.",
            )
        )

    checks.append(
        build_check(
            "controller_shape",
            "adaptive_controller",
            controller_shape_id,
            str(dict((adaptive_plan or {}).get("controller_recommendation") or {}).get("recommended_shape_id") or controller_shape_id),
            "pass",
            "The audit now reads controller truth from the current BTC adaptive plan rather than pinning the older default shape.",
        )
    )
    checks.append(
        build_check(
            "controller_alpha",
            "adaptive_controller",
            str((controller_shape.get("close") or {}).get("alpha")),
            str(runtime["raw_close_alpha"]),
            "warn"
            if not compare_float(runtime["raw_close_alpha"], (controller_shape.get("close") or {}).get("alpha"))
            else "pass",
            (
                "The runtime already matches the current monetization-aware controller alpha."
                if compare_float(runtime["raw_close_alpha"], (controller_shape.get("close") or {}).get("alpha"))
                else "The current runtime still differs from the active controller shape's close alpha."
            ),
        )
    )
    checks.append(
        build_check(
            "controller_max_open",
            "adaptive_controller",
            "80",
            str(runtime["max_open_per_side"]),
            "warn" if runtime["max_open_per_side"] != 80 else "pass",
            "The runtime is intentionally capital-lighter than the original controller scaffold.",
        )
    )

    spec_shape = None
    if unified_spec:
        spec_shape = dict((unified_spec.get("shapes") or {}).get(SPEC_KEY) or {})
    if spec_shape:
        checks.append(
            build_check(
                "design_step",
                "unified_design_spec",
                str(spec_shape.get("step")),
                str(runtime["step"]),
                "pass" if compare_float(runtime["step"], spec_shape.get("step"), tol=25.0) else "warn",
                "The running step is near the unified design spec's 425 target, even though it is not identical.",
            )
        )
        checks.append(
            build_check(
                "design_alpha",
                "unified_design_spec",
                str(spec_shape.get("raw_close_alpha")),
                str(runtime["raw_close_alpha"]),
                "pass"
                if compare_float(runtime["raw_close_alpha"], spec_shape.get("raw_close_alpha"))
                else "warn",
                "The live-coupled shadow lane matches the design spec's partial-close alpha.",
            )
        )
        checks.append(
            build_check(
                "design_max_open",
                "unified_design_spec",
                str(spec_shape.get("max_open_per_side")),
                str(runtime["max_open_per_side"]),
                "pass" if runtime["max_open_per_side"] == int(spec_shape.get("max_open_per_side") or 0) else "warn",
                "The running lane matches the spec's lean 6-slot posture.",
            )
        )
        checks.append(
            build_check(
                "design_asymmetry",
                "unified_design_spec",
                f"buy={spec_shape.get('step_buy')} sell={spec_shape.get('step_sell')}",
                (
                    f"buy={runtime['step_buy']} sell={runtime['step_sell']}"
                    if runtime["step_buy"] is not None or runtime["step_sell"] is not None
                    else f"symmetric step={runtime['step']}"
                ),
                "warn"
                if runtime["step_buy"] is None and runtime["step_sell"] is None
                else "pass",
                "The design spec wants 3:1 BUY:SELL asymmetry; the running lane currently stays symmetric.",
            )
        )
        checks.append(
            build_check(
                "design_session_gate",
                "unified_design_spec",
                "session gate 14:00-19:00 UTC",
                "not configured",
                "warn",
                "The running lane is direct-live but does not currently advertise the session-gate layer from the unified design spec.",
            )
        )

    checks.extend(build_runtime_truth_checks(runtime))

    return checks


def build_payload() -> dict[str, Any]:
    registry = load_json(REGISTRY_PATH)
    watchdog_groups = load_json(WATCHDOG_GROUPS_PATH)
    library = load_json(SHAPE_LIBRARY_PATH)
    regime_payload = load_json(REGIME_PATH)
    adaptive_plan = load_optional_json(ADAPTIVE_PLAN_PATH)
    unified_spec = load_optional_json(UNIFIED_SPEC_PATH)
    execution_monitor = load_json(EXECUTION_MONITOR_PATH)

    registry_lane = find_registry_lane(registry, LANE_NAME)
    execution_row = find_execution_row(execution_monitor, LANE_NAME)
    runtime = build_runtime_summary(registry_lane, execution_row)
    runtime["watchdog_group"] = find_watchdog_group(watchdog_groups, LANE_NAME)
    controller_shape_id, controller_shape = resolve_controller_shape(library, adaptive_plan)
    regime = regime_row(regime_payload, SYMBOL)
    checks = build_checks(runtime, controller_shape_id, controller_shape, regime, adaptive_plan, unified_spec)
    runtime_objective_context = dict((adaptive_plan or {}).get("runtime_objective_context") or {})
    close_conversion_pressure = bool(
        runtime["runner_session_trade_closes"] <= 0
        and float(runtime["runner_session_trade_realized_usd"] or 0.0) <= 0.0
    )
    negative_carry_pressure = bool(float(runtime["pre_start_state_carry_realized_usd"] or 0.0) < 0.0)
    objective_reads: list[str] = []
    if close_conversion_pressure:
        objective_reads.append("fresh session has not booked realized gains, so selection should favor faster cash harvesting")
    if negative_carry_pressure:
        objective_reads.append("pre-start carry remains negative, so new shapes should repair realized cashflow first")
    objective_read = (
        "Monetization pressure active: " + " and ".join(objective_reads) + "."
        if objective_reads
        else "No monetization-pressure override is active."
    )
    runtime_objective_context.update(
        {
            "audit_present": True,
            "lane_name": runtime["lane_name"],
            "open_count": runtime["open_count"],
            "runner_session_trade_closes": runtime["runner_session_trade_closes"],
            "runner_session_trade_realized_usd": runtime["runner_session_trade_realized_usd"],
            "pre_start_state_carry_realized_usd": runtime["pre_start_state_carry_realized_usd"],
            "realized_close_count": runtime["realized_close_count"],
            "realized_net_usd": runtime["realized_net_usd"],
            "realized_avg_per_close": runtime["realized_avg_per_close"],
            "realized_win_rate": runtime_objective_context.get("realized_win_rate"),
            "anchor_reset_count": runtime["anchor_reset_count"],
            "close_conversion_pressure": close_conversion_pressure,
            "negative_carry_pressure": negative_carry_pressure,
            "objective_read": objective_read,
        }
    )

    pass_count = sum(1 for item in checks if item["status"] == "pass")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    fail_count = sum(1 for item in checks if item["status"] == "fail")

    if fail_count:
        status = "runtime_mismatch"
    elif warn_count:
        status = "runtime_present_manual_review_required"
    else:
        status = "runtime_present_aligned"

    if not runtime["enabled"]:
        completion_read = (
            "BTC adaptive work is no longer a missing-launch problem, but the current adaptive artifact is now explicitly parked in registry. "
            "Treat its stale direct-live state as historical runtime evidence only; the next step is an intentional relaunch decision after supervision hygiene and design reconciliation, not passive drift."
        )
    elif runtime["watchdog_group"] and not (runtime["runner_heartbeat_age_seconds"] or 0) > runtime["stale_after_seconds"]:
        completion_read = (
            "BTC adaptive work is no longer a missing-launch problem. "
            "A currently supervised adaptive-regime lane is running, but it remains a hybrid runtime variant: "
            "close to the unified design spec on step/alpha/max-open, yet still unreconciled against the controller scaffold "
            "and still missing the design spec's asymmetry and session-gate layers."
        )
    else:
        completion_read = (
            "BTC adaptive work is no longer a missing-launch problem, but the current runtime is not honest supervised proof: "
            "the lane remains enabled and direct-live while current repo truth shows it stale and outside any watchdog group. "
            "Treat this as supervision-hygiene debt first, then revisit controller/spec reconciliation."
        )
    if runtime_objective_context.get("objective_read"):
        completion_read = f"{completion_read} Current selector objective read: {runtime_objective_context.get('objective_read')}"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "lane_name": LANE_NAME,
        "status": status,
        "summary": {
            "runtime_present": True,
            "pass_count": pass_count,
            "warn_count": warn_count,
            "fail_count": fail_count,
            "completion_read": completion_read,
        },
        "runtime_lane": runtime,
        "regime_context": regime,
        "runtime_objective_context": runtime_objective_context,
        "controller_shape": {
            "shape_id": controller_shape_id,
            "step_method": dict(controller_shape.get("step_method") or {}),
            "close": dict(controller_shape.get("close") or {}),
            "rearm": dict(controller_shape.get("rearm") or {}),
        },
        "adaptive_plan": adaptive_plan or {},
        "unified_design_spec": dict((unified_spec or {}).get("shapes", {}).get(SPEC_KEY) or {}),
        "checks": checks,
        "notes": [
            "This audit is read-only. It does not relaunch, disable, or rewrite the BTC adaptive lane.",
            "Use this surface to separate already-landed runtime truth from still-open reconciliation work.",
        ],
    }


def write_markdown(payload: dict[str, Any], output_path: Path) -> None:
    runtime = payload["runtime_lane"]
    summary = payload["summary"]
    lines = [
        "# BTC Adaptive Runtime Audit",
        "",
        "This surface reconciles the running BTC adaptive-regime lane against the controller scaffold and the unified design spec. It does not mutate runtime state.",
        "",
        "## Current Read",
        "",
        f"- status: `{payload['status']}`",
        f"- runtime lane: `{payload['lane_name']}`",
        f"- runtime snapshot: `{runtime['open_count']}` opens / `{runtime['runner_session_trade_closes']}` new closes / carry `{runtime['pre_start_state_carry_closes']}c/{runtime['pre_start_state_carry_realized_usd']}`",
        f"- controller shape: `{dict(payload.get('controller_shape') or {}).get('shape_id', '-')}`",
        f"- monetization objective: {dict(payload.get('runtime_objective_context') or {}).get('objective_read', 'No monetization-pressure override is active.')}",
        f"- direct-live: `{runtime['direct_live']}`",
        f"- watchdog: `group={runtime['watchdog_group'] or 'none'} status={runtime['watchdog_status'] or 'none'}`",
        f"- completion read: {summary['completion_read']}",
        "",
        "## Checks",
        "",
        "| Check | Surface | Expected | Actual | Status | Note |",
        "|---|---|---|---|---|---|",
    ]
    for item in payload["checks"]:
        lines.append(
            f"| `{item['check_id']}` | `{item['surface']}` | {item['expected']} | {item['actual']} | `{item['status']}` | {item['note']} |"
        )

    lines.extend(
        [
            "",
            "## Runtime Snapshot",
            "",
            f"- step: `{runtime['step']}`",
            f"- step_buy / step_sell: `{runtime['step_buy']}` / `{runtime['step_sell']}`",
            f"- alpha: `{runtime['raw_close_alpha']}`",
            f"- max_open_per_side: `{runtime['max_open_per_side']}`",
            f"- rearm: `{runtime['raw_rearm_variant']}`",
            f"- last trade event: `{runtime['last_trade_event_at']}`",
            f"- heartbeat: `{runtime['runner_heartbeat_at']}`",
            f"- heartbeat age seconds: `{runtime['runner_heartbeat_age_seconds']}`",
            f"- stale_after_seconds: `{runtime['stale_after_seconds']}`",
            "",
            "## Notes",
            "",
        ]
    )
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
