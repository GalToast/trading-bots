#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_coinbase_spot_machinegun_shadow import (
    DEFAULT_OPPORTUNITY_TAPE_PATH,
    DEFAULT_STATE_PATH,
    STRATEGY_BOARD_PATH,
)
from live_coinbase_spot_piranha_shadow import fetch_coinbase_tick
from live_penetration_lattice_shadow import utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
JSON_PATH = REPORTS / "coinbase_spot_machinegun_forward_review.json"
CSV_PATH = REPORTS / "coinbase_spot_machinegun_forward_review.csv"
MD_PATH = REPORTS / "coinbase_spot_machinegun_forward_review.md"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or limit <= 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def banking_target_state(state: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    starting_cash = to_float(state.get("starting_cash_usd"))
    realized_net = to_float(state.get("realized_net_usd"))
    target_pct_per_hour = to_float(state.get("target_net_pct_per_hour")) or 5.0
    target_started_at = str(state.get("target_started_at") or runner.get("started_at") or "")
    started = parse_timestamp(target_started_at)
    now = datetime.now(timezone.utc)
    elapsed_hours = max(0.0, ((now - started).total_seconds() / 3600.0) if started else 0.0)
    realized_pct = (realized_net / starting_cash) * 100.0 if starting_cash else 0.0
    realized_pct_per_hour = realized_pct / elapsed_hours if elapsed_hours > 0.0 else 0.0
    target_usd_per_hour = starting_cash * (target_pct_per_hour / 100.0)
    target_usd_elapsed = target_usd_per_hour * elapsed_hours
    target_gap_usd = realized_net - target_usd_elapsed
    target_gap_pct = realized_pct - (target_pct_per_hour * elapsed_hours)
    return {
        "target_net_pct_per_hour": round(target_pct_per_hour, 6),
        "target_started_at": target_started_at,
        "elapsed_hours": round(elapsed_hours, 6),
        "target_net_usd_per_hour": round(target_usd_per_hour, 6),
        "target_net_usd_elapsed": round(target_usd_elapsed, 6),
        "realized_net_usd": round(realized_net, 6),
        "realized_net_pct": round(realized_pct, 6),
        "realized_net_pct_per_hour": round(realized_pct_per_hour, 6),
        "target_gap_usd": round(target_gap_usd, 6),
        "target_gap_pct": round(target_gap_pct, 6),
        "status": "on_target" if target_gap_usd >= 0.0 else "behind_target",
    }


def target_gap_usd(state: dict[str, Any], runner: dict[str, Any]) -> float:
    starting_cash = to_float(state.get("starting_cash_usd"))
    realized_net = to_float(state.get("realized_net_usd"))
    target_pct_per_hour = to_float(state.get("target_net_pct_per_hour"))
    target_started_at = str(state.get("target_started_at") or runner.get("started_at") or "")
    started = parse_timestamp(target_started_at)
    if not starting_cash or not target_pct_per_hour or not started:
        return 0.0
    elapsed_hours = max(0.0, (datetime.now(timezone.utc) - started).total_seconds() / 3600.0)
    target_usd = starting_cash * (target_pct_per_hour / 100.0) * elapsed_hours
    return realized_net - target_usd


def behind_target(state: dict[str, Any], runner: dict[str, Any]) -> bool:
    return to_float(state.get("target_net_pct_per_hour")) > 0.0 and target_gap_usd(state, runner) < 0.0


def strategy_rows(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: to_float(row.get("machinegun_score")), reverse=True)
    return rows


def apply_ghost_bias(rows: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    stats = state.get("ghost_stats")
    if not isinstance(stats, dict):
        stats = {}
    min_closes = max(1, int(state.get("ghost_min_closes_for_bias") or 3))
    cap = max(0.0, to_float(state.get("ghost_edge_bias_cap_pct")) or 2.0)
    adjusted: list[dict[str, Any]] = []
    for row in rows:
        product_id = str(row.get("product_id") or "")
        copy = dict(row)
        product_stats = stats.get(product_id) if isinstance(stats.get(product_id), dict) else {}
        closes = int(product_stats.get("closes", 0) or 0) if isinstance(product_stats, dict) else 0
        net_pct = to_float(product_stats.get("net_pct")) if isinstance(product_stats, dict) else 0.0
        bias = 0.0
        if closes >= min_closes:
            bias = max(-cap, min(cap, net_pct / max(1, closes)))
        copy["raw_rank"] = int(copy.get("rank") or 0)
        copy["ghost_closes"] = closes
        copy["ghost_net_pct"] = round(net_pct, 6)
        copy["ghost_edge_bias_pct"] = round(bias, 6)
        copy["ghost_adjusted_edge_over_hurdle_pct"] = round(to_float(copy.get("edge_over_hurdle_pct")) + bias, 6)
        copy["ghost_adjusted_machinegun_score"] = round(to_float(copy.get("machinegun_score")) + (bias * 3.0), 6)
        adjusted.append(copy)
    adjusted.sort(
        key=lambda row: (
            to_float(row.get("ghost_adjusted_machinegun_score")),
            to_float(row.get("ghost_adjusted_edge_over_hurdle_pct")),
        ),
        reverse=True,
    )
    for idx, row in enumerate(adjusted, start=1):
        row["rank"] = idx
    return adjusted


def ghost_timing_cooloff_reason(state: dict[str, Any], product_id: str, *, bid: float | None = None) -> str:
    stats = state.get("ghost_stats")
    if not isinstance(stats, dict):
        return ""
    product_stats = stats.get(product_id)
    if not isinstance(product_stats, dict):
        return ""
    closes = int(product_stats.get("closes", 0) or 0)
    wins = int(product_stats.get("wins", 0) or 0)
    min_closes = max(1, int(state.get("ghost_timing_cooloff_min_closes") or state.get("ghost_veto_min_closes") or 3))
    if closes < min_closes or wins > 0:
        return ""
    max_avg_loss_pct = abs(to_float(state.get("ghost_timing_cooloff_max_avg_loss_pct")) or to_float(state.get("ghost_veto_max_avg_loss_pct")) or 3.0)
    avg_net_pct = to_float(product_stats.get("net_pct")) / max(1, closes)
    if avg_net_pct > -max_avg_loss_pct:
        return ""
    ghosts = state.get("ghost_positions")
    ghost = ghosts.get(product_id) if isinstance(ghosts, dict) else None
    reclaim_price = to_float(ghost.get("highest_bid")) if isinstance(ghost, dict) else 0.0
    if bid is not None and reclaim_price > 0.0 and bid > reclaim_price:
        return ""
    return f"ghost_timing_cooloff_{closes}_closes_avg_{avg_net_pct:.4f}pct_reclaim_above_{reclaim_price:.12g}"


def annotate_admission(rows: list[dict[str, Any]], state: dict[str, Any], runner: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = state.get("reentry_blocks")
    if not isinstance(blocks, dict):
        blocks = {}
    streaks = state.get("candidate_streaks")
    if not isinstance(streaks, dict):
        streaks = {}
    required_streak = max(1, int(state.get("entry_confirmation_polls") or 1))
    pressure_edge_floor = max(0.0, to_float(state.get("target_pressure_min_entry_edge_pct")))
    pressure_live_move_floor = max(0.0, to_float(state.get("target_pressure_min_live_move_bps")))
    live_override_bps = max(0.0, to_float(state.get("target_pressure_live_override_bps")))
    live_override_min_edge = max(0.0, to_float(state.get("target_pressure_live_override_min_edge_pct")))
    target_pressure_active = behind_target(state, runner)
    live_momentum = state.get("live_momentum")
    if not isinstance(live_momentum, dict):
        live_momentum = {}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        product_id = str(row.get("product_id") or "")
        copy = dict(row)
        streak = int(streaks.get(product_id, 0) or 0)
        copy["entry_confirmation_streak"] = streak
        copy["entry_confirmation_required"] = required_streak
        reason = ""
        remaining_cooldown = int(blocks.get(product_id, 0) or 0)
        if remaining_cooldown > 0:
            reason = f"reentry_cooldown_{remaining_cooldown}_polls"
        elif required_streak > 1 and streak < required_streak:
            reason = f"entry_confirmation_wait_{streak}_of_{required_streak}"
        else:
            reason = ghost_timing_cooloff_reason(state, product_id)
        edge = to_float(copy.get("ghost_adjusted_edge_over_hurdle_pct", copy.get("edge_over_hurdle_pct")))
        momentum = live_momentum.get(product_id) if isinstance(live_momentum.get(product_id), dict) else {}
        live_move_bps = to_float(momentum.get("move_bps")) if isinstance(momentum, dict) else 0.0
        live_samples = int(momentum.get("samples", 0) or 0) if isinstance(momentum, dict) else 0
        live_move_streak = int(momentum.get("live_move_streak", 0) or 0) if isinstance(momentum, dict) else 0
        live_override_streak = int(momentum.get("live_override_streak", 0) or 0) if isinstance(momentum, dict) else 0
        velocity_override = (
            live_override_bps > 0.0
            and live_samples >= 2
            and live_override_streak >= required_streak
            and live_move_bps >= live_override_bps
            and edge >= live_override_min_edge
            and to_float(copy.get("ret_15m_pct")) > 0.0
            and to_float(copy.get("ret_60m_pct")) > 0.0
        )
        if not reason and target_pressure_active and pressure_edge_floor > 0.0 and edge < pressure_edge_floor and not velocity_override:
            reason = f"target_pressure_edge_floor_{edge:.4f}_lt_{pressure_edge_floor:.4f}"
        if not reason and target_pressure_active and pressure_live_move_floor > 0.0:
            if live_samples < 2:
                reason = f"target_pressure_live_move_warmup_{live_samples}_of_2"
            elif live_move_bps < pressure_live_move_floor:
                reason = f"target_pressure_live_move_{live_move_bps:.4f}_bps_lt_{pressure_live_move_floor:.4f}"
            elif live_move_streak < required_streak:
                reason = f"target_pressure_live_move_confirm_{live_move_streak}_of_{required_streak}"
        copy["admission_state"] = "eligible" if not reason else "blocked"
        copy["admission_reason"] = reason
        copy["target_pressure_min_entry_edge_pct"] = round(pressure_edge_floor, 6)
        copy["target_pressure_min_live_move_bps"] = round(pressure_live_move_floor, 6)
        copy["target_pressure_live_override"] = velocity_override
        copy["target_pressure_live_override_bps"] = round(live_override_bps, 6)
        copy["target_pressure_live_override_min_edge_pct"] = round(live_override_min_edge, 6)
        copy["live_move_bps"] = round(live_move_bps, 6)
        copy["live_move_samples"] = live_samples
        copy["live_move_streak"] = live_move_streak
        copy["live_override_streak"] = live_override_streak
        annotated.append(copy)
    return annotated


def admissible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("admission_state") == "eligible"]


def fallback_tick(position: dict[str, Any]) -> dict[str, Any]:
    bid = to_float(position.get("highest_bid")) or to_float(position.get("entry_price"))
    return {"bid": bid, "ask": bid, "source": "position_fallback"}


def position_mark(
    position: dict[str, Any],
    *,
    tick: dict[str, Any],
    taker_fee_bps: float,
) -> dict[str, Any]:
    bid = to_float(tick.get("bid"))
    entry = to_float(position.get("entry_price"))
    quantity = to_float(position.get("quantity"))
    cost = to_float(position.get("cost_usd"))
    entry_fee = to_float(position.get("entry_fee"))
    highest_bid = max(to_float(position.get("highest_bid")), bid)
    trail_giveback_pct = max(0.0, to_float(position.get("trail_giveback_pct")))
    fee_rate = taker_fee_bps / 10000.0
    proceeds = quantity * bid
    exit_fee = proceeds * fee_rate
    gross = (bid - entry) * quantity
    net = proceeds - exit_fee - cost
    net_pct_on_cost = (net / cost) * 100.0 if cost else 0.0
    max_net_pnl = max(to_float(position.get("max_net_pnl")), net)
    max_net_pct_on_cost = max(to_float(position.get("max_net_pct_on_cost")), net_pct_on_cost)
    trail_stop = highest_bid * (1.0 - (trail_giveback_pct / 100.0)) if highest_bid else 0.0
    return {
        "product_id": str(position.get("product_id") or ""),
        "playbook": str(position.get("playbook") or ""),
        "entry_price": round(entry, 12),
        "bid": round(bid, 12),
        "ask": round(to_float(tick.get("ask")), 12),
        "tick_source": str(tick.get("source") or "coinbase_best_bid_ask"),
        "quantity": round(quantity, 12),
        "cost_usd": round(cost, 6),
        "proceeds_before_exit_fee": round(proceeds, 6),
        "gross_pnl": round(gross, 6),
        "entry_fee": round(entry_fee, 6),
        "exit_fee": round(exit_fee, 6),
        "roundtrip_fee": round(entry_fee + exit_fee, 6),
        "net_if_closed": round(net, 6),
        "net_pct_on_cost": round(net_pct_on_cost, 6),
        "max_net_pnl": round(max_net_pnl, 6),
        "max_net_pct_on_cost": round(max_net_pct_on_cost, 6),
        "highest_bid": round(highest_bid, 12),
        "trail_stop": round(trail_stop, 12),
        "distance_to_trail_pct": round(((bid - trail_stop) / bid) * 100.0, 6) if bid else 0.0,
        "loss_pct": round(((bid - entry) / entry) * 100.0, 6) if entry else 0.0,
        "trail_giveback_pct": round(trail_giveback_pct, 6),
        "entry_edge_over_hurdle_pct": round(to_float(position.get("entry_edge_over_hurdle_pct")), 6),
    }


def rotation_review(
    *,
    position: dict[str, Any] | None,
    mark: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    taker_fee_bps: float,
    rotation_buffer_pct: float,
) -> dict[str, Any]:
    if not position or not mark:
        top = candidate_rows[0] if candidate_rows else {}
        return {
            "decision": "open_top_candidate" if top else "idle_no_fee_hurdle_candidate",
            "held_product_id": "",
            "best_challenger_product_id": str(top.get("product_id") or ""),
            "best_challenger_edge_pct": round(to_float(top.get("ghost_adjusted_edge_over_hurdle_pct", top.get("edge_over_hurdle_pct"))), 6),
            "rotation_required_pct": round((taker_fee_bps / 100.0 * 2.0) + max(0.0, rotation_buffer_pct), 6),
            "admissible_candidate_count": len(candidate_rows),
        }
    held = str(position.get("product_id") or "")
    current_row = next((row for row in rows if str(row.get("product_id") or "") == held), None)
    challenger = next((row for row in candidate_rows if str(row.get("product_id") or "") != held), None)
    current_edge = (
        to_float(current_row.get("ghost_adjusted_edge_over_hurdle_pct", current_row.get("edge_over_hurdle_pct")))
        if current_row
        else to_float(position.get("entry_edge_over_hurdle_pct"))
    )
    challenger_edge = to_float(challenger.get("ghost_adjusted_edge_over_hurdle_pct", challenger.get("edge_over_hurdle_pct"))) if challenger else 0.0
    required = (taker_fee_bps / 100.0 * 2.0) + max(0.0, rotation_buffer_pct)
    advantage = challenger_edge - current_edge
    decision = "hold_no_challenger"
    if challenger and advantage >= required:
        decision = "rotate_to_challenger"
    elif challenger:
        decision = "hold_challenger_not_fee_clear"
    return {
        "decision": decision,
        "held_product_id": held,
        "held_rank": int(current_row.get("rank") or 0) if current_row else None,
        "held_edge_pct": round(current_edge, 6),
        "held_raw_edge_pct": round(to_float(current_row.get("edge_over_hurdle_pct")) if current_row else to_float(position.get("entry_edge_over_hurdle_pct")), 6),
        "held_ghost_bias_pct": round(to_float(current_row.get("ghost_edge_bias_pct")) if current_row else 0.0, 6),
        "held_admission_state": str(current_row.get("admission_state") or "") if current_row else "",
        "held_admission_reason": str(current_row.get("admission_reason") or "") if current_row else "",
        "held_net_if_closed": mark["net_if_closed"],
        "held_net_pct_on_cost": mark["net_pct_on_cost"],
        "held_distance_to_trail_pct": mark["distance_to_trail_pct"],
        "best_challenger_product_id": str(challenger.get("product_id") or "") if challenger else "",
        "best_challenger_rank": int(challenger.get("rank") or 0) if challenger else None,
        "best_challenger_playbook": str(challenger.get("playbook") or "") if challenger else "",
        "best_challenger_edge_pct": round(challenger_edge, 6),
        "best_challenger_raw_edge_pct": round(to_float(challenger.get("edge_over_hurdle_pct")) if challenger else 0.0, 6),
        "best_challenger_ghost_bias_pct": round(to_float(challenger.get("ghost_edge_bias_pct")) if challenger else 0.0, 6),
        "edge_advantage_pct": round(advantage, 6),
        "rotation_required_pct": round(required, 6),
        "rotation_buffer_pct": round(rotation_buffer_pct, 6),
        "admissible_candidate_count": len(candidate_rows),
    }


def tape_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: Counter[str] = Counter()
    top_products: Counter[str] = Counter()
    for record in records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        decisions[str(decision.get("decision") or "unknown")] += 1
        top = (record.get("top_candidates") or [{}])[0]
        if isinstance(top, dict) and top.get("product_id"):
            top_products[str(top["product_id"])] += 1
    return {
        "scans": len(records),
        "decision_counts": dict(decisions),
        "top_product_counts": dict(top_products.most_common(10)),
        "last_scan_at": records[-1].get("ts_utc") if records else "",
    }


def filter_records_since(records: list[dict[str, Any]], started_at: Any) -> list[dict[str, Any]]:
    started = parse_timestamp(started_at)
    if not started:
        return records
    filtered: list[dict[str, Any]] = []
    for record in records:
        ts = parse_timestamp(record.get("ts_utc"))
        if ts is None or ts >= started:
            filtered.append(record)
    return filtered


def ghost_leaders(state: dict[str, Any]) -> list[dict[str, Any]]:
    stats = state.get("ghost_stats")
    if not isinstance(stats, dict):
        return []
    rows: list[dict[str, Any]] = []
    for product_id, payload in stats.items():
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "product_id": str(product_id),
                "closes": int(payload.get("closes", 0) or 0),
                "wins": int(payload.get("wins", 0) or 0),
                "losses": int(payload.get("losses", 0) or 0),
                "net_pct": round(to_float(payload.get("net_pct")), 6),
                "best_pct": payload.get("best_pct"),
                "worst_pct": payload.get("worst_pct"),
            }
        )
    rows.sort(key=lambda row: (row["net_pct"], row["wins"]), reverse=True)
    return rows


