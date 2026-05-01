#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_GATE_PATH = REPORTS / "kraken_maker_ab_promotion_gate.json"
DEFAULT_COMPARISON_PATH = REPORTS / "kraken_maker_ab_comparison_board.json"
DEFAULT_HOT_SCAN_PATH = REPORTS / "kraken_maker_hot_products_scan.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_next_proof_board.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_next_proof_board.md"


def parallel_ratio50_command() -> str:
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 3 --systemic-selection-limit 3 --systemic-deploy-pct 0.10 "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=20,FOLKS-USD=30,BTR-USD=30 "
        "--max-loss-pct 3.0 --max-quote-usd 8.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--state-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_ab_state.json "
        "--event-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_ab_events.jsonl "
        "--lock-path reports\\locks\\kraken_spot_maker_machinegun_parallel_ratio50_ab.lock "
        "--loss-tracker-state-path reports\\kraken_maker_loss_tracker_parallel_ratio50_ab_state.json"
    )


def parallel_ratio50_taker_guard_command() -> str:
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 3 --systemic-selection-limit 3 --systemic-deploy-pct 0.10 "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=20,FOLKS-USD=30,BTR-USD=30 "
        "--max-loss-pct 3.0 --max-quote-usd 8.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--state-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_state.json "
        "--event-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_events.jsonl "
        "--lock-path reports\\locks\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab.lock "
        "--loss-tracker-state-path reports\\kraken_maker_loss_tracker_parallel_ratio50_taker_guard_ab_state.json"
    )


def parallel_ratio50_taker_guard_live_exec_command() -> str:
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 3 --systemic-selection-limit 3 --systemic-deploy-pct 0.10 "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=20,FOLKS-USD=30,BTR-USD=30 "
        "--max-loss-pct 3.0 --max-quote-usd 10.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--state-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_state.json "
        "--event-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl "
        "--lock-path reports\\locks\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab.lock "
        "--loss-tracker-state-path reports\\kraken_maker_loss_tracker_parallel_ratio50_taker_guard_live_exec_ab_state.json"
    )


def parallel_ratio50_taker_guard_live_exec_fast_cooldown_command() -> str:
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 3 --systemic-selection-limit 3 --systemic-deploy-pct 0.10 "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=15,FOLKS-USD=20,BTR-USD=25 "
        "--max-loss-pct 3.0 --max-quote-usd 10.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--state-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_state.json "
        "--event-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl "
        "--lock-path reports\\locks\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab.lock "
        "--loss-tracker-state-path reports\\kraken_maker_loss_tracker_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_state.json"
    )


def parallel_ratio50_taker_guard_live_exec_dds25_command() -> str:
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 3 --systemic-selection-limit 3 --systemic-deploy-pct 0.25 "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=15,FOLKS-USD=20,BTR-USD=25 "
        "--max-loss-pct 3.0 --max-quote-usd 25.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--enable-dds --dds-depth-pct 0.10 --enable-post-only-simulation --post-only-reject-prob 0.10 "
        "--state-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab_state.json "
        "--event-path reports\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab_events.jsonl "
        "--lock-path reports\\locks\\kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab.lock "
        "--loss-tracker-state-path reports\\kraken_maker_loss_tracker_parallel_ratio50_taker_guard_live_exec_dds25_ab_state.json"
    )


def parallel_ratio50_taker_guard_live_exec_dds25_fixed_command(
    epoch_suffix: str = "dds25_fixed_texas_safe_epoch1",
) -> str:
    artifact_stem = f"parallel_ratio50_taker_guard_live_exec_{epoch_suffix}_ab"
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 3 --systemic-selection-limit 3 --systemic-deploy-pct 0.25 "
        "--systemic-exclude-products FOLKS-USD "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=15,FOLKS-USD=20,BTR-USD=25 "
        "--max-loss-pct 3.0 --max-quote-usd 25.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--enable-dds --dds-depth-pct 0.10 --enable-post-only-simulation --post-only-reject-prob 0.10 "
        f"--state-path reports\\kraken_spot_maker_machinegun_{artifact_stem}_state.json "
        f"--event-path reports\\kraken_spot_maker_machinegun_{artifact_stem}_events.jsonl "
        f"--lock-path reports\\locks\\kraken_spot_maker_machinegun_{artifact_stem}.lock "
        f"--loss-tracker-state-path reports\\kraken_maker_loss_tracker_{artifact_stem}_state.json"
    )


