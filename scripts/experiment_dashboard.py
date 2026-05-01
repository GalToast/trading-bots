#!/usr/bin/env python3
"""
Experiment Dashboard — compact room-level board for the current active proof/watch queue.

This surface is intentionally downstream of the current authority stack:
- ETH control truth: `reports/eth_m5_control_proof_gate_board.json`
- BTC sell-tight watch truth: `reports/btc_sell_tight_comparison_latest.json`
- GBP closure-repair pair truth: `reports/gbpusd_closure_repair_compare.json`
- Queue ordering and guardrails: `reports/hungry_hippo_next_action_board.json`

Usage:
    python scripts/experiment_dashboard.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ETH_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
BTC_COMPARE_PATH = REPORTS / "btc_sell_tight_comparison_latest.json"
GBP_COMPARE_PATH = REPORTS / "gbpusd_closure_repair_compare.json"
NEXT_ACTION_BOARD_PATH = REPORTS / "hungry_hippo_next_action_board.json"
OUTPUT_PATH = REPORTS / "experiment_dashboard_latest.md"


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fmt_age(iso_str: str | None) -> str:
    if not iso_str or str(iso_str).strip().lower() == "none":
        return "N/A"
    try:
        then = datetime.fromisoformat(str(iso_str))
    except Exception:
        return "N/A"
    age_minutes = max((datetime.now(timezone.utc) - then).total_seconds() / 60.0, 0.0)
    return f"{age_minutes:.0f}m ago"


def fmt_money(value: Any) -> str:
    try:
        return f"${float(value):+.2f}"
    except Exception:
        return "$+0.00"


def row_by_action(payload: dict[str, Any] | None, action: str) -> dict[str, Any]:
    if not payload:
        return {}
    for row in list(payload.get("rows") or []):
        if str(row.get("action") or "") == action:
            return row if isinstance(row, dict) else {}
    return {}


def check_eth_step14() -> dict[str, Any]:
    board = load_json(ETH_GATE_PATH) or {}
    summary = dict(board.get("summary") or {})
    runtime = dict(board.get("control_runtime") or {})
    reset_gate = dict(board.get("reset_gate") or {})
    comparison_gate = dict(board.get("comparison_gate") or {})

    if not board:
        return {
            "name": "ETH M5 step14 control",
            "status": "missing_authority",
            "milestone": "Rebuild `reports/eth_m5_control_proof_gate_board.json` before judging ETH.",
        }

    realized_closes = int(summary.get("realized_closes") or 0)
    target_closes = int(summary.get("target_closes") or 25)
    closes_remaining = int(summary.get("closes_remaining") or max(target_closes - realized_closes, 0))
    realized_net = float(summary.get("realized_net_usd") or 0.0)
    reset_limit = float(reset_gate.get("limit") or 0.0)

    milestone_parts: list[str] = []
    if closes_remaining > 0:
        milestone_parts.append(f"need {closes_remaining} more closes")
    if realized_net <= 0.0:
        milestone_parts.append("net must turn positive")
    if reset_limit > 0:
        milestone_parts.append(f"reset rate must stay below {reset_limit:.0f}/hour")
    milestone_parts.append("geometry must stay normalized on the fixed step14 control")

    return {
        "name": "ETH M5 step14 control",
        "status": str(summary.get("verdict") or "unknown"),
        "generated_at": str(board.get("generated_at") or ""),
        "realized_closes": realized_closes,
        "target_closes": target_closes,
        "closes_remaining": closes_remaining,
        "realized_net_usd": realized_net,
        "avg_per_close": float(summary.get("avg_per_close") or 0.0),
        "reset_rate_per_hour": float(reset_gate.get("reset_rate_per_hour") or 0.0),
        "reset_limit_per_hour": reset_limit,
        "heartbeat_at": str(runtime.get("heartbeat_at") or ""),
        "heartbeat_age": fmt_age(runtime.get("heartbeat_at")),
        "runtime_stale": bool(runtime.get("runtime_stale")),
        "geometry_normalized": bool(runtime.get("geometry_normalized")),
        "dynamic_geometry_enabled": bool(runtime.get("dynamic_geometry_enabled")),
        "open_ticket_count": int(runtime.get("open_ticket_count") or 0),
        "comparison_status": str(comparison_gate.get("comparison_status") or ""),
        "blocking_reasons": list(board.get("blocking_reasons") or []),
        "milestone": "; ".join(milestone_parts),
    }


def check_btc_sell_tight_v2() -> dict[str, Any]:
    comparison = load_json(BTC_COMPARE_PATH) or {}
    next_action_board = load_json(NEXT_ACTION_BOARD_PATH) or {}
    action_row = row_by_action(
        next_action_board,
        "continue_btc_m15_sell_tight_v2_forward_proof_and_watch_reset_behavior",
    )
    machine_truth = dict(action_row.get("machine_truth") or {})
    v2 = dict(comparison.get("v2") or {})
    close_mix = dict(comparison.get("v2_close_mix") or {})
    decision = dict(comparison.get("comparison") or {})

    if not comparison:
        return {
            "name": "BTC sell-tight v2",
            "status": "missing_authority",
            "milestone": "Rebuild `reports/btc_sell_tight_comparison_latest.json` before judging BTC.",
        }

    closes = int(machine_truth.get("btc_realized_closes") or v2.get("closes") or 0)
    first_read_target = 20
    closes_to_first_read = max(first_read_target - closes, 0)
    harvest_closes = int(machine_truth.get("btc_harvest_closes") or close_mix.get("harvest_closes") or 0)

    milestone_parts: list[str] = []
    if closes_to_first_read > 0:
        milestone_parts.append(f"about {closes_to_first_read} more closes to reach the first 20-close read")
    if harvest_closes <= 0:
        milestone_parts.append("first close_ticket harvest still missing")
    max_reset_rate = float(machine_truth.get("btc_max_resets_per_hour") or 0.0)
    if max_reset_rate > 0:
        milestone_parts.append(f"hourly reset pace must settle below {max_reset_rate:.0f}/hour")

    return {
        "name": "BTC sell-tight v2",
        "status": str(action_row.get("category") or "watch_now"),
        "generated_at": str(comparison.get("generated_at") or ""),
        "closes": closes,
        "net": float(machine_truth.get("btc_realized_net_usd") or v2.get("net") or 0.0),
        "avg": float(v2.get("avg") or 0.0),
        "anchor_resets": int(machine_truth.get("btc_anchor_resets") or v2.get("resets") or 0),
        "resets_per_close": float(machine_truth.get("btc_resets_per_close") or v2.get("resets_per_close") or 0.0),
        "reset_rate_per_hour": float(machine_truth.get("btc_reset_rate_per_hour") or 0.0),
        "max_resets_per_close": float(machine_truth.get("btc_max_resets_per_close") or 0.0),
        "max_resets_per_hour": max_reset_rate,
        "open": int(v2.get("open") or 0),
        "step_sell": float(v2.get("step_sell") or v2.get("step") or 0.0),
        "heartbeat_at": str(v2.get("heartbeat") or ""),
        "heartbeat": fmt_age(v2.get("heartbeat")),
        "harvest_closes": harvest_closes,
        "escape_tier2_surgical_closes": int(
            machine_truth.get("btc_escape_tier2_surgical_closes")
            or close_mix.get("escape_tier2_surgical_closes")
            or 0
        ),
        "close_mix_status": str(machine_truth.get("btc_close_mix_status") or close_mix.get("close_mix_status") or ""),
        "decision_status": str(decision.get("decision_status") or ""),
        "decision_summary": str(decision.get("decision_summary") or ""),
        "milestone": "; ".join(milestone_parts),
    }


def lane_by_name(payload: dict[str, Any] | None, lane_name: str) -> dict[str, Any]:
    if not payload:
        return {}
    for lane in list(payload.get("lanes") or []):
        if str(lane.get("lane") or "") == lane_name:
            return lane if isinstance(lane, dict) else {}
    return {}


def check_gbp_paired() -> dict[str, Any]:
    compare = load_json(GBP_COMPARE_PATH) or {}
    next_action_board = load_json(NEXT_ACTION_BOARD_PATH) or {}
    action_row = row_by_action(
        next_action_board,
        "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape",
    )
    summary = dict(compare.get("summary") or {})
    baseline = lane_by_name(compare, "shadow_gbpusd_tick_forward")
    no_escape = lane_by_name(compare, "shadow_gbpusd_tick_forward_no_escape")

    if not compare:
        return {
            "name": "GBP closure-repair pair",
            "status": "missing_authority",
            "milestone": "Rebuild `reports/gbpusd_closure_repair_compare.json` before judging GBP.",
        }

    baseline_closes = int(baseline.get("realized_closes") or 0)
    no_escape_closes = int(no_escape.get("realized_closes") or 0)
    note = (
        "Baseline lifetime totals are diagnostic only until the fresh paired baseline-vs-no-escape window matures; "
        "do not treat the legacy baseline close count as repaired proof."
    )

    milestone_parts = [
        "both lanes must keep writing fresh state",
        "no-escape must stay offensive_closure_enabled=false",
        "accumulate enough fresh paired closes to compare baseline vs no-escape honestly",
    ]
    if no_escape_closes <= 0:
        milestone_parts.insert(0, "no-escape still needs its first realized close")

    return {
        "name": "GBP closure-repair pair",
        "status": str(action_row.get("category") or "watch_now"),
        "generated_at": str(compare.get("generated_at") or ""),
        "paired_experiment_live": bool(summary.get("paired_experiment_live")),
        "next_action": str(summary.get("next_action") or ""),
        "baseline": baseline,
        "baseline_closes": baseline_closes,
        "no_escape": no_escape,
        "no_escape_closes": no_escape_closes,
        "no_escape_offensive_closure_enabled": bool(no_escape.get("offensive_closure_enabled", True))
        if no_escape
        else None,
        "note": note,
        "milestone": "; ".join(milestone_parts),
    }


def build_report() -> str:
    next_action_board = load_json(NEXT_ACTION_BOARD_PATH) or {}
    summary = dict(next_action_board.get("summary") or {})
    leadership_read = list(next_action_board.get("leadership_read") or [])

    eth = check_eth_step14()
    gbp = check_gbp_paired()
    btc = check_btc_sell_tight_v2()

    lines: list[str] = []
    lines.append("# Experiment Dashboard")
    lines.append(f"- Generated at: `{utc_now_iso()}`")
    lines.append("- Purpose: keep the current ETH / GBP / BTC proof queue readable from the same authority stack the team docket now trusts.")
    if summary:
        lines.append(f"- Top priority action: `{summary.get('top_priority_action', '')}`")
        lines.append(f"- Blocking enabled configs: `{int(summary.get('blocking_enabled_config_count') or 0)}`")
    lines.append("")

    if leadership_read:
        lines.append("## Leadership Read")
        lines.append("")
        for item in leadership_read[:3]:
            lines.append(f"- {item}")
        lines.append("")

    lines.append(f"## {eth['name']} — {eth['status']}")
    lines.append("")
    if eth["status"] == "missing_authority":
        lines.append(f"- Milestone: {eth['milestone']}")
    else:
        lines.append(f"- Realized closes: `{eth['realized_closes']}` / `{eth['target_closes']}`")
        lines.append(f"- Realized net USD: `{fmt_money(eth['realized_net_usd'])}`")
        lines.append(f"- Avg per close: `{fmt_money(eth['avg_per_close'])}`")
        lines.append(
            f"- Runtime: heartbeat `{eth['heartbeat_age']}`, stale=`{str(bool(eth['runtime_stale'])).lower()}`, "
            f"geometry_normalized=`{str(bool(eth['geometry_normalized'])).lower()}`, "
            f"dynamic_geometry_enabled=`{str(bool(eth['dynamic_geometry_enabled'])).lower()}`"
        )
        lines.append(
            f"- Reset gate: `{eth['reset_rate_per_hour']:.2f}` / hour against `{eth['reset_limit_per_hour']:.0f}` / hour limit"
        )
        lines.append(f"- Open tickets: `{eth['open_ticket_count']}`")
        lines.append(f"- Comparison status: `{eth['comparison_status']}`")
        if eth["blocking_reasons"]:
            reasons = ", ".join(str(item) for item in eth["blocking_reasons"])
            lines.append(f"- Blocking reasons: `{reasons}`")
        lines.append(f"- Next milestone: {eth['milestone']}")
    lines.append("")

    lines.append(f"## {gbp['name']} — {gbp['status']}")
    lines.append("")
    if gbp["status"] == "missing_authority":
        lines.append(f"- Milestone: {gbp['milestone']}")
    else:
        lines.append(f"- Paired experiment live: `{str(bool(gbp['paired_experiment_live'])).lower()}`")
        lines.append(f"- Next action: `{gbp['next_action']}`")
        if gbp["baseline"]:
            lines.append(
                f"- Baseline lane: `{gbp['baseline_closes']}` closes, `{fmt_money(gbp['baseline'].get('realized_net_usd'))}`, "
                f"heartbeat `{fmt_age(gbp['baseline'].get('heartbeat_at'))}`, offensive_closure_enabled=`{str(bool(gbp['baseline'].get('offensive_closure_enabled'))).lower()}`"
            )
        if gbp["no_escape"]:
            lines.append(
                f"- No-escape lane: `{gbp['no_escape_closes']}` closes, `{fmt_money(gbp['no_escape'].get('realized_net_usd'))}`, "
                f"heartbeat `{fmt_age(gbp['no_escape'].get('heartbeat_at'))}`, offensive_closure_enabled=`{str(bool(gbp['no_escape'].get('offensive_closure_enabled'))).lower()}`"
            )
        lines.append(f"- Honest read: {gbp['note']}")
        lines.append(f"- Next milestone: {gbp['milestone']}")
    lines.append("")

    lines.append(f"## {btc['name']} — {btc['status']}")
    lines.append("")
    if btc["status"] == "missing_authority":
        lines.append(f"- Milestone: {btc['milestone']}")
    else:
        lines.append(f"- Realized closes: `{btc['closes']}`")
        lines.append(f"- Realized net USD: `{fmt_money(btc['net'])}`")
        lines.append(f"- Avg per close: `{fmt_money(btc['avg'])}`")
        lines.append(
            f"- Reset posture: `{btc['anchor_resets']}` anchor resets, `{btc['resets_per_close']:.2f}` resets/close "
            f"(limit `{btc['max_resets_per_close']:.1f}`), `{btc['reset_rate_per_hour']:.2f}` / hour "
            f"(limit `{btc['max_resets_per_hour']:.0f}` / hour)"
        )
        lines.append(
            f"- Close mix: `{btc['harvest_closes']}` harvest closes, "
            f"`{btc['escape_tier2_surgical_closes']}` escape_tier2_surgical closes, status `{btc['close_mix_status']}`"
        )
        lines.append(f"- Open tickets: `{btc['open']}`")
        lines.append(f"- Sell step: `{btc['step_sell']:.2f}`")
        lines.append(f"- Heartbeat: `{btc['heartbeat']}`")
        if btc["decision_summary"]:
            lines.append(f"- Honest read: {btc['decision_summary']}")
        lines.append(f"- Next milestone: {btc['milestone']}")
    lines.append("")

    lines.append("## Queue Read")
    lines.append("")
    lines.append("1. ETH remains the first honest control-restoration proof lane.")
    lines.append("2. GBP is a paired closure-repair watch, not a repaired-promotion story.")
    lines.append("3. BTC is still a forward-proof watch until harvest appears and resets settle inside guardrails.")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    report = build_report()
    print(report)
    OUTPUT_PATH.write_text(report + "\n", encoding="utf-8")
    print(f"\nReport also written to {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
