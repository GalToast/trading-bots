#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MEMORY = ROOT / "memory"
VALIDATED_EDGES_MD = MEMORY / "validated-edges.md"
OPTIMAL_SPECS_JSON = REPORTS / "optimal_lattice_specs.json"
ADAPTIVE_PLAN_JSON = REPORTS / "adaptive_btc_shadow_runner_plan.json"
EXECUTION_MONITOR_JSON = REPORTS / "execution_monitor_report.json"
LIVE_LANE_DASHBOARD_JSON = REPORTS / "live_lane_dashboard.json"
REGISTRY_JSON = ROOT / "configs" / "penetration_lattice_runner_registry.json"
OUTPUT_JSON = REPORTS / "btc_m15_warp_restore_board.json"
OUTPUT_MD = REPORTS / "btc_m15_warp_restore_board.md"

LIVE_LANE = "live_btcusd_m15_warp_941781"
RETIRED_LANES = ("shadow_btcusd_m15_warp", "shadow_btcusd_m15_warp_on20")
RESTORE_LANE = "shadow_btcusd_m15_warp_restore_v1"
RESTORE_STATE_PATH = REPORTS / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json"
RESTORE_EVENT_PATH = REPORTS / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_events.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


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


def find_registry_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    rows = payload.get("lanes") if isinstance(payload, dict) else []
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("name") or "").strip() == lane_name:
            return row
    return {}