def cooldown_ratio50_size12_command() -> str:
    return (
        "python scripts\\live_kraken_spot_frontier_maker_machinegun_shadow.py "
        "--starting-cash 100.0 --maker-fee-bps 25 --min-rent-harvest-net-pct 0.10 "
        "--idiosyncratic-max-positions 4 --idiosyncratic-deploy-pct 0.15 "
        "--systemic-max-positions 1 --systemic-deploy-pct 0.12 "
        "--reentry-cooldown-polls 60 --reentry-cooldown-overrides HOUSE-USD=20,FOLKS-USD=30,BTR-USD=30 "
        "--max-loss-pct 3.0 --max-quote-usd 12.0 --systemic-min-live-to-board-spread-ratio 0.50 "
        "--enforce-min-notional --min-notional-path reports\\kraken_spot_live_radar.json "
        "--state-path reports\\kraken_spot_maker_machinegun_cooldown_ratio50_size12_ab_state.json "
        "--event-path reports\\kraken_spot_maker_machinegun_cooldown_ratio50_size12_ab_events.jsonl "
        "--lock-path reports\\locks\\kraken_spot_maker_machinegun_cooldown_ratio50_size12_ab.lock "
        "--loss-tracker-state-path reports\\kraken_maker_loss_tracker_cooldown_ratio50_size12_ab_state.json"
    )


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def by_lane(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("lanes") if isinstance(payload.get("lanes"), list) else []
    return {str(row.get("lane") or ""): row for row in rows if str(row.get("lane") or "")}


def hot_products(payload: dict[str, Any], classification: str) -> list[str]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return [
        str(row.get("product_id"))
        for row in rows
        if str(row.get("classification") or "") == classification and str(row.get("product_id") or "")
    ]


def ratio50_readiness(
    gate_lane: dict[str, Any],
    comparison_lane: dict[str, Any],
    *,
    min_closes: int,
    min_ghost_marks: int,
) -> dict[str, Any]:
    closes = int(to_float(gate_lane.get("realized_closes") or comparison_lane.get("realized_closes")))
    losses = int(to_float(gate_lane.get("losses") or comparison_lane.get("losses")))
    ghost_marks = int(to_float(gate_lane.get("ghost_marks")))
    open_positions = int(to_float(gate_lane.get("open_positions") or comparison_lane.get("open_positions")))
    net = to_float(gate_lane.get("realized_net_usd") or comparison_lane.get("realized_net_usd"))
    reasons = list(gate_lane.get("reasons") or [])
    if losses > 0:
        status = "failed_red_packet"
        next_action = "autopsy_ratio50_loss_before_parallel_or_sizing"
    elif closes >= min_closes and ghost_marks >= min_ghost_marks and open_positions == 0 and net > 0:
        status = "ready_for_parallel_ratio50_shadow"
        next_action = "launch_parallel_ratio50_shadow_only"
    elif open_positions > 0:
        status = "wait_open_residue"
        next_action = "wait_for_flat_state_then_refresh_gate"
    else:
        status = "collect_more"
        next_action = "keep_ratio50_running_until_20_clean_closes_and_20_ghost_marks"
    return {
        "lane": "cooldown_ratio50",
        "status": status,
        "next_action": next_action,
        "closes": closes,
        "losses": losses,
        "ghost_marks": ghost_marks,
        "open_positions": open_positions,
        "realized_net_usd": round(net, 6),
        "closes_remaining": max(0, min_closes - closes),
        "ghost_marks_remaining": max(0, min_ghost_marks - ghost_marks),
        "gate_reasons": reasons,
    }


def parallel_lane_readiness(
    lane_name: str,
    gate_lane: dict[str, Any],
    comparison_lane: dict[str, Any],
    *,
    min_closes: int,
    min_ghost_marks: int,
) -> dict[str, Any]:
    closes = int(to_float(gate_lane.get("realized_closes") or comparison_lane.get("realized_closes")))
    losses = int(to_float(gate_lane.get("losses") or comparison_lane.get("losses")))
    ghost_marks = int(to_float(gate_lane.get("ghost_marks")))
    open_positions = int(to_float(gate_lane.get("open_positions") or comparison_lane.get("open_positions")))
    max_concurrent = int(
        to_float(gate_lane.get("max_concurrent_positions") or comparison_lane.get("max_concurrent_positions"))
    )
    net = to_float(gate_lane.get("realized_net_usd") or comparison_lane.get("realized_net_usd"))
    reasons = list(gate_lane.get("reasons") or [])
    events_path_raw = str(comparison_lane.get("events_path") or "")
    state_path_raw = str(comparison_lane.get("state_path") or "")
    started = (bool(events_path_raw) and Path(events_path_raw).exists()) or (
        bool(state_path_raw) and Path(state_path_raw).exists()
    )
    if not started:
        status = "not_launched"
        next_action = f"wait_for_launch_before_monitoring_{lane_name}"
    elif losses > 0:
        status = "failed_red_packet"
        next_action = f"autopsy_{lane_name}_loss_before_sizing_or_live"
    elif closes >= min_closes and ghost_marks >= min_ghost_marks and open_positions == 0 and net > 0 and max_concurrent >= 3:
        status = "ready_for_next_shadow_stage"
        next_action = f"choose_next_isolated_branch_after_{lane_name}"
    elif open_positions > 0:
        status = "wait_open_residue"
        next_action = f"wait_for_{lane_name}_flat_state_then_refresh_gate"
    else:
        status = "collect_more"
        next_action = f"monitor_{lane_name}_until_20_clean_closes_20_ghost_marks_and_top3_exercised"
    return {
        "lane": lane_name,
        "status": status,
        "next_action": next_action,
        "started": started,
        "closes": closes,
        "losses": losses,
        "ghost_marks": ghost_marks,
        "open_positions": open_positions,
        "max_concurrent_positions": max_concurrent,
        "realized_net_usd": round(net, 6),
        "closes_remaining": max(0, min_closes - closes),
        "ghost_marks_remaining": max(0, min_ghost_marks - ghost_marks),
        "gate_reasons": reasons,
    }


def parallel_ratio50_readiness(
    gate_lane: dict[str, Any],
    comparison_lane: dict[str, Any],
    *,
    min_closes: int,
    min_ghost_marks: int,
) -> dict[str, Any]:
    readiness = parallel_lane_readiness(
        "parallel_ratio50",
        gate_lane,
        comparison_lane,
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    if not readiness["started"]:
        readiness["next_action"] = "wait_for_ratio50_maturity_before_parallel_ratio50"
    elif readiness["status"] == "failed_red_packet":
        readiness["next_action"] = "autopsy_parallel_ratio50_loss_before_sizing_or_live"
    return readiness


def build_payload(
    *,
    gate_path: Path = DEFAULT_GATE_PATH,
    comparison_path: Path = DEFAULT_COMPARISON_PATH,
    hot_scan_path: Path = DEFAULT_HOT_SCAN_PATH,
    min_closes: int = 20,
    min_ghost_marks: int = 20,
) -> dict[str, Any]:
    gate = load_json(gate_path)
    comparison = load_json(comparison_path)
    hot_scan = load_json(hot_scan_path)
    gate_lanes = by_lane(gate)
    comparison_lanes = by_lane(comparison)
    ratio = ratio50_readiness(
        gate_lanes.get("cooldown_ratio50", {}),
        comparison_lanes.get("cooldown_ratio50", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    parallel_ratio = parallel_ratio50_readiness(
        gate_lanes.get("parallel_ratio50", {}),
        comparison_lanes.get("parallel_ratio50", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    taker_guard = parallel_lane_readiness(
        "parallel_ratio50_taker_guard",
        gate_lanes.get("parallel_ratio50_taker_guard", {}),
        comparison_lanes.get("parallel_ratio50_taker_guard", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    live_exec_guard = parallel_lane_readiness(
        "parallel_ratio50_taker_guard_live_exec",
        gate_lanes.get("parallel_ratio50_taker_guard_live_exec", {}),
        comparison_lanes.get("parallel_ratio50_taker_guard_live_exec", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    fast_cooldown_guard = parallel_lane_readiness(
        "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
        gate_lanes.get("parallel_ratio50_taker_guard_live_exec_fast_cooldown", {}),
        comparison_lanes.get("parallel_ratio50_taker_guard_live_exec_fast_cooldown", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    dds25_guard = parallel_lane_readiness(
        "parallel_ratio50_taker_guard_live_exec_dds25",
        gate_lanes.get("parallel_ratio50_taker_guard_live_exec_dds25", {}),
        comparison_lanes.get("parallel_ratio50_taker_guard_live_exec_dds25", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    dds25_fixed_guard = parallel_lane_readiness(
        "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
        gate_lanes.get("parallel_ratio50_taker_guard_live_exec_dds25_fixed", {}),
        comparison_lanes.get("parallel_ratio50_taker_guard_live_exec_dds25_fixed", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    dds25_texas_guard = parallel_lane_readiness(
        "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1",
        gate_lanes.get("parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1", {}),
        comparison_lanes.get("parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1", {}),
        min_closes=min_closes,
        min_ghost_marks=min_ghost_marks,
    )
    admitted_now = hot_products(hot_scan, "admitted_now")
    reentry_blocked = hot_products(hot_scan, "reentry_blocked")
    blocked_lanes = [
        str(row.get("lane"))
        for row in gate.get("lanes") or []
        if str(row.get("gate") or "") == "do_not_promote"
    ]
    fast_cooldown_ready = fast_cooldown_guard.get("status") == "ready_for_next_shadow_stage"
    active_dds25_guard = (
        dds25_texas_guard
        if dds25_texas_guard["started"] and dds25_texas_guard.get("status") != "failed_red_packet"
        else
        dds25_fixed_guard
        if dds25_fixed_guard["started"] and dds25_fixed_guard.get("status") != "failed_red_packet"
        else dds25_guard
        if dds25_guard["started"] and fast_cooldown_ready and dds25_guard.get("status") != "failed_red_packet"
        else None
    )
    primary = (
        active_dds25_guard
        if active_dds25_guard is not None
        else fast_cooldown_guard
        if fast_cooldown_guard["started"]
        else live_exec_guard
        if live_exec_guard["started"]
        else taker_guard
        if taker_guard["started"]
        else parallel_ratio
        if parallel_ratio["started"]
        else ratio
    )
    if active_dds25_guard is not None:
        exact_launch_shape = (
            f"{active_dds25_guard['lane']} is launched; monitor the isolated "
            "DDS size proof without mixing it into the 10 USD tape"
        )
        next_shadow_command = ""
    elif fast_cooldown_ready:
        exact_launch_shape = (
            "launch fresh parallel_ratio50_taker_guard_live_exec_dds25_fixed after the multi-burst "
            "accounting fix; do not continue the old DDS25 contaminated tape"
        )
        next_shadow_command = parallel_ratio50_taker_guard_live_exec_dds25_fixed_command()
    elif fast_cooldown_guard["started"]:
        exact_launch_shape = (
            "parallel_ratio50_taker_guard_live_exec_fast_cooldown is launched; monitor the isolated "
            "cooldown-compression ceiling proof"
        )
        next_shadow_command = ""
    elif live_exec_guard["started"]:
        exact_launch_shape = (
            "parallel_ratio50_taker_guard_live_exec is launched; monitor the min-notional-enforced proof"
        )
        next_shadow_command = ""
    elif taker_guard["started"]:
        exact_launch_shape = (
            "parallel_ratio50_taker_guard is already launched; monitor the corrected taker-insurance proof"
        )
        next_shadow_command = ""
    elif parallel_ratio["status"] == "failed_red_packet":
        exact_launch_shape = (
            "same 8 USD top-3 parallel ratio50 shape, isolated new state/events, after taker insurance exits "
            "were corrected to execute immediately at bid with taker fees"
        )
        next_shadow_command = parallel_ratio50_taker_guard_command()
    elif parallel_ratio["started"]:
        exact_launch_shape = "parallel_ratio50 is already launched; keep the isolated 8 USD concurrency proof running"
        next_shadow_command = ""
    elif ratio["status"] == "ready_for_parallel_ratio50_shadow":
        exact_launch_shape = (
            "same 8 USD quote cap and ratio50 entry blocker, --systemic-max-positions 3, "
            "--systemic-selection-limit 3, isolated parallel_ratio50 state/events/lock/loss tracker"
        )
        next_shadow_command = parallel_ratio50_command()
    elif ratio["status"] == "failed_red_packet":
        exact_launch_shape = "do not launch; build ratio50 loss autopsy first"
        next_shadow_command = ""
    else:
        exact_launch_shape = "no new lane; keep cooldown_ratio50 as the single active proof hypothesis"
        next_shadow_command = ""
    if active_dds25_guard is not None:
        read = (
            f"{active_dds25_guard['lane']} is the active isolated size proof. "
            "It keeps ratio50/taker guards, compressed cooldowns, and radar-backed min-notional, "
            "adds a 25 USD max quote plus 10% top-of-book DDS and post-only reject simulation, "
            "and must not be merged with the clean 10 USD evidence tape."
        )
    elif fast_cooldown_ready:
        read = (
            "parallel_ratio50_taker_guard_live_exec_fast_cooldown has cleared the no-loss next-stage "
            "shadow gate. The old DDS25 lane is not allowed to steal primary if it failed or is "
            "bug-contaminated; the next honest size proof is a fresh Texas-safe dds25_fixed epoch with new "
            "state/events/lock/loss-tracker files. Do not place live orders from this branch."
        )
    elif fast_cooldown_guard["started"]:
        read = (
            "parallel_ratio50_taker_guard_live_exec_fast_cooldown is the active isolated ceiling proof. "
            "It keeps the 10 USD min-notional-enforced contract and ratio50/taker guards, but compresses "
            "product cooldowns to test whether throughput and top-3 exercise improve without reopening "
            "the red-packet loss mode. Do not place live orders from this branch."
        )
    elif live_exec_guard["started"]:
        read = (
            "parallel_ratio50_taker_guard_live_exec is the active live-executable shadow proof lane. "
            "It enforces radar-backed min-notional at a 10 USD quote cap; do not place live orders until "
            "this lane clears the no-loss maturity gate and the separate live-readiness board clears."
        )
    elif taker_guard["started"]:
        read = (
            "parallel_ratio50_taker_guard is the active isolated proof lane. Do not launch sizing or live orders "
            "until the corrected taker-insurance proof clears the no-loss maturity gate."
        )
    elif parallel_ratio["status"] == "failed_red_packet":
        read = (
            "parallel_ratio50 failed through exit realism, not entry ratio. The next proof is the corrected "
            "taker-insurance rerun, not size or live."
        )
    elif parallel_ratio["started"]:
        read = (
            "parallel_ratio50 is the active isolated proof lane. Do not launch sizing or live orders until "
            "parallel_ratio50 clears the no-loss maturity gate."
        )
    else:
        read = (
            "Single next-proof authority. Do not launch parallelism or sizing until ratio50 clears the no-loss "
            "maturity gate."
        )
    return {
        "generated_at": gate.get("generated_at") or comparison.get("generated_at") or hot_scan.get("generated_at") or "",
        "mode": "kraken_maker_next_proof_board",
        "parameters": {
            "gate_path": str(gate_path),
            "comparison_path": str(comparison_path),
            "hot_scan_path": str(hot_scan_path),
            "min_closes": min_closes,
            "min_ghost_marks": min_ghost_marks,
        },
        "summary": {
            "primary_lane": primary["lane"],
            "primary_status": primary["status"],
            "next_action": primary["next_action"],
            "exact_launch_shape": exact_launch_shape,
            "next_shadow_command": next_shadow_command,
            "pending_next_shadow_command_after_maturity": parallel_ratio50_command(),
            "taker_guard_shadow_command": parallel_ratio50_taker_guard_command(),
            "taker_guard_live_exec_shadow_command": parallel_ratio50_taker_guard_live_exec_command(),
            "taker_guard_live_exec_fast_cooldown_shadow_command": parallel_ratio50_taker_guard_live_exec_fast_cooldown_command(),
            "isolated_dds25_shadow_command_after_fast_cooldown_maturity": parallel_ratio50_taker_guard_live_exec_dds25_command(),
            "fresh_dds25_shadow_command_after_fast_cooldown_maturity": parallel_ratio50_taker_guard_live_exec_dds25_fixed_command(),
            "isolated_dds25_read": (
                "The original isolated dds25 state/events are quarantined if they contain the pre-fix "
                "multi-burst accounting bug. New size/DDS experiments must use fresh epoch-specific "
                "state/events/lock/loss-tracker files, not the failed dds25_fixed tape."
            ),
            "alternate_next_shadow_command_after_maturity": cooldown_ratio50_size12_command(),
            "alternate_next_shadow_read": (
                "Clean single-position size challenger for the size-before-parallel falsifier. "
                "Do not use the older cooldown_size12 lane as promotion proof because it has one loss "
                "and lacks the ratio50 live/board spread blocker."
            ),
            "blocked_lanes": blocked_lanes,
            "admitted_now": admitted_now,
            "reentry_blocked": reentry_blocked,
            "read": read,
        },
        "ratio50": ratio,
        "parallel_ratio50": parallel_ratio,
        "parallel_ratio50_taker_guard": taker_guard,
        "parallel_ratio50_taker_guard_live_exec": live_exec_guard,
        "parallel_ratio50_taker_guard_live_exec_fast_cooldown": fast_cooldown_guard,
        "parallel_ratio50_taker_guard_live_exec_dds25": dds25_guard,
        "parallel_ratio50_taker_guard_live_exec_dds25_fixed": dds25_fixed_guard,
        "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1": dds25_texas_guard,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    ratio = payload.get("ratio50") or {}
    parallel_ratio = payload.get("parallel_ratio50") or {}
    taker_guard = payload.get("parallel_ratio50_taker_guard") or {}
    live_exec_guard = payload.get("parallel_ratio50_taker_guard_live_exec") or {}
    fast_cooldown_guard = payload.get("parallel_ratio50_taker_guard_live_exec_fast_cooldown") or {}
    dds25_guard = payload.get("parallel_ratio50_taker_guard_live_exec_dds25") or {}
    dds25_fixed_guard = payload.get("parallel_ratio50_taker_guard_live_exec_dds25_fixed") or {}
    dds25_texas_guard = payload.get("parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1") or {}
    lines = [
        "# Kraken Maker Next Proof Board",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Primary lane: `{summary.get('primary_lane')}`",
        f"- Primary status: `{summary.get('primary_status')}`",
        f"- Next action: `{summary.get('next_action')}`",
        f"- Exact launch shape: {summary.get('exact_launch_shape')}",
        f"- Next shadow command ready now: `{bool(summary.get('next_shadow_command'))}`",
        f"- Blocked lanes: `{summary.get('blocked_lanes')}`",
        f"- Admitted now: `{summary.get('admitted_now')}`",
        f"- Reentry blocked: `{summary.get('reentry_blocked')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Ratio50 Maturity",
        "",
        "| Closes | Losses | Net $ | Ghost Marks | Open | Closes Remaining | Ghost Marks Remaining | Reasons |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
            closes=ratio.get("closes", 0),
            losses=ratio.get("losses", 0),
            realized_net_usd=to_float(ratio.get("realized_net_usd")),
            ghost_marks=ratio.get("ghost_marks", 0),
            open_positions=ratio.get("open_positions", 0),
            closes_remaining=ratio.get("closes_remaining", 0),
            ghost_marks_remaining=ratio.get("ghost_marks_remaining", 0),
            reasons=", ".join(ratio.get("gate_reasons") or []) or "none",
        ),
    ]
    if parallel_ratio.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=parallel_ratio.get("closes", 0),
                    losses=parallel_ratio.get("losses", 0),
                    realized_net_usd=to_float(parallel_ratio.get("realized_net_usd")),
                    ghost_marks=parallel_ratio.get("ghost_marks", 0),
                    open_positions=parallel_ratio.get("open_positions", 0),
                    max_concurrent_positions=parallel_ratio.get("max_concurrent_positions", 0),
                    closes_remaining=parallel_ratio.get("closes_remaining", 0),
                    ghost_marks_remaining=parallel_ratio.get("ghost_marks_remaining", 0),
                    reasons=", ".join(parallel_ratio.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    if taker_guard.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Taker Guard Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=taker_guard.get("closes", 0),
                    losses=taker_guard.get("losses", 0),
                    realized_net_usd=to_float(taker_guard.get("realized_net_usd")),
                    ghost_marks=taker_guard.get("ghost_marks", 0),
                    open_positions=taker_guard.get("open_positions", 0),
                    max_concurrent_positions=taker_guard.get("max_concurrent_positions", 0),
                    closes_remaining=taker_guard.get("closes_remaining", 0),
                    ghost_marks_remaining=taker_guard.get("ghost_marks_remaining", 0),
                    reasons=", ".join(taker_guard.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    if live_exec_guard.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Taker Guard Live-Exec Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=live_exec_guard.get("closes", 0),
                    losses=live_exec_guard.get("losses", 0),
                    realized_net_usd=to_float(live_exec_guard.get("realized_net_usd")),
                    ghost_marks=live_exec_guard.get("ghost_marks", 0),
                    open_positions=live_exec_guard.get("open_positions", 0),
                    max_concurrent_positions=live_exec_guard.get("max_concurrent_positions", 0),
                    closes_remaining=live_exec_guard.get("closes_remaining", 0),
                    ghost_marks_remaining=live_exec_guard.get("ghost_marks_remaining", 0),
                    reasons=", ".join(live_exec_guard.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    if fast_cooldown_guard.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Taker Guard Live-Exec Fast-Cooldown Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=fast_cooldown_guard.get("closes", 0),
                    losses=fast_cooldown_guard.get("losses", 0),
                    realized_net_usd=to_float(fast_cooldown_guard.get("realized_net_usd")),
                    ghost_marks=fast_cooldown_guard.get("ghost_marks", 0),
                    open_positions=fast_cooldown_guard.get("open_positions", 0),
                    max_concurrent_positions=fast_cooldown_guard.get("max_concurrent_positions", 0),
                    closes_remaining=fast_cooldown_guard.get("closes_remaining", 0),
                    ghost_marks_remaining=fast_cooldown_guard.get("ghost_marks_remaining", 0),
                    reasons=", ".join(fast_cooldown_guard.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    if dds25_guard.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Taker Guard Live-Exec DDS25 Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=dds25_guard.get("closes", 0),
                    losses=dds25_guard.get("losses", 0),
                    realized_net_usd=to_float(dds25_guard.get("realized_net_usd")),
                    ghost_marks=dds25_guard.get("ghost_marks", 0),
                    open_positions=dds25_guard.get("open_positions", 0),
                    max_concurrent_positions=dds25_guard.get("max_concurrent_positions", 0),
                    closes_remaining=dds25_guard.get("closes_remaining", 0),
                    ghost_marks_remaining=dds25_guard.get("ghost_marks_remaining", 0),
                    reasons=", ".join(dds25_guard.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    if dds25_fixed_guard.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Taker Guard Live-Exec DDS25 Fixed Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=dds25_fixed_guard.get("closes", 0),
                    losses=dds25_fixed_guard.get("losses", 0),
                    realized_net_usd=to_float(dds25_fixed_guard.get("realized_net_usd")),
                    ghost_marks=dds25_fixed_guard.get("ghost_marks", 0),
                    open_positions=dds25_fixed_guard.get("open_positions", 0),
                    max_concurrent_positions=dds25_fixed_guard.get("max_concurrent_positions", 0),
                    closes_remaining=dds25_fixed_guard.get("closes_remaining", 0),
                    ghost_marks_remaining=dds25_fixed_guard.get("ghost_marks_remaining", 0),
                    reasons=", ".join(dds25_fixed_guard.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    if dds25_texas_guard.get("started"):
        lines.extend(
            [
                "",
                "## Parallel Ratio50 Taker Guard Live-Exec DDS25 Texas-Safe Maturity",
                "",
                "| Closes | Losses | Net $ | Ghost Marks | Open | Max Concurrent | Closes Remaining | Ghost Marks Remaining | Reasons |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                "| {closes} | {losses} | {realized_net_usd:.6f} | {ghost_marks} | {open_positions} | {max_concurrent_positions} | {closes_remaining} | {ghost_marks_remaining} | {reasons} |".format(
                    closes=dds25_texas_guard.get("closes", 0),
                    losses=dds25_texas_guard.get("losses", 0),
                    realized_net_usd=to_float(dds25_texas_guard.get("realized_net_usd")),
                    ghost_marks=dds25_texas_guard.get("ghost_marks", 0),
                    open_positions=dds25_texas_guard.get("open_positions", 0),
                    max_concurrent_positions=dds25_texas_guard.get("max_concurrent_positions", 0),
                    closes_remaining=dds25_texas_guard.get("closes_remaining", 0),
                    ghost_marks_remaining=dds25_texas_guard.get("ghost_marks_remaining", 0),
                    reasons=", ".join(dds25_texas_guard.get("gate_reasons") or []) or "none",
                ),
            ]
        )
    command = summary.get("next_shadow_command") or ""
    if not command and not (
        dds25_texas_guard.get("started")
        or
        dds25_fixed_guard.get("started")
        or dds25_guard.get("started")
        or fast_cooldown_guard.get("started")
        or live_exec_guard.get("started")
        or taker_guard.get("started")
        or parallel_ratio.get("started")
    ):
        command = summary.get("pending_next_shadow_command_after_maturity") or ""
    if command:
        title = "## Ready Command" if summary.get("next_shadow_command") else "## Pending Command After Maturity"
        lines.extend(["", title, "", "```powershell", str(command), "```"])
    alternate_command = summary.get("alternate_next_shadow_command_after_maturity") or ""
    if alternate_command:
        lines.extend(
            [
                "",
                "## Alternate Challenge Command After Maturity",
                "",
                str(summary.get("alternate_next_shadow_read") or ""),
                "",
                "```powershell",
                str(alternate_command),
                "```",
            ]
        )
    dds25_command = summary.get("fresh_dds25_shadow_command_after_fast_cooldown_maturity") or ""
    if dds25_command:
        lines.extend(
            [
                "",
                "## Fresh DDS25 Fixed Command After Fast-Cooldown Maturity",
                "",
                str(summary.get("isolated_dds25_read") or ""),
                "",
                "```powershell",
                str(dds25_command),
                "```",
            ]
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the next-proof authority board for Kraken maker A/B work.")
    parser.add_argument("--gate-path", default=str(DEFAULT_GATE_PATH))
    parser.add_argument("--comparison-path", default=str(DEFAULT_COMPARISON_PATH))
    parser.add_argument("--hot-scan-path", default=str(DEFAULT_HOT_SCAN_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--min-closes", type=int, default=20)
    parser.add_argument("--min-ghost-marks", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        gate_path=Path(args.gate_path),
        comparison_path=Path(args.comparison_path),
        hot_scan_path=Path(args.hot_scan_path),
        min_closes=int(args.min_closes),
        min_ghost_marks=int(args.min_ghost_marks),
    )
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": str(Path(args.md_path).resolve())}, indent=2))


if __name__ == "__main__":
    main()
