#!/usr/bin/env python3
"""
HUNGRY HIPPO — Intelligent Rearm System (Sprint 1.6)

Builds adaptive rearm parameters from:
1. Kill reason analysis
2. Consecutive failure tracking
3. Canonical session windows
4. Canonical control guardrails
5. Performance history

Output: reports/hungry_hippo_rearm_params.json
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from hungry_hippo_symbol_profiles import (
    default_session_profile_for_symbol,
    discover_symbols,
    infer_asset_class,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "reports" / "hungry_hippo_rearm_params.json"
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
SESSION_TABLE_PATH = ROOT / "reports" / "session_regime_step_table_v2.json"
BTC_HANDOFF_PATH = ROOT / "reports" / "btc_downtrend_handoff.json"

KILL_RESPONSES = {
    "floating_loss_breach": {"cooldown_multiplier": 2.0, "variant": "exc2", "max_injections": 1},
    "reset_storm": {"cooldown_multiplier": 3.0, "variant": "exc1", "max_injections": 0},
    "session_end": {"cooldown_multiplier": 0.5, "variant": "exc1", "max_injections": 3},
    "manual_kill": {"cooldown_multiplier": 1.0, "variant": "exc1", "max_injections": 2},
    "exception_crash": {"cooldown_multiplier": 4.0, "variant": "exc2", "max_injections": 0},
    "regime_mismatch": {"cooldown_multiplier": 2.0, "variant": "exc1", "max_injections": 1},
}

BACKOFF_SCHEDULE = {
    0: 1.0,
    1: 2.0,
    2: 4.0,
    3: 8.0,
    4: 16.0,
    5: 32.0,
    6: 60.0,
}

HOLD_CONTROL_MODES = {"wait_extreme_confirmation", "mixed_hold"}
KILL_REASONS = ["floating_loss_breach", "reset_storm", "session_end", "manual_kill", "exception_crash", "regime_mismatch"]


def load_json(path: Path) -> Any:
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


@lru_cache(maxsize=1)
def load_control_surfaces() -> dict[str, Any]:
    regime_payload = load_json(REGIME_SIGNAL_PATH)
    session_payload = load_json(SESSION_TABLE_PATH)
    btc_handoff = load_json(BTC_HANDOFF_PATH)

    regime_rows = {
        str(row.get("symbol") or "").upper(): row
        for row in list(regime_payload.get("rows") or [])
        if row.get("symbol")
    }
    session_windows = dict(session_payload.get("session_windows") or {})

    return {
        "regime_rows": regime_rows,
        "session_windows": session_windows,
        "btc_hold_gate": dict((btc_handoff or {}).get("hold_gate") or {}),
    }


def off_session_multiplier(off_hour_weight: Any) -> float:
    try:
        weight = float(off_hour_weight)
    except (TypeError, ValueError):
        return 1.0
    if weight <= 0.0:
        return 1.0
    return max(1.0, round(1.0 / weight, 2))


def resolve_session_policy(symbol: str, current_hour_utc: int, surfaces: dict[str, Any]) -> tuple[bool, float, str, str]:
    symbol = symbol.upper()
    session_meta = dict((surfaces.get("session_windows") or {}).get(symbol) or {})
    window_text = str(session_meta.get("window") or "None")
    parsed = parse_window_hours(window_text)
    if session_meta:
        if parsed is None:
            return True, 1.0, window_text, "canonical_session_table"
        is_active = current_hour_utc in parsed
        return is_active, 1.0 if is_active else off_session_multiplier(session_meta.get("off_hour_weight")), window_text, "canonical_session_table"

    fallback = default_session_profile_for_symbol(symbol, infer_asset_class(symbol))
    fallback_window = str(fallback.get("window") or "None")
    parsed_fallback = parse_window_hours(fallback_window)
    if parsed_fallback is None:
        return True, 1.0, fallback_window, str(fallback.get("source") or "default_all_hours")
    is_active = current_hour_utc in parsed_fallback
    return (
        is_active,
        1.0 if is_active else off_session_multiplier(fallback.get("off_hour_weight")),
        fallback_window,
        str(fallback.get("source") or "derived_family_defaults"),
    )


def apply_guardrails(params: dict[str, Any], surfaces: dict[str, Any]) -> dict[str, Any]:
    symbol = str(params.get("symbol") or "").upper()
    regime_row = dict((surfaces.get("regime_rows") or {}).get(symbol) or {})
    hold_gate = dict(surfaces.get("btc_hold_gate") or {})

    params["guardrail_control_mode"] = str(regime_row.get("control_mode") or "")
    params["guardrail_action_bias"] = str(regime_row.get("action_bias") or "")

    if not regime_row:
        params["canonical_guardrail_status"] = "uncovered"
        params["canonical_guardrail_reasons"] = ["No canonical regime row is available for this symbol yet."]
        params["auto_rearm_allowed"] = int(params.get("max_injections") or 0) > 0
        return params

    reasons: list[str] = []
    control_mode = str(regime_row.get("control_mode") or "")
    action_bias = str(regime_row.get("action_bias") or "")

    if control_mode in HOLD_CONTROL_MODES:
        reasons.append(
            f"Canonical control mode `{control_mode}` is a wait/hold state, so auto-rearm is disabled."
        )

    if (
        symbol == "BTCUSD"
        and hold_gate.get("deploy_decision") == "hold_current_bullish_shape"
        and action_bias == "SELL"
    ):
        reasons.append(
            "BTC SELL hold gate is active, so the bullish runtime must not auto-reinject until the hold is lifted."
        )

    if reasons:
        params["max_injections"] = 0
        params["should_rearm_now"] = False
        params["canonical_guardrail_status"] = "blocked"
        params["canonical_guardrail_reasons"] = reasons
        params["auto_rearm_allowed"] = False
        return params

    params["canonical_guardrail_status"] = "aligned"
    params["canonical_guardrail_reasons"] = ["Current rearm policy is compatible with canonical control surfaces."]
    params["auto_rearm_allowed"] = int(params.get("max_injections") or 0) > 0
    return params


def compute_rearm_params(
    symbol: str,
    kill_reason: str,
    consecutive_failures: int,
    current_hour_utc: int,
    recent_performance: list[float] | None = None,
    surfaces: dict[str, Any] | None = None,
) -> dict[str, Any]:
    surfaces = surfaces or load_control_surfaces()

    base_cooldown = 30.0
    response = KILL_RESPONSES.get(kill_reason, KILL_RESPONSES["manual_kill"])
    backoff = BACKOFF_SCHEDULE.get(min(consecutive_failures, 6), 60.0)

    is_active_hour, session_multiplier, session_window, session_source = resolve_session_policy(
        symbol, current_hour_utc, surfaces
    )

    performance_multiplier = 1.0
    if recent_performance and len(recent_performance) >= 3:
        recent_avg = sum(recent_performance[-3:]) / 3
        if recent_avg > 5.0:
            performance_multiplier = 0.5
        elif recent_avg < -5.0:
            performance_multiplier = 2.0

    cooldown_seconds = base_cooldown * response["cooldown_multiplier"] * backoff * session_multiplier * performance_multiplier
    cooldown_seconds = min(cooldown_seconds, 1800.0)
    next_rearm_allowed_at = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)

    params = {
        "symbol": symbol,
        "kill_reason": kill_reason,
        "consecutive_failures": consecutive_failures,
        "current_hour_utc": current_hour_utc,
        "is_active_hour": is_active_hour,
        "session_window": session_window,
        "session_window_source": session_source,
        "cooldown_seconds": cooldown_seconds,
        "cooldown_breakdown": {
            "base": base_cooldown,
            "kill_response": response["cooldown_multiplier"],
            "backoff": backoff,
            "session": session_multiplier,
            "performance": performance_multiplier,
        },
        "rearm_variant": response["variant"],
        "max_injections": response["max_injections"],
        "next_rearm_allowed_at": next_rearm_allowed_at.isoformat(),
        "should_rearm_now": is_active_hour and cooldown_seconds <= 300.0,
    }
    return apply_guardrails(params, surfaces)


def build_guardrail_summary(current_state: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    uncovered_symbols: list[str] = []
    blocked_symbols: list[str] = []
    for symbol, payload in current_state.items():
        status = str(payload.get("canonical_guardrail_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "uncovered":
            uncovered_symbols.append(symbol)
        if status == "blocked":
            blocked_symbols.append(symbol)
    return {
        "current_state_status_counts": status_counts,
        "blocked_symbols": blocked_symbols,
        "uncovered_symbols": uncovered_symbols,
        "hold_control_modes": sorted(HOLD_CONTROL_MODES),
    }


def discover_tracked_symbols(surfaces: dict[str, Any]) -> list[str]:
    symbols = set(
        discover_symbols(
            {"rows": list((surfaces.get("regime_rows") or {}).values())},
            {"session_windows": surfaces.get("session_windows") or {}},
        )
    )
    symbols.add("BTCUSD")
    return sorted(symbols)


def main() -> int:
    current_hour = datetime.now(timezone.utc).hour
    surfaces = load_control_surfaces()
    symbols = discover_tracked_symbols(surfaces)

    results: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        symbol_results = []
        for kill_reason in KILL_REASONS:
            for failures in range(6):
                params = compute_rearm_params(
                    symbol=symbol,
                    kill_reason=kill_reason,
                    consecutive_failures=failures,
                    current_hour_utc=current_hour,
                    recent_performance=[2.0, 3.0, 1.5] if failures == 0 else [-5.0, -3.0, -2.0],
                )
                symbol_results.append(params)
        results[symbol] = symbol_results

    current_state: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        current_state[symbol] = compute_rearm_params(
            symbol=symbol,
            kill_reason="manual_kill",
            consecutive_failures=0,
            current_hour_utc=current_hour,
            recent_performance=[2.0, 3.0, 1.5],
        )

    effective_session_windows = {
        symbol: {
            "window": current_state[symbol]["session_window"],
            "source": current_state[symbol]["session_window_source"],
        }
        for symbol in symbols
    }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_hour_utc": current_hour,
        "current_state_rearm_params": current_state,
        "scenario_matrix": results,
        "kill_response_table": KILL_RESPONSES,
        "backoff_schedule": BACKOFF_SCHEDULE,
        "session_windows": effective_session_windows,
        "guardrail_metadata": {
            **build_guardrail_summary(current_state),
            "btc_hold_gate": surfaces.get("btc_hold_gate") or {},
            "sources": {
                "regime_signal": str(REGIME_SIGNAL_PATH.relative_to(ROOT)),
                "session_table": str(SESSION_TABLE_PATH.relative_to(ROOT)),
                "btc_handoff": str(BTC_HANDOFF_PATH.relative_to(ROOT)),
            },
            "discovered_symbols": symbols,
        },
    }

    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Intelligent rearm params written to {OUTPUT}")
    print(f"\nCurrent-state rearm params (guardrailed, hour {current_hour}):")
    for sym, params in current_state.items():
        print(
            f"  {sym:10} cooldown={params['cooldown_seconds']:.0f}s, variant={params['rearm_variant']}, "
            f"active={params['is_active_hour']}, should_rearm={params['should_rearm_now']}, "
            f"guardrail={params['canonical_guardrail_status']}"
        )

    print("\nKill response table:")
    for reason, resp in KILL_RESPONSES.items():
        print(f"  {reason:25} cooldown={resp['cooldown_multiplier']}x, variant={resp['variant']}, max_injections={resp['max_injections']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