def build_payload(
    *,
    state_path: Path,
    strategy_path: Path,
    opportunity_tape_path: Path,
    no_live_tick: bool,
    tape_limit: int,
) -> dict[str, Any]:
    state_payload = load_json(state_path)
    state = state_payload.get("state") if isinstance(state_payload, dict) else {}
    if not isinstance(state, dict):
        state = {}
    runner = state_payload.get("runner") if isinstance(state_payload, dict) else {}
    if not isinstance(runner, dict):
        runner = {}
    rows = annotate_admission(apply_ghost_bias(strategy_rows(strategy_path), state), state, runner)
    candidates = admissible_rows(rows)
    position = state.get("position") if isinstance(state.get("position"), dict) else None
    taker_fee_bps = to_float(state.get("taker_fee_bps")) or to_float(runner.get("fee_bps_per_side"))
    rotation_buffer_pct = to_float(state.get("rotation_buffer_pct"))
    mark: dict[str, Any] | None = None
    tick_error = ""
    if position:
        tick = fallback_tick(position)
        if not no_live_tick:
            try:
                tick = fetch_coinbase_tick(CoinbaseAdvancedClient(), str(position.get("product_id") or ""))
                tick["source"] = "coinbase_best_bid_ask"
            except Exception as exc:  # pragma: no cover - network/auth defensive fallback
                tick_error = f"{type(exc).__name__}: {exc}"
        mark = position_mark(position, tick=tick, taker_fee_bps=taker_fee_bps)
        retention_pct = max(0.0, min(100.0, to_float(state.get("profit_lock_retention_pct"))))
        min_profit = max(0.0, to_float(state.get("min_profit_to_trail_usd")))
        max_net_pnl = to_float(mark.get("max_net_pnl"))
        profit_lock_floor = max(min_profit, max_net_pnl * (retention_pct / 100.0))
        mark["profit_lock_retention_pct"] = round(retention_pct, 6)
        mark["profit_lock_floor_usd"] = round(profit_lock_floor, 6)
        mark["profit_lock_armed"] = bool(max_net_pnl >= min_profit and min_profit > 0.0)
    review = rotation_review(
        position=position,
        mark=mark,
        rows=rows,
        candidate_rows=candidates,
        taker_fee_bps=taker_fee_bps,
        rotation_buffer_pct=rotation_buffer_pct,
    )
    tape_records = filter_records_since(
        load_jsonl_tail(opportunity_tape_path, tape_limit),
        runner.get("started_at") or state.get("target_started_at"),
    )
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_machinegun_forward_review",
        "state_path": str(state_path),
        "strategy_board_path": str(strategy_path),
        "opportunity_tape_path": str(opportunity_tape_path),
        "runner": {
            "pid": runner.get("pid"),
            "heartbeat_at": runner.get("heartbeat_at"),
            "last_successful_run_at": runner.get("last_successful_run_at"),
            "consecutive_exceptions": runner.get("consecutive_exceptions"),
            "shadow_only": runner.get("shadow_only"),
        },
        "fee": {
            "taker_bps_per_side": round(taker_fee_bps, 4),
            "fee_source": state.get("fee_source") or runner.get("fee_source") or "",
            "fee_tier": state.get("fee_tier") or runner.get("fee_tier") or "",
        },
        "account_state": {
            "cash_usd": round(to_float(state.get("cash_usd")), 6),
            "starting_cash_usd": round(to_float(state.get("starting_cash_usd")), 6),
            "realized_net_usd": round(to_float(state.get("realized_net_usd")), 6),
            "realized_closes": int(state.get("realized_closes") or 0),
            "total_fees": round(to_float(state.get("total_fees")), 6),
        },
        "banking_target": banking_target_state(state, runner),
        "current_position": mark,
        "rotation_review": review,
        "strategy_top": rows[:12],
        "admissible_strategy_top": candidates[:12],
        "opportunity_tape_summary": tape_summary(tape_records),
        "ghost_tournament": {
            "active": sorted((state.get("ghost_positions") or {}).keys()) if isinstance(state.get("ghost_positions"), dict) else [],
            "leaders": ghost_leaders(state)[:12],
        },
        "tick_error": tick_error,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "rank",
        "product_id",
        "admission_state",
        "admission_reason",
        "playbook",
        "hurdle_state",
        "machinegun_score",
        "edge_over_hurdle_pct",
        "live_move_bps",
        "live_move_streak",
        "ret_15m_pct",
        "ret_60m_pct",
        "spread_bps",
        "trail_giveback_pct",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("strategy_top") or []:
            writer.writerow({column: row.get(column, "") for column in columns})
    review = payload["rotation_review"]
    position = payload.get("current_position") or {}
    target = payload.get("banking_target") or {}
    lines = [
        "# Coinbase Spot Machinegun Forward Review",
        "",
        "## Leadership Read",
        "",
        f"- Decision: `{review.get('decision')}`",
        f"- Held product: `{review.get('held_product_id') or position.get('product_id') or 'NONE'}`",
        f"- Best challenger: `{review.get('best_challenger_product_id') or 'NONE'}`",
        f"- Admissible candidates: `{review.get('admissible_candidate_count', 0)}`",
        f"- Rotation requires challenger edge advantage of `{review.get('rotation_required_pct', 0.0)}` percentage points after exit+entry churn buffer.",
        "",
        "## Current Position",
        "",
    ]
    if position:
        lines.extend(
            [
                f"- Product: `{position['product_id']}`",
                f"- Net if closed now: `{position['net_if_closed']}` USD (`{position['net_pct_on_cost']}`% on cost)",
                f"- Gross PnL: `{position['gross_pnl']}` USD",
                f"- Round-trip fee if closed now: `{position['roundtrip_fee']}` USD",
                f"- Best net mark: `{position.get('max_net_pnl', 0.0)}` USD (`{position.get('max_net_pct_on_cost', 0.0)}`% on cost)",
                f"- Profit-lock: armed `{position.get('profit_lock_armed', False)}`, floor `{position.get('profit_lock_floor_usd', 0.0)}` USD, retention `{position.get('profit_lock_retention_pct', 0.0)}`%",
                f"- Bid / entry / high: `{position['bid']}` / `{position['entry_price']}` / `{position['highest_bid']}`",
                f"- Trail stop: `{position['trail_stop']}`, distance to trail: `{position['distance_to_trail_pct']}`%",
                "",
            ]
        )
    else:
        lines.extend(["- No current position.", ""])
    lines.extend(
        [
            "## 5%/Hour Banking Target",
            "",
            f"- Target: `{target.get('target_net_pct_per_hour', 0.0)}`% net per hour (`{target.get('target_net_usd_per_hour', 0.0)}` USD/hour on starting cash)",
            f"- Elapsed target window: `{target.get('elapsed_hours', 0.0)}` hours from `{target.get('target_started_at', '')}`",
            f"- Realized pace: `{target.get('realized_net_pct_per_hour', 0.0)}`%/hour, realized net `{target.get('realized_net_usd', 0.0)}` USD (`{target.get('realized_net_pct', 0.0)}`%)",
            f"- Target gap: `{target.get('target_gap_usd', 0.0)}` USD (`{target.get('target_gap_pct', 0.0)}` percentage points), status `{target.get('status', '')}`",
            "",
            "## Rotation Math",
            "",
            f"- Held edge: `{review.get('held_edge_pct', 0.0)}` (raw `{review.get('held_raw_edge_pct', 0.0)}`, ghost bias `{review.get('held_ghost_bias_pct', 0.0)}`)",
            f"- Held admission: `{review.get('held_admission_state', '') or 'unknown'}` `{review.get('held_admission_reason', '')}`",
            f"- Challenger edge: `{review.get('best_challenger_edge_pct', 0.0)}` (raw `{review.get('best_challenger_raw_edge_pct', 0.0)}`, ghost bias `{review.get('best_challenger_ghost_bias_pct', 0.0)}`)",
            f"- Edge advantage: `{review.get('edge_advantage_pct', 0.0)}`",
            f"- Required advantage: `{review.get('rotation_required_pct', 0.0)}`",
            "",
            "## Opportunity Tape",
            "",
            f"- Scans reviewed: `{payload['opportunity_tape_summary']['scans']}`",
            f"- Last scan: `{payload['opportunity_tape_summary']['last_scan_at']}`",
            f"- Decision counts: `{payload['opportunity_tape_summary']['decision_counts']}`",
            f"- Top-product counts: `{payload['opportunity_tape_summary']['top_product_counts']}`",
            "",
            "## Ghost Tournament",
            "",
            f"- Active ghosts: `{payload['ghost_tournament']['active']}`",
            "",
            "| Product | Closes | Wins | Losses | Net % | Best % | Worst % |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["ghost_tournament"]["leaders"]:
        lines.append(
            "| {product_id} | {closes} | {wins} | {losses} | {net_pct:.4f} | {best_pct} | {worst_pct} |".format(**row)
        )
    if not payload["ghost_tournament"]["leaders"]:
        lines.append("| NONE | 0 | 0 | 0 | 0.0000 |  |  |")
    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| Rank | Product | Admission | Reason | Playbook | Adj Score | Adj Edge % | Live bps | Live Streak | Ghost Bias % | Ghost Closes | Streak | 15m % | 60m % | Spread bps |",
            "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload.get("strategy_top") or []:
        lines.append(
            "| {rank} | {product_id} | {admission_state} | {admission_reason} | {playbook} | {ghost_adjusted_machinegun_score:.4f} | {ghost_adjusted_edge_over_hurdle_pct:.4f} | {live_move_bps:.4f} | {live_move_streak} | {ghost_edge_bias_pct:.4f} | {ghost_closes} | {entry_confirmation_streak}/{entry_confirmation_required} | {ret_15m_pct:.4f} | {ret_60m_pct:.4f} | {spread_bps:.2f} |".format(
                **row
            )
        )
    if payload.get("tick_error"):
        lines.extend(["", "## Tick Fallback", "", f"- `{payload['tick_error']}`"])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Coinbase spot machinegun forward-review board.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--strategy-board-path", default=str(STRATEGY_BOARD_PATH))
    parser.add_argument("--opportunity-tape-path", default=str(DEFAULT_OPPORTUNITY_TAPE_PATH))
    parser.add_argument("--json-path", default=str(JSON_PATH))
    parser.add_argument("--csv-path", default=str(CSV_PATH))
    parser.add_argument("--md-path", default=str(MD_PATH))
    parser.add_argument("--tape-limit", type=int, default=200)
    parser.add_argument("--no-live-tick", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(
        state_path=Path(args.state_path),
        strategy_path=Path(args.strategy_board_path),
        opportunity_tape_path=Path(args.opportunity_tape_path),
        no_live_tick=bool(args.no_live_tick),
        tape_limit=int(args.tape_limit),
    )
    write_reports(payload, json_path=Path(args.json_path), csv_path=Path(args.csv_path), md_path=Path(args.md_path))
    print(
        json.dumps(
            {
                "json_path": str(args.json_path),
                "csv_path": str(args.csv_path),
                "md_path": str(args.md_path),
                "decision": payload["rotation_review"].get("decision"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