def find_row(payload: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    for row in rows or []:
        if isinstance(row, dict) and str(row.get(key) or "").strip() == value:
            return row
    return {}


def registry_flag_value(lane: dict[str, Any], flag: str) -> str:
    args = lane.get("restart_args") if isinstance(lane.get("restart_args"), list) else []
    for idx, token in enumerate(args):
        if str(token) == flag and idx + 1 < len(args):
            return str(args[idx + 1])
    return ""


def parse_historical_shadow(text: str) -> dict[str, Any]:
    direct = re.search(
        r"M15 Warp SHADOW \(\$(?P<step>[\d.]+) step\): (?P<closes>\d+) closes, \$?(?P<net>[\d,]+(?:\.\d+)?) net, (?P<resets>\d+) resets",
        text,
    )
    if direct:
        return {
            "step": parse_float(direct.group("step")),
            "closes": parse_int(direct.group("closes")),
            "net_usd": parse_float(direct.group("net").replace(",", "")),
            "resets": parse_int(direct.group("resets")),
            "sample_source": "validated_edges_direct_shadow_line",
        }
    s_plus = re.search(
        r"BTC M15 Warp SHADOW \(\$(?P<step>[\d.]+) step\) — (?P<closes>\d+) closes, ~\$(?P<hourly>[\d.]+)/h",
        text,
    )
    if s_plus:
        return {
            "step": parse_float(s_plus.group("step")),
            "closes": parse_int(s_plus.group("closes")),
            "hourly_usd": parse_float(s_plus.group("hourly")),
            "sample_source": "validated_edges_s_plus_shadow_line",
        }
    return {}


def parse_historical_live(text: str) -> dict[str, Any]:
    match = re.search(
        r"BTC M15 Warp LIVE \(\$(?P<step>[\d.]+) step\) — (?P<closes>\d+) closes, \+\$(?P<net>[\d,]+(?:\.\d+)?)/(?P<clean>\d+)c clean, \$(?P<per_close>[\d.]+)/close",
        text,
    )
    if not match:
        return {}
    return {
        "step": parse_float(match.group("step")),
        "closes": parse_int(match.group("closes")),
        "clean_closes": parse_int(match.group("clean")),
        "net_usd": parse_float(match.group("net").replace(",", "")),
        "usd_per_close": parse_float(match.group("per_close")),
        "sample_source": "validated_edges_s_plus_live_line",
    }


def live_runtime_snapshot(
    registry_payload: dict[str, Any],
    execution_payload: dict[str, Any],
    dashboard_payload: dict[str, Any],
) -> dict[str, Any]:
    lane = find_registry_lane(registry_payload, LIVE_LANE)
    execution = find_row(execution_payload, "lane", LIVE_LANE)
    dashboard = find_row(dashboard_payload, "lane", LIVE_LANE)
    state_path_text = str(lane.get("state_path") or "").strip()
    state_path = ROOT / state_path_text if state_path_text else None
    state_payload = load_json(state_path) if state_path else {}
    metadata = state_payload.get("metadata") if isinstance(state_payload.get("metadata"), dict) else {}
    symbol_state = {}
    symbols = state_payload.get("symbols") if isinstance(state_payload.get("symbols"), dict) else {}
    if isinstance(symbols.get("BTCUSD"), dict):
        symbol_state = symbols["BTCUSD"]
    return {
        "restart_step": parse_float(registry_flag_value(lane, "--step")),
        "restart_max_floating_loss_usd": parse_float(registry_flag_value(lane, "--max-floating-loss-usd")),
        "runtime_step": parse_float(metadata.get("step") or symbol_state.get("base_step_px")),
        "runtime_step_buy": parse_float(metadata.get("step_buy")),
        "runtime_step_sell": parse_float(metadata.get("step_sell")),
        "runtime_max_floating_loss_usd": parse_float(metadata.get("max_floating_loss_usd") or symbol_state.get("max_floating_loss_usd")),
        "close_count": parse_int(execution.get("close_count")),
        "runner_session_trade_opens": parse_int(execution.get("runner_session_trade_opens")),
        "runner_session_trade_closes": parse_int(execution.get("runner_session_trade_closes")),
        "runner_session_trade_realized_usd": parse_float(execution.get("runner_session_trade_realized_usd")),
        "pre_start_state_carry_closes": parse_int(execution.get("pre_start_state_carry_closes")),
        "pre_start_state_carry_realized_usd": parse_float(execution.get("pre_start_state_carry_realized_usd")),
        "anchor_resets": parse_int(execution.get("anchor_resets")),
        "anchor_resets_flat": parse_int(execution.get("anchor_resets_flat")),
        "anchor_resets_risk": parse_int(execution.get("anchor_resets_risk")),
        "next_buy_level": parse_float(execution.get("next_buy_level")),
        "next_sell_level": parse_float(execution.get("next_sell_level")),
        "quote_bid": parse_float(execution.get("quote_bid")),
        "quote_ask": parse_float(execution.get("quote_ask")),
        "notes": str(execution.get("notes") or ""),
        "evidence_basis": str(dashboard.get("evidence_basis") or ""),
        "operator_posture": str(dashboard.get("operator_posture") or ""),
    }


def retired_shadow_rows(registry_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane_name in RETIRED_LANES:
        lane = find_registry_lane(registry_payload, lane_name)
        rows.append(
            {
                "lane": lane_name,
                "step": parse_float(registry_flag_value(lane, "--step")),
                "max_open_per_side": parse_int(registry_flag_value(lane, "--max-open-per-side")),
                "max_floating_loss_usd": parse_float(registry_flag_value(lane, "--max-floating-loss-usd")),
                "pause_note": str(lane.get("pause_note") or ""),
                "enabled": bool(lane.get("enabled", True)),
            }
        )
    return rows


def restore_candidate(optimal_payload: dict[str, Any], adaptive_payload: dict[str, Any]) -> dict[str, Any]:
    symbols = optimal_payload.get("symbols") if isinstance(optimal_payload, dict) else {}
    btc = symbols.get("BTCUSD_M15") if isinstance(symbols, dict) and isinstance(symbols.get("BTCUSD_M15"), dict) else {}
    optimal = btc.get("optimal") if isinstance(btc.get("optimal"), dict) else {}
    adaptive_step = parse_float(((adaptive_payload.get("adaptive_step_plan") or {}) if isinstance(adaptive_payload, dict) else {}).get("step"))
    command = [
        "python",
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol",
        "BTCUSD",
        "--fresh-start",
        "--timeframe",
        "M15",
        "--step",
        str(parse_float(optimal.get("step_sell"))),
        "--step-buy",
        str(parse_float(optimal.get("step_buy"))),
        "--step-sell",
        str(parse_float(optimal.get("step_sell"))),
        "--max-open-per-side",
        str(parse_int(optimal.get("max_open_per_side"))),
        "--raw-close-alpha",
        str(parse_float(optimal.get("close_alpha"))),
        "--raw-rearm-variant",
        "rearm_lvl2_exc1",
        "--raw-sell-gap",
        str(parse_int(optimal.get("close_gap"), 1)),
        "--raw-buy-gap",
        str(parse_int(optimal.get("close_gap"), 1)),
        "--shared-price-max-age-ms",
        "0",
        "--poll-seconds",
        "30",
        "--max-floating-loss-usd",
        str(parse_float(optimal.get("max_floating_loss_usd"))),
        "--max-lattice-window-bars",
        "240",
        "--state-path",
        str(RESTORE_STATE_PATH.relative_to(ROOT)),
        "--event-path",
        str(RESTORE_EVENT_PATH.relative_to(ROOT)),
    ]
    return {
        "lane": RESTORE_LANE,
        "symbol": "BTCUSD",
        "timeframe": "M15",
        "verdict": "launch_shadow_restore_comparison",
        "live_change_rule": "preserve_current_live_baseline",
        "step_sell": parse_float(optimal.get("step_sell")),
        "step_buy": parse_float(optimal.get("step_buy")),
        "step_sell_x_atr": parse_float(optimal.get("step_sell_x_atr")),
        "step_buy_x_atr": parse_float(optimal.get("step_buy_x_atr")),
        "close_style": str(optimal.get("close_style") or ""),
        "close_alpha": parse_float(optimal.get("close_alpha")),
        "close_gap": parse_int(optimal.get("close_gap"), 1),
        "max_open_per_side": parse_int(optimal.get("max_open_per_side")),
        "max_floating_loss_usd": parse_float(optimal.get("max_floating_loss_usd")),
        "expected_pnl_per_close": parse_float(btc.get("expected_pnl_per_close")),
        "expected_closes_per_hour": parse_float(btc.get("expected_closes_per_hour")),
        "expected_pnl_per_hour": parse_float(btc.get("expected_pnl_per_hour")),
        "source": str(optimal.get("source") or ""),
        "action": str(btc.get("action") or ""),
        "adaptive_plan_status": str(adaptive_payload.get("status") or ""),
        "adaptive_step_warning": adaptive_step,
        "adaptive_plan_warnings": list(adaptive_payload.get("warnings") or []) if isinstance(adaptive_payload, dict) else [],
        "state_path": str(RESTORE_STATE_PATH.relative_to(ROOT)),
        "event_path": str(RESTORE_EVENT_PATH.relative_to(ROOT)),
        "command": command,
    }


def build_payload() -> dict[str, Any]:
    validated_text = load_text(VALIDATED_EDGES_MD)
    registry_payload = load_json(REGISTRY_JSON)
    execution_payload = load_json(EXECUTION_MONITOR_JSON)
    dashboard_payload = load_json(LIVE_LANE_DASHBOARD_JSON)
    optimal_payload = load_json(OPTIMAL_SPECS_JSON)
    adaptive_payload = load_json(ADAPTIVE_PLAN_JSON)

    historical_shadow = parse_historical_shadow(validated_text)
    historical_live = parse_historical_live(validated_text)
    live_runtime = live_runtime_snapshot(registry_payload, execution_payload, dashboard_payload)
    retired = retired_shadow_rows(registry_payload)
    candidate = restore_candidate(optimal_payload, adaptive_payload)

    leadership_read = [
        "Historical BTC M15 warp S+ proof was real; the old $15 shadow remains a valid lineage artifact, not a myth.",
        "The current live BTC M15 lane is a preserved live baseline, not the same geometry as the old S+ shadow, and should not be overwritten during restore work.",
        "The retired $15 and $20 shadows stay archived as prior baselines only; the canonical restore path is a fresh comparison shadow with the repo-backed normalized geometry from optimal_lattice_specs.",
        "The adaptive BTC scaffold remains manual-review-only and is not the first honest restore attempt.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(VALIDATED_EDGES_MD.relative_to(ROOT)),
            str(REGISTRY_JSON.relative_to(ROOT)),
            str(EXECUTION_MONITOR_JSON.relative_to(ROOT)),
            str(LIVE_LANE_DASHBOARD_JSON.relative_to(ROOT)),
            str(OPTIMAL_SPECS_JSON.relative_to(ROOT)),
            str(ADAPTIVE_PLAN_JSON.relative_to(ROOT)),
        ],
        "leadership_read": leadership_read,
        "historical_best_shadow": historical_shadow,
        "historical_best_live_reference": historical_live,
        "current_live_runtime": live_runtime,
        "retired_shadow_restore_baselines": retired,
        "restore_candidate": candidate,
    }


def markdown_from_payload(payload: dict[str, Any]) -> str:
    historical_shadow = payload.get("historical_best_shadow") or {}
    historical_live = payload.get("historical_best_live_reference") or {}
    current_live = payload.get("current_live_runtime") or {}
    candidate = payload.get("restore_candidate") or {}
    retired = payload.get("retired_shadow_restore_baselines") or []

    lines = [
        "# BTC M15 Warp Restore Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: one canonical restore packet for the BTC M15 warp S+ family without overwriting the current live baseline.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in payload.get("leadership_read") or []:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Historical S+ Lineage",
            "",
            f"- Best shadow sample: `step=${historical_shadow.get('step', 0):.0f}` `closes={historical_shadow.get('closes', 0)}` `net={historical_shadow.get('net_usd', 0.0):+.2f}` `resets={historical_shadow.get('resets', 0)}`",
            f"- Best live reference sample in memory: `step=${historical_live.get('step', 0):.0f}` `closes={historical_live.get('closes', 0)}` `clean_closes={historical_live.get('clean_closes', 0)}` `net={historical_live.get('net_usd', 0.0):+.2f}` `usd_per_close={historical_live.get('usd_per_close', 0.0):.2f}`",
            "",
            "## Current Live Baseline",
            "",
            f"- Live lane: `{LIVE_LANE}`",
            f"- Runtime geometry: `step={current_live.get('runtime_step', 0):.0f}` `step_buy={current_live.get('runtime_step_buy', 0):.0f}` `step_sell={current_live.get('runtime_step_sell', 0):.0f}`",
            f"- Runtime posture: `evidence_basis={current_live.get('evidence_basis', '')}` `operator_posture={current_live.get('operator_posture', '')}`",
            f"- Fresh runner proof: `session_opens={current_live.get('runner_session_trade_opens', 0)}` `session_closes={current_live.get('runner_session_trade_closes', 0)}` `session_realized={current_live.get('runner_session_trade_realized_usd', 0.0):+.2f}`",
            f"- Carry still visible: `pre_start_state_carry={current_live.get('pre_start_state_carry_closes', 0)}c/{current_live.get('pre_start_state_carry_realized_usd', 0.0):+.2f}`",
            f"- Reset taxonomy: `total={current_live.get('anchor_resets', 0)}` `flat={current_live.get('anchor_resets_flat', 0)}` `risk={current_live.get('anchor_resets_risk', 0)}`",
            f"- Current executable levels: `bid={current_live.get('quote_bid', 0.0):.2f}` `ask={current_live.get('quote_ask', 0.0):.2f}` `next_buy={current_live.get('next_buy_level', 0.0):.2f}` `next_sell={current_live.get('next_sell_level', 0.0):.2f}`",
            "",
            "## Retired Baselines",
            "",
            "| Lane | Step | Max Open/Side | Max Loss | Enabled | Pause Note |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in retired:
        lines.append(
            f"| {row['lane']} | {row['step']:.0f} | {row['max_open_per_side']} | {row['max_floating_loss_usd']:.2f} | "
            f"{'yes' if row['enabled'] else 'no'} | {row['pause_note'] or '-'} |"
        )

    command = " ".join(str(token) for token in candidate.get("command") or [])
    lines.extend(
        [
            "",
            "## Canonical Restore Candidate",
            "",
            f"- Verdict: `{candidate.get('verdict', '')}`",
            f"- Live rule: `{candidate.get('live_change_rule', '')}`",
            f"- Candidate lane: `{candidate.get('lane', '')}`",
            f"- Geometry: `step_sell={candidate.get('step_sell', 0):.0f}` `step_buy={candidate.get('step_buy', 0):.0f}` "
            f"`sell_x_atr={candidate.get('step_sell_x_atr', 0.0):.2f}` `buy_x_atr={candidate.get('step_buy_x_atr', 0.0):.2f}`",
            f"- Close policy: `style={candidate.get('close_style', '')}` `alpha={candidate.get('close_alpha', 0.0):.1f}` `gap={candidate.get('close_gap', 0)}`",
            f"- Budget: `max_open_per_side={candidate.get('max_open_per_side', 0)}` `max_floating_loss_usd={candidate.get('max_floating_loss_usd', 0.0):.2f}`",
            f"- Expected forward profile from current repo spec: `pnl_per_close={candidate.get('expected_pnl_per_close', 0.0):.2f}` "
            f"`closes_per_hour={candidate.get('expected_closes_per_hour', 0.0):.2f}` `pnl_per_hour={candidate.get('expected_pnl_per_hour', 0.0):.2f}`",
            f"- Why this candidate: `{candidate.get('action', '')}`",
            f"- Adaptive caution: `status={candidate.get('adaptive_plan_status', '')}` `adaptive_step={candidate.get('adaptive_step_warning', 0.0):.2f}` "
            f"`warnings={'; '.join(candidate.get('adaptive_plan_warnings') or []) or '-'}`",
            "",
            "```bash",
            command,
            "```",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(markdown_from_payload(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(json.dumps({"json_path": str(OUTPUT_JSON), "md_path": str(OUTPUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
