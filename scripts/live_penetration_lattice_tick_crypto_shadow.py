#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure scripts/ directory is on PYTHONPATH so bare imports work
# regardless of cwd (repo root vs scripts/ directory)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import MetaTrader5 as mt5

import live_penetration_lattice_mirror as live_mirror
import mt5_terminal_guard
from live_penetration_lattice_shadow import (
    append_jsonl,
    log_runner_exception,
    run_direct_live_exec,
    save_state,
    utc_now_iso,
)
from tick_penetration_lattice_core import (
    engine_from_args,
    load_latest_tick,
    load_recent_bars,
    load_ticks_since_with_source,
    normalize_raw_close_style,
    purge_stale_rearm_tickets,
    timeframe_seconds,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIRECT_EXEC_STATE_PATH = ROOT / "reports" / "penetration_lattice_live_crypto_exec_state.json"
DEFAULT_DIRECT_EXEC_LOG_PATH = ROOT / "reports" / "penetration_lattice_live_crypto_exec_events.jsonl"


def _record_runner_source(runner_status: dict[str, Any] | None, *, key: str, source: str) -> None:
    if runner_status is None or not str(source or "").strip():
        return
    counts = runner_status.setdefault(f"{key}_counts", {})
    counts[str(source)] = int(counts.get(str(source), 0) or 0) + 1
    runner_status[f"{key}_last"] = str(source)


def _refresh_positive_only_runner_status(runner_status: dict[str, Any] | None, symbol: str, engine: Any) -> None:
    if runner_status is None:
        return
    state = getattr(engine, "state", None)
    hold_active = bool(getattr(state, "positive_only_hold_active", False))
    runner_status["positive_only_hold_active"] = hold_active
    runner_status["positive_only_hold_symbols"] = [str(symbol)] if hold_active else []
    reason = str(getattr(state, "positive_only_hold_reason", "") or "") if hold_active else ""
    runner_status["positive_only_hold_reason"] = reason
    runner_status["positive_only_hold_reason_by_symbol"] = {str(symbol): reason} if reason else {}
    hold_since = int(getattr(state, "positive_only_hold_since", 0) or 0) if hold_active else 0
    runner_status["positive_only_hold_since_by_symbol"] = {str(symbol): hold_since} if hold_since > 0 else {}
    if hold_active:
        runner_status["status"] = "positive_only_hold_active"
    elif str(runner_status.get("status") or "") == "positive_only_hold_active":
        runner_status["status"] = ""


def is_good_session() -> bool:
    """Check if current UTC hour is within the proven FX good session window."""
    utc_hour = datetime.now(timezone.utc).hour
    return 7 <= utc_hour < 21


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def deal_net_usd(deal: dict[str, Any]) -> float:
    return (
        float(deal.get("profit", 0.0) or 0.0)
        + float(deal.get("swap", 0.0) or 0.0)
        + float(deal.get("commission", 0.0) or 0.0)
        + float(deal.get("fee", 0.0) or 0.0)
    )


def is_exit_deal(deal: dict[str, Any]) -> bool:
    entry_code = int(deal.get("entry", -1) or -1)
    exit_codes = {int(getattr(mt5, "DEAL_ENTRY_OUT", 1) or 1)}
    out_by = getattr(mt5, "DEAL_ENTRY_OUT_BY", None)
    if out_by is not None:
        exit_codes.add(int(out_by))
    return entry_code in exit_codes


def broker_position_snapshot(ticket: int, *, live_magic: int) -> dict[str, Any] | None:
    positions = mt5.positions_get(ticket=int(ticket)) or []
    for pos in positions:
        if int(getattr(pos, "magic", 0) or 0) != int(live_magic):
            continue
        return {
            "ticket": int(getattr(pos, "ticket", 0) or 0),
            "symbol": str(getattr(pos, "symbol", "") or "").upper(),
            "price_open": float(getattr(pos, "price_open", 0.0) or 0.0),
            "comment": str(getattr(pos, "comment", "") or ""),
            "time": int(getattr(pos, "time", 0) or 0),
        }
    return None


def exact_logged_deals(exec_log_path: Path, *, symbol: str) -> list[dict[str, Any]]:
    if not exec_log_path.exists():
        return []
    resolved: list[dict[str, Any]] = []
    seen_tickets: set[int] = set()
    with exec_log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            result = payload.get("result") or {}
            event = payload.get("event") or {}
            event_symbol = str(event.get("symbol") or payload.get("symbol") or "").upper()
            if event_symbol and event_symbol != str(symbol or "").upper():
                continue
            for attempt in result.get("attempts") or []:
                deal_ticket = int(attempt.get("deal", 0) or 0)
                if deal_ticket <= 0 or deal_ticket in seen_tickets:
                    continue
                seen_tickets.add(deal_ticket)
                deals = mt5.history_deals_get(ticket=deal_ticket) or []
                if deals:
                    deal = deals[-1]
                    resolved.append(
                        {
                            "ticket": int(getattr(deal, "ticket", 0) or 0),
                            "symbol": str(getattr(deal, "symbol", "") or "").upper(),
                            "entry": int(getattr(deal, "entry", -1) or -1),
                            "profit": float(getattr(deal, "profit", 0.0) or 0.0),
                            "commission": float(getattr(deal, "commission", 0.0) or 0.0),
                            "swap": float(getattr(deal, "swap", 0.0) or 0.0),
                            "fee": float(getattr(deal, "fee", 0.0) or 0.0),
                        }
                    )
                    continue
                broker_fill = result.get("broker_fill")
                if isinstance(broker_fill, dict):
                    resolved.append(
                        {
                            "ticket": int(broker_fill.get("ticket", deal_ticket) or deal_ticket),
                            "symbol": event_symbol,
                            "entry": int(broker_fill.get("entry", -1) or -1),
                            "profit": float(broker_fill.get("profit", 0.0) or 0.0),
                            "commission": float(broker_fill.get("commission", 0.0) or 0.0),
                            "swap": float(broker_fill.get("swap", 0.0) or 0.0),
                            "fee": float(broker_fill.get("fee", 0.0) or 0.0),
                        }
                    )
    return resolved


def sync_engine_to_broker(
    engine,
    *,
    exec_state: dict[str, Any],
    exec_log_path: Path,
    event_path: Path,
    live_magic: int,
    attached_live_magics: list[int] | tuple[int, ...] | set[int] | None = None,
) -> dict[str, Any]:
    current_by_key: dict[tuple[str, float], dict[str, Any]] = {}
    current_by_live_ticket: dict[int, dict[str, Any]] = {}
    for ticket in list(getattr(engine.state, "open_tickets", []) or []):
        key = (str(ticket.get("direction", "") or "").upper(), float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0))
        current_by_key[key] = ticket
        live_ticket = int(ticket.get("live_ticket", 0) or 0)
        if live_ticket > 0:
            current_by_live_ticket[live_ticket] = ticket

    def _tracked_fill_price(tracked_row: dict[str, Any], broker_pos: dict[str, Any]) -> float:
        broker_price_open = float(broker_pos.get("price_open", 0.0) or 0.0)
        if broker_price_open > 0.0:
            return broker_price_open
        tracked_fill = float(tracked_row.get("fill_price", 0.0) or 0.0)
        if tracked_fill > 0.0:
            return tracked_fill
        return float(tracked_row.get("entry_level", 0.0) or 0.0)

    broker_positions = {
        int(row.get("ticket", 0) or 0): row
        for row in live_mirror.broker_live_positions(
            symbol=engine.symbol,
            live_magic=live_magic,
            attached_live_magics=attached_live_magics,
        )
        if int(row.get("ticket", 0) or 0) > 0
    }
    symbol_norm = engine.symbol.upper()
    existing_positions = list(exec_state.get("positions") or [])
    refreshed_positions: list[dict[str, Any]] = []
    tracked_tickets: set[int] = set()
    rehydrated_tickets: list[int] = []
    dropped_tickets: list[int] = []

    for tracked in existing_positions:
        tracked_symbol = str(tracked.get("symbol", "") or "").upper()
        if tracked_symbol != symbol_norm:
            refreshed_positions.append(tracked)
            continue
        live_ticket = int(tracked.get("live_ticket", 0) or 0)
        broker_pos = broker_positions.get(live_ticket)
        if live_ticket <= 0 or not broker_pos:
            if live_ticket > 0:
                dropped_tickets.append(live_ticket)
            continue
        tracked_tickets.add(live_ticket)
        normalized = dict(tracked)
        normalized["symbol"] = symbol_norm
        normalized["direction"] = str(broker_pos.get("direction", tracked.get("direction", "")) or "").upper()
        if float(normalized.get("entry_level", 0.0) or 0.0) <= 0.0:
            existing_ticket = current_by_live_ticket.get(live_ticket, {})
            normalized["entry_level"] = float(existing_ticket.get("trigger_level", broker_pos.get("price_open", 0.0)) or 0.0)
        normalized["fill_price"] = _tracked_fill_price(normalized, broker_pos)
        normalized["broker_price_open"] = float(broker_pos.get("price_open", normalized["fill_price"]) or normalized["fill_price"])
        normalized["broker_magic"] = int(broker_pos.get("magic", normalized.get("broker_magic", live_magic)) or live_magic)
        if not str(normalized.get("position_comment", "") or "").strip():
            normalized["position_comment"] = str(broker_pos.get("comment", "") or "")
        if not str(normalized.get("opened_at", "") or "").strip():
            opened_ts = int(broker_pos.get("time", 0) or 0)
            if opened_ts > 0:
                normalized["opened_at"] = datetime.fromtimestamp(opened_ts, tz=timezone.utc).isoformat()
        refreshed_positions.append(normalized)

    for live_ticket, broker_pos in broker_positions.items():
        if live_ticket in tracked_tickets:
            continue
        existing_ticket = current_by_live_ticket.get(live_ticket, {})
        entry_level = float(existing_ticket.get("trigger_level", broker_pos.get("price_open", 0.0)) or 0.0)
        opened_ts = int(broker_pos.get("time", 0) or 0)
        refreshed_positions.append(
            {
                "symbol": symbol_norm,
                "direction": str(broker_pos.get("direction", "") or "").upper(),
                "entry_level": entry_level,
                "fill_price": _tracked_fill_price(existing_ticket, broker_pos),
                "broker_price_open": float(broker_pos.get("price_open", 0.0) or 0.0),
                "broker_magic": int(broker_pos.get("magic", live_magic) or live_magic),
                "live_ticket": live_ticket,
                "comment": str(broker_pos.get("comment", "") or ""),
                "position_comment": str(broker_pos.get("comment", "") or ""),
                "opened_at": datetime.fromtimestamp(opened_ts, tz=timezone.utc).isoformat() if opened_ts > 0 else utc_now_iso(),
            }
        )
        tracked_tickets.add(live_ticket)
        rehydrated_tickets.append(live_ticket)

    exec_state["positions"] = refreshed_positions

    aligned_open_tickets: list[dict[str, Any]] = []
    for tracked in list(exec_state.get("positions") or []):
        if str(tracked.get("symbol", "") or "").upper() != symbol_norm:
            continue
        live_ticket = int(tracked.get("live_ticket", 0) or 0)
        if live_ticket <= 0:
            continue
        broker_pos = broker_positions.get(live_ticket)
        if not broker_pos:
            continue
        direction = str(tracked.get("direction", "") or "").upper()
        trigger_level = float(tracked.get("entry_level", 0.0) or 0.0)
        fill_price = _tracked_fill_price(tracked, broker_pos)
        existing = current_by_key.get((direction, trigger_level), {})
        opened_at = parse_iso(str(tracked.get("opened_at") or ""))
        aligned_open_tickets.append(
            {
                "direction": direction,
                "trigger_level": trigger_level,
                "fill_price": fill_price,
                "opened_time": int(opened_at.timestamp()) if opened_at else int(broker_pos.get("time", 0) or 0),
                "opened_msc": int(existing.get("opened_msc", 0) or 0),
                "level_idx": int(existing.get("level_idx", engine._ticket_level_idx(direction, trigger_level)) or 0),
                "from_rearm": bool(existing.get("from_rearm", False)),
                "live_ticket": live_ticket,
                "broker_magic": int(broker_pos.get("magic", tracked.get("broker_magic", live_magic)) or live_magic),
                "position_comment": str(broker_pos.get("comment", "") or tracked.get("position_comment", "") or ""),
            }
        )

    deals = exact_logged_deals(exec_log_path, symbol=engine.symbol)
    realized_net_usd = sum(deal_net_usd(deal) for deal in deals)
    realized_closes = sum(1 for deal in deals if is_exit_deal(deal))

    old_open = list(getattr(engine.state, "open_tickets", []) or [])
    old_realized = float(getattr(engine.state, "realized_net_usd", 0.0) or 0.0)
    old_closes = int(getattr(engine.state, "realized_closes", 0) or 0)
    old_rearm_tokens = list(getattr(engine.state, "rearm_tokens", []) or [])

    old_max_open_total = int(getattr(engine.state, "max_open_total", 0) or 0)
    mops = int(getattr(engine.state, "max_open_per_side", 12) or 12)
    desired_max_open_total = max(
        old_max_open_total,
        len(aligned_open_tickets),
        mops * 2,
    )

    changed = (
        len(old_open) != len(aligned_open_tickets)
        or abs(old_realized - float(realized_net_usd)) > 1e-9
        or old_closes != int(realized_closes)
        or bool(rehydrated_tickets)
        or bool(dropped_tickets)
        or old_max_open_total != desired_max_open_total
    )
    if changed:
        engine.state.open_tickets = aligned_open_tickets
        engine.state.realized_net_usd = float(realized_net_usd)
        engine.state.realized_closes = int(realized_closes)
        # Any mismatch means the speculative rearm-token chain is suspect.
        engine.state.rearm_tokens = []
        # Keep a sane capacity floor even on non-fresh watchdog restarts that
        # reload a previously bad max_open_total=0 state snapshot.
        engine.state.max_open_total = desired_max_open_total
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "direct_live_broker_sync",
                "symbol": engine.symbol,
                "old_open_count": len(old_open),
                "new_open_count": len(aligned_open_tickets),
                "old_realized_net_usd": old_realized,
                "new_realized_net_usd": float(realized_net_usd),
                "old_realized_closes": old_closes,
                "new_realized_closes": int(realized_closes),
                "old_max_open_total": old_max_open_total,
                "new_max_open_total": desired_max_open_total,
                "cleared_rearm_tokens": len(old_rearm_tokens),
                "rehydrated_tickets": rehydrated_tickets,
                "dropped_tracked_tickets": dropped_tickets,
            },
        )
    return {
        "open_count": len(aligned_open_tickets),
        "realized_net_usd": float(realized_net_usd),
        "realized_closes": int(realized_closes),
        "changed": changed,
    }


def build_direct_live_action_sink(
    *,
    exec_state: dict[str, Any],
    exec_log_path: Path,
    live_magic: int,
    live_comment_prefix: str,
    live_volume: float,
):
    def _track_open(result: dict[str, Any], request: dict[str, Any]) -> None:
        live_ticket = int(result.get("ticket", 0) or 0)
        if not (result.get("ok") and live_ticket > 0):
            return
        positions = exec_state.setdefault("positions", [])
        for row in positions:
            if int(row.get("live_ticket", 0) or 0) == live_ticket:
                return
        positions.append(
            {
                "symbol": str(request.get("symbol", "") or "").upper(),
                "direction": str(request.get("direction", "") or "").upper(),
                "entry_level": float(request.get("trigger_level", 0.0) or 0.0),
                "fill_price": float(
                    result.get("broker_position_price_open", result.get("requested_price", request.get("fill_price", 0.0))) or 0.0
                ),
                "broker_price_open": float(
                    result.get("broker_position_price_open", result.get("requested_price", request.get("fill_price", 0.0))) or 0.0
                ),
                "live_ticket": live_ticket,
                "comment": live_mirror.short_live_comment(
                    "open_buy" if str(request.get("direction", "")).upper() == "BUY" else "open_sell",
                    comment_prefix=live_comment_prefix,
                ),
                "position_comment": str(result.get("position_comment", "") or ""),
                "opened_at": utc_now_iso(),
            }
        )

    def _drop_tracked_position(live_ticket: int) -> None:
        positions = exec_state.setdefault("positions", [])
        exec_state["positions"] = [row for row in positions if int(row.get("live_ticket", 0) or 0) != int(live_ticket)]

    def _resolve_close_ticket(request: dict[str, Any]) -> int:
        ticket_payload = request.get("ticket") or {}
        live_ticket = int(ticket_payload.get("live_ticket", 0) or 0)
        if live_ticket > 0:
            return live_ticket
        positions = exec_state.setdefault("positions", [])
        target = live_mirror.find_position_by_entry_level(
            positions,
            str(request.get("symbol", "") or "").upper(),
            str(request.get("direction", "") or "").upper(),
            float(request.get("trigger_level", 0.0) or 0.0),
        )
        return int((target or {}).get("live_ticket", 0) or 0)

    def _deal_realized_pnl(result: dict[str, Any]) -> float:
        broker_fill = result.get("broker_fill") or {}
        return deal_net_usd(broker_fill) if isinstance(broker_fill, dict) else 0.0

    def action_sink(request: dict[str, Any]) -> dict[str, Any]:
        kind = str(request.get("kind", "") or "").lower()
        if kind == "open":
            comment = live_mirror.short_live_comment(
                "open_buy" if str(request.get("direction", "")).upper() == "BUY" else "open_sell",
                comment_prefix=live_comment_prefix,
            )
            result = live_mirror.send_market_order(
                str(request.get("symbol", "") or "").upper(),
                str(request.get("direction", "") or "").upper(),
                live_volume,
                comment,
                live_magic=live_magic,
            )
            append_jsonl(exec_log_path, {"ts_utc": utc_now_iso(), "action": "open_attempt", "event": request, "result": result})
            _track_open(result, request)
            return {
                "ok": bool(result.get("ok")),
                "fill_price": float(result.get("broker_position_price_open", result.get("requested_price", request.get("fill_price", 0.0))) or 0.0),
                "live_ticket": int(result.get("ticket", 0) or 0),
                "position_comment": str(result.get("position_comment", "") or ""),
                "realized_pnl": _deal_realized_pnl(result),
            }

        if kind == "close":
            live_ticket = _resolve_close_ticket(request)
            if live_ticket <= 0:
                result = {"ok": False, "reason": "tracked_live_ticket_missing"}
                append_jsonl(exec_log_path, {"ts_utc": utc_now_iso(), "action": "close_attempt", "event": request, "result": result})
                return result
            result = live_mirror.close_live_position(
                int(live_ticket),
                live_magic=live_magic,
                comment_prefix=live_comment_prefix,
            )
            append_jsonl(exec_log_path, {"ts_utc": utc_now_iso(), "action": "close_attempt", "event": request, "result": result})
            if result.get("ok") or result.get("reason") == "position_not_found":
                _drop_tracked_position(live_ticket)
            return {
                "ok": bool(result.get("ok")),
                "fill_price": float(((result.get("broker_fill") or {}).get("price", request.get("fill_price", 0.0))) or 0.0),
                "realized_pnl": _deal_realized_pnl(result),
            }

        result = {"ok": False, "reason": f"unsupported_kind:{kind}"}
        append_jsonl(exec_log_path, {"ts_utc": utc_now_iso(), "action": "exec_action_rejected", "event": request, "result": result})
        return result

    return action_sink


def load_compatible_state(path: Path, engine) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    symbols = payload.get("symbols") or {}
    for symbol, snap in symbols.items():
        if str(symbol or "").upper() == engine.symbol.upper():
            engine.load_snapshot(snap or {})
            return


def prime_engine_fresh(engine) -> None:
    bars = load_recent_bars(engine.symbol, engine.timeframe_name, 240)
    if not bars:
        return
    engine.state.last_bar_time = int(bars[-1]["time"])
    engine.state.open_tickets = []
    engine.state.rearm_tokens = []
    engine.state.rearm_opens = 0
    engine.state.realized_closes = 0
    engine.state.realized_net_usd = 0.0
    engine.state.anchor_resets = 0
    # FIX: Set max_open_total to a reasonable default (max_open_per_side * 2)
    # instead of 0, so the engine can start opening positions immediately.
    # Previously this was 0, which blocked all opens until broker_sync corrected it —
    # but broker_sync also computed max(0, 0) = 0 when there were 0 inherited positions.
    mops = int(getattr(engine.state, "max_open_per_side", 12) or 12)
    engine.state.max_open_total = mops * 2
    engine.state.last_tick_time = 0
    engine.state.last_tick_msc = 0
    engine.prime(float(bars[-1]["close"]), int(bars[-1]["time"]))


def arm_engine_from_current_tick(engine, *, shared_price_max_age_ms: int = 0) -> None:
    tick, _source = load_latest_tick(engine.symbol, shared_price_max_age_ms=shared_price_max_age_ms)
    if not tick:
        return
    engine.state.last_tick_time = int(tick["time"])
    engine.state.last_tick_msc = int(tick["time_msc"])


def load_current_tick(symbol: str, *, shared_price_max_age_ms: int = 0) -> dict[str, Any] | None:
    tick, _source = load_latest_tick(symbol, shared_price_max_age_ms=shared_price_max_age_ms)
    return tick


def load_current_tick_with_source(symbol: str, *, shared_price_max_age_ms: int = 0) -> tuple[dict[str, Any] | None, str]:
    return load_latest_tick(symbol, shared_price_max_age_ms=shared_price_max_age_ms)


def bootstrap(
    engine,
    state_path: Path,
    event_path: Path,
    fresh_start: bool,
    metadata: dict[str, Any],
    *,
    shared_price_max_age_ms: int = 0,
) -> None:
    if state_path.exists() and not fresh_start:
        load_compatible_state(state_path, engine)
        removed = []
        if bool(metadata.get("direct_live")):
            removed = purge_stale_rearm_tickets(engine)
        if removed:
            save_state(state_path, {engine.symbol: engine}, metadata=metadata)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "purged_stale_rearm_tickets",
                    "symbols": [engine.symbol],
                    "removed": removed,
                    **metadata,
                },
            )
        return
    prime_engine_fresh(engine)
    if fresh_start:
        arm_engine_from_current_tick(engine, shared_price_max_age_ms=shared_price_max_age_ms)
    save_state(state_path, {engine.symbol: engine}, metadata=metadata)
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "fresh_start_prime" if fresh_start else "bootstrap_complete",
            "symbols": [engine.symbol],
            **metadata,
        },
    )


def run_once(
    engine,
    *,
    state_path: Path,
    event_path: Path,
    metadata: dict[str, Any],
    direct_exec: dict[str, Any] | None,
    runner_status: dict[str, Any] | None,
    session_gate: bool = False,
    shared_price_max_age_ms: int = 0,
) -> None:
    if session_gate and not is_good_session():
        utc_hour = datetime.now(timezone.utc).hour
        if direct_exec:
            sync_engine_to_broker(
                engine,
                exec_state=direct_exec["state"],
                exec_log_path=direct_exec["log_path"],
                event_path=event_path,
                live_magic=int(direct_exec["live_magic"]),
                attached_live_magics=direct_exec.get("attached_live_magics"),
            )
            live_mirror.save_state(direct_exec["state_path"], direct_exec["state"])
        if runner_status is not None:
            runner_status["heartbeat_at"] = utc_now_iso()
            runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
            runner_status["consecutive_exceptions"] = 0
            runner_status["session_gated"] = True
            runner_status["gated_hour"] = utc_hour
        _refresh_positive_only_runner_status(runner_status, engine.symbol, engine)
        save_state(state_path, {engine.symbol: engine}, metadata=metadata, runner=runner_status)
        return
    if runner_status is not None:
        runner_status["session_gated"] = False
        runner_status["gated_hour"] = None
    action_sink = None
    if direct_exec:
        sync_engine_to_broker(
            engine,
            exec_state=direct_exec["state"],
            exec_log_path=direct_exec["log_path"],
            event_path=event_path,
            live_magic=int(direct_exec["live_magic"]),
            attached_live_magics=direct_exec.get("attached_live_magics"),
        )
        action_sink = build_direct_live_action_sink(
            exec_state=direct_exec["state"],
            exec_log_path=direct_exec["log_path"],
            live_magic=int(direct_exec["live_magic"]),
            live_comment_prefix=str(direct_exec["live_comment_prefix"]),
            live_volume=float(direct_exec["live_volume"]),
        )
    ticks, ticks_source = load_ticks_since_with_source(
        engine.symbol,
        int(engine.state.last_tick_msc or 0),
        lookback_seconds=max(120, timeframe_seconds(engine.timeframe_name) * 3),
        shared_price_max_age_ms=shared_price_max_age_ms,
    )
    for tick in ticks:
        if not isinstance(tick, dict):
            continue
        tick["tick_history_source_last"] = str(ticks_source or "")
    live_tick, live_tick_source = load_current_tick_with_source(
        engine.symbol,
        shared_price_max_age_ms=shared_price_max_age_ms,
    )
    if live_tick is not None:
        live_tick["latest_tick_source_last"] = str(live_tick_source or "")
        live_tick["tick_history_source_last"] = str(ticks_source or "")
        live_tick_msc = int(live_tick["time_msc"])
        latest_loaded_msc = int(ticks[-1]["time_msc"]) if ticks else int(engine.state.last_tick_msc or 0)
        if live_tick_msc > latest_loaded_msc:
            ticks.append(live_tick)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "tick_history_fallback",
                    "symbol": engine.symbol,
                    "reason": f"{live_tick_source}_newer_than_loaded_history",
                    "live_tick_source": live_tick_source,
                    "last_tick_msc_before": int(engine.state.last_tick_msc or 0),
                    "latest_loaded_msc": latest_loaded_msc,
                    "live_tick_msc": live_tick_msc,
                    "bid": float(live_tick.get("bid", 0.0) or 0.0),
                    "ask": float(live_tick.get("ask", 0.0) or 0.0),
                    "last": float(live_tick.get("last", 0.0) or 0.0),
                },
            )
            _record_runner_source(runner_status, key="latest_tick_append_source", source=live_tick_source)
    for tick in ticks:
        if not isinstance(tick, dict):
            continue
        tick["latest_tick_source_last"] = str(tick.get("latest_tick_source_last", live_tick_source) or "")
        tick["tick_history_source_last"] = str(tick.get("tick_history_source_last", ticks_source) or "")
    _record_runner_source(runner_status, key="tick_history_source", source=ticks_source)
    _record_runner_source(runner_status, key="latest_tick_source", source=live_tick_source)
    if ticks:
        engine.process_ticks(ticks, action_sink=action_sink, event_path=event_path, emit=True)
    if runner_status is not None:
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
    _refresh_positive_only_runner_status(runner_status, engine.symbol, engine)
    save_state(state_path, {engine.symbol: engine}, metadata=metadata, runner=runner_status)
    if direct_exec:
        run_direct_live_exec(
            direct_exec["state"],
            source_state_path=state_path,
            source_event_path=event_path,
            exec_state_path=direct_exec["state_path"],
            exec_log_path=direct_exec["log_path"],
            allowed_symbols=direct_exec["allowed_symbols"],
            live_magic=direct_exec["live_magic"],
            attached_live_magics=direct_exec.get("attached_live_magics"),
            live_comment_prefix=direct_exec["live_comment_prefix"],
            live_volume=direct_exec["live_volume"],
        )
        sync_engine_to_broker(
            engine,
            exec_state=direct_exec["state"],
            exec_log_path=direct_exec["log_path"],
            event_path=event_path,
            live_magic=int(direct_exec["live_magic"]),
            attached_live_magics=direct_exec.get("attached_live_magics"),
        )
        save_state(state_path, {engine.symbol: engine}, metadata=metadata, runner=runner_status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tick-native crypto live/shadow runner using executable-side MT5 ticks.")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--timeframe", required=True, choices=["M1", "M5", "M15", "H1", "H4"],
                        help="REQUIRED: M5 for M5 Warp, M15 for M15 Warp, H1 for H1. Defaults removed to prevent silent money drain.")
    parser.add_argument("--step", type=float, required=True)
    parser.add_argument("--step-buy", type=float, default=None,
                        help="Asymmetric buy step. If not set, uses --step value.")
    parser.add_argument("--step-sell", type=float, default=None,
                        help="Asymmetric sell step. If not set, uses --step value.")
    parser.add_argument("--disable-dynamic-geometry", action="store_true",
                        help="Freeze the configured geometry and skip structure/box-driven step changes.")
    parser.add_argument("--proven-step-ceiling", type=float, default=0.0,
                        help="Maximum allowed step for adaptive geometry. Zero means no ceiling.")
    parser.add_argument("--proven-step-buy-ceiling", type=float, default=0.0,
                        help="Maximum allowed BUY step for adaptive geometry. Zero falls back to the global ceiling.")
    parser.add_argument("--proven-step-sell-ceiling", type=float, default=0.0,
                        help="Maximum allowed SELL step for adaptive geometry. Zero falls back to the global ceiling.")
    parser.add_argument("--max-open-per-side", type=int, default=30)
    parser.add_argument("--raw-close-alpha", type=float, default=1.0)
    parser.add_argument("--raw-close-style", default="all_profitable")
    parser.add_argument("--raw-handoff-steps", type=float, default=0.5)
    parser.add_argument("--raw-rearm-variant", default="rearm_lvl2_exc1")
    parser.add_argument("--raw-rearm-cooldown-bars", type=int, default=0)
    parser.add_argument("--raw-rearm-momentum-gate", action="store_true")
    parser.add_argument("--raw-sell-gap", type=int, default=1)
    parser.add_argument("--raw-buy-gap", type=int, default=1)
    parser.add_argument("--min-positive-close-profit-usd", type=float, default=0.0,
                        help="Minimum projected executable profit required before ordinary close paths may fire. Zero keeps the old cross-zero behavior.")
    parser.add_argument("--positive-only-closes", action="store_true",
                        help="Never realize a losing emergency close; hold the book, stop opening, and wait for profitable exits.")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--direct-live", action="store_true")
    parser.add_argument("--direct-exec-state-path", default=str(DEFAULT_DIRECT_EXEC_STATE_PATH))
    parser.add_argument("--direct-exec-log-path", default=str(DEFAULT_DIRECT_EXEC_LOG_PATH))
    parser.add_argument("--live-magic", type=int, default=live_mirror.DEFAULT_LIVE_MAGIC)
    parser.add_argument("--attach-broker-magic", action="append", type=int, default=[],
                        help="Additional broker magics this live lane should adopt into its managed inventory.")
    parser.add_argument("--live-comment-prefix", default=live_mirror.DEFAULT_LIVE_COMMENT_PREFIX)
    parser.add_argument("--live-volume", type=float, default=live_mirror.DEFAULT_LIVE_VOLUME)
    parser.add_argument("--shared-price-max-age-ms", type=int, default=0)
    parser.add_argument("--session-gate", action="store_true",
                        help="Skip tick processing during off-session hours (21:00-07:00 UTC). "
                             "Good session is 07:00-21:00 UTC (London, overlap, NY).")
    parser.add_argument("--max-floating-loss-usd", type=float, default=-10.0)
    parser.add_argument("--max-lattice-window-bars", type=int, default=240)
    parser.add_argument("--max-entry-spread-ratio", type=float, default=0.0,
                        help="Block new opens when current spread consumes too much of the base step. Zero disables the guard.")
    parser.add_argument("--liquidity-gap-spread-multiplier", type=float, default=0.0,
                        help="Block opens only when spread blows out relative to recent spread/step baseline. Zero disables the adaptive guard.")
    parser.add_argument("--liquidity-gap-spread-lookback", type=int, default=0,
                        help="Tick count lookback for liquidity-gap spread baseline.")
    parser.add_argument("--liquidity-gap-spread-floor-ratio", type=float, default=0.0,
                        help="Absolute spread/step floor for the liquidity-gap spread guard.")
    parser.add_argument("--liquidity-gap-spread-max-ratio", type=float, default=0.0,
                        help="Optional hard ceiling for the liquidity-gap spread guard. Zero disables the cap.")
    parser.add_argument("--breakout-buffer-pips", type=float, default=0.0)
    # Escape hatch (Tier 0 offensive escape)
    parser.add_argument("--escape-hatch", action="store_true",
                        help="Enable escape hatch: close stale unprofitable positions at ~$0 cost.")
    parser.add_argument("--escape-max-bars", type=int, default=0,
                        help="Max bars a position can be open without profit before breakeven escape.")
    parser.add_argument("--escape-max-loss", type=float, default=0.0,
                        help="Max acceptable loss for breakeven escape ($).")
    # Cluster-aware escape (max-profit lattice optimization)
    parser.add_argument("--cluster-aware-escape", action="store_true",
                        help="Enable cluster-aware escape: group same-fill positions and apply threshold to cluster total.")
    parser.add_argument("--cluster-fill-tolerance", type=float, default=0.01,
                        help="Max price difference to consider positions in same cluster (default $0.01).")
    parser.add_argument("--guard-open-admission", action="store_true",
                        help="Guard new same-side opens until existing side inventory shows recovery.")
    parser.add_argument("--suppress-additional-levels-after-burst", action="store_true",
                        help="Stop stacking new opens once same-bar/tick burst concentration reaches the configured threshold.")
    parser.add_argument("--burst-open-threshold", type=int, default=2,
                        help="Burst count that triggers suppression of additional opens within the same tick/bar (default 2).")
    parser.add_argument("--adaptive-overlay-autopilot", action="store_true",
                        help="Auto-arm guarded admission, cluster-aware escape, and burst suppression when burst concentration proves the lane needs them.")
    # Offensive extreme closure (affordability-gated Tier 0)
    parser.add_argument("--offensive-closure", action="store_true",
                        help="Enable affordability-gated offensive extreme closure.")
    parser.add_argument("--offensive-safety-margin-usd", type=float, default=2.0,
                        help="Dollar buffer required before subsidized cut (default $2).")
    parser.add_argument("--offensive-safety-margin-pct", type=float, default=0.20,
                        help="Percentage of realized_net buffer (default 20%%).")
    parser.add_argument("--offensive-cut-cooldown-bars", type=int, default=5,
                        help="Bars between offensive cuts (default 5).")
    parser.add_argument("--offensive-breakeven-band-usd", type=float, default=0.50,
                        help="Max profit to still consider barely profitable for cut (default $0.50).")
    parser.add_argument("--offensive-budget-share", type=float, default=0.25,
                        help="Max share of positive close-ticket harvest spendable on offensive cuts (default 25%%).")
    parser.add_argument("--close-at-float-zero", action="store_true",
                        help="Close ALL profitable positions when total floating PnL >= 0 (portfolio-state trigger).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(
        mt5_module=mt5,
        require_trade_allowed=bool(args.direct_live),
    )
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    try:
        symbol = str(args.symbol or "").upper()
        attached_broker_magics = sorted(
            {
                int(magic)
                for magic in list(args.attach_broker_magic or [])
                if int(magic or 0) > 0 and int(magic or 0) != int(args.live_magic)
            }
        )
        engine = engine_from_args(
            symbol=symbol,
            timeframe_name=str(args.timeframe).upper(),
            step=float(args.step),
            max_open_per_side=int(args.max_open_per_side),
            variant_name=str(args.raw_rearm_variant or ""),
            close_alpha=max(0.0, min(1.0, float(args.raw_close_alpha))),
            close_style=normalize_raw_close_style(str(args.raw_close_style or "")),
            momentum_gate=bool(args.raw_rearm_momentum_gate),
            cooldown_bars=max(0, int(args.raw_rearm_cooldown_bars)),
            sell_gap=max(0, int(args.raw_sell_gap)),
            buy_gap=max(0, int(args.raw_buy_gap)),
            step_buy=float(args.step_buy) if args.step_buy is not None else None,
            step_sell=float(args.step_sell) if args.step_sell is not None else None,
            max_floating_loss_usd=float(args.max_floating_loss_usd),
            max_lattice_window_bars=int(args.max_lattice_window_bars),
            max_entry_spread_ratio=max(0.0, float(args.max_entry_spread_ratio)),
            liquidity_gap_spread_multiplier=max(0.0, float(args.liquidity_gap_spread_multiplier)),
            liquidity_gap_spread_lookback=max(0, int(args.liquidity_gap_spread_lookback)),
            liquidity_gap_spread_floor_ratio=max(0.0, float(args.liquidity_gap_spread_floor_ratio)),
            liquidity_gap_spread_max_ratio=max(0.0, float(args.liquidity_gap_spread_max_ratio)),
            breakout_buffer_pips=float(args.breakout_buffer_pips),
            escape_bars=int(args.escape_max_bars) if args.escape_hatch else 0,
            escape_threshold_usd=float(args.escape_max_loss) if args.escape_hatch else 0.0,
            cluster_aware_escape=bool(args.cluster_aware_escape),
            cluster_fill_tolerance=float(args.cluster_fill_tolerance),
            guard_open_admission=bool(args.guard_open_admission),
            suppress_additional_levels_after_burst=bool(args.suppress_additional_levels_after_burst),
            burst_open_threshold=max(1, int(args.burst_open_threshold)),
            adaptive_overlay_autopilot=bool(args.adaptive_overlay_autopilot),
            offensive_closure_enabled=bool(args.offensive_closure),
            offensive_safety_margin_usd=float(args.offensive_safety_margin_usd) if args.offensive_closure else 0.0,
            offensive_safety_margin_pct=float(args.offensive_safety_margin_pct) if args.offensive_closure else 0.0,
            offensive_cut_cooldown_bars=int(args.offensive_cut_cooldown_bars) if args.offensive_closure else 0,
            offensive_breakeven_band_usd=float(args.offensive_breakeven_band_usd) if args.offensive_closure else 0.0,
            offensive_budget_share=float(args.offensive_budget_share) if args.offensive_closure else 0.0,
            allow_dynamic_geometry=not bool(args.disable_dynamic_geometry),
            proven_step_ceiling=float(args.proven_step_ceiling) if args.proven_step_ceiling > 0 else 0.0,
            proven_step_buy_ceiling=float(args.proven_step_buy_ceiling) if args.proven_step_buy_ceiling > 0 else 0.0,
            proven_step_sell_ceiling=float(args.proven_step_sell_ceiling) if args.proven_step_sell_ceiling > 0 else 0.0,
            min_positive_close_profit_usd=max(0.0, float(args.min_positive_close_profit_usd)),
            positive_only_closes=bool(args.positive_only_closes),
            close_at_float_zero=bool(args.close_at_float_zero),
        )
        state_path = Path(args.state_path)
        event_path = Path(args.event_path)
        metadata = {
            "symbols": [symbol],
            "timeframe": str(args.timeframe).upper(),
            "step": float(args.step),
            "step_buy": float(args.step_buy) if args.step_buy is not None else float(args.step),
            "step_sell": float(args.step_sell) if args.step_sell is not None else float(args.step),
            "declared_step_buy_price_units": float(engine.base_step_buy_px),
            "declared_step_sell_price_units": float(engine.base_step_sell_px),
            "declared_step_price_units": (
                float(engine.base_step_buy_px)
                if abs(float(engine.base_step_buy_px) - float(engine.base_step_sell_px)) <= 1e-12
                else float(engine.base_step_px)
            ),
            "dynamic_geometry_enabled": not bool(args.disable_dynamic_geometry),
            "proven_step_ceiling": float(args.proven_step_ceiling) if args.proven_step_ceiling > 0 else None,
            "proven_step_buy_ceiling": float(args.proven_step_buy_ceiling) if args.proven_step_buy_ceiling > 0 else None,
            "proven_step_sell_ceiling": float(args.proven_step_sell_ceiling) if args.proven_step_sell_ceiling > 0 else None,
            "max_open_per_side": int(args.max_open_per_side),
            "raw_close_alpha": max(0.0, min(1.0, float(args.raw_close_alpha))),
            "raw_close_style": normalize_raw_close_style(str(args.raw_close_style or "")),
            "raw_handoff_steps": float(args.raw_handoff_steps),
            "raw_rearm_variant": str(args.raw_rearm_variant),
            "raw_rearm_cooldown_bars": int(args.raw_rearm_cooldown_bars),
            "raw_rearm_momentum_gate": bool(args.raw_rearm_momentum_gate),
            "raw_sell_gap": int(args.raw_sell_gap),
            "raw_buy_gap": int(args.raw_buy_gap),
            "min_positive_close_profit_usd": max(0.0, float(args.min_positive_close_profit_usd)),
            "positive_only_closes": bool(args.positive_only_closes),
            "tick_native": True,
            "live_close_realism_mode": "tick_native",
            "live_open_realism_mode": "tick_native",
            "direct_live": bool(args.direct_live),
            "live_magic": int(args.live_magic),
            "attached_broker_magics": attached_broker_magics,
            "live_comment_prefix": str(args.live_comment_prefix),
            "live_volume": float(args.live_volume),
            "session_gate": bool(args.session_gate),
            "shared_price_max_age_ms": max(0, int(args.shared_price_max_age_ms)),
            "max_floating_loss_usd": float(args.max_floating_loss_usd),
            "max_lattice_window_bars": int(args.max_lattice_window_bars),
            "max_entry_spread_ratio": max(0.0, float(args.max_entry_spread_ratio)),
            "liquidity_gap_spread_multiplier": max(0.0, float(args.liquidity_gap_spread_multiplier)),
            "liquidity_gap_spread_lookback": max(0, int(args.liquidity_gap_spread_lookback)),
            "liquidity_gap_spread_floor_ratio": max(0.0, float(args.liquidity_gap_spread_floor_ratio)),
            "liquidity_gap_spread_max_ratio": max(0.0, float(args.liquidity_gap_spread_max_ratio)),
            "breakout_buffer_pips": float(args.breakout_buffer_pips),
            "escape_hatch_enabled": bool(args.escape_hatch),
            "escape_max_bars": int(args.escape_max_bars) if args.escape_hatch else 0,
            "escape_max_loss": float(args.escape_max_loss) if args.escape_hatch else 0.0,
            "cluster_aware_escape": bool(args.cluster_aware_escape),
            "cluster_fill_tolerance": float(args.cluster_fill_tolerance),
            "guard_open_admission": bool(args.guard_open_admission),
            "suppress_additional_levels_after_burst": bool(args.suppress_additional_levels_after_burst),
            "burst_open_threshold": max(1, int(args.burst_open_threshold)),
            "adaptive_overlay_autopilot": bool(args.adaptive_overlay_autopilot),
            "offensive_closure_enabled": bool(args.offensive_closure),
            "offensive_safety_margin_usd": float(args.offensive_safety_margin_usd) if args.offensive_closure else 0.0,
            "offensive_safety_margin_pct": float(args.offensive_safety_margin_pct) if args.offensive_closure else 0.0,
            "offensive_cut_cooldown_bars": int(args.offensive_cut_cooldown_bars) if args.offensive_closure else 0,
            "offensive_breakeven_band_usd": float(args.offensive_breakeven_band_usd) if args.offensive_closure else 0.0,
            "offensive_budget_share": float(args.offensive_budget_share) if args.offensive_closure else 0.0,
            "mt5_connection": mt5_connection,
        }
        runner_status = {
            "pid": os.getpid(),
            "script": Path(__file__).name,
            "started_at": utc_now_iso(),
            "poll_seconds": max(1.0, float(args.poll_seconds)),
            "heartbeat_at": None,
            "last_successful_run_at": None,
            "consecutive_exceptions": 0,
            "last_exception_at": None,
            "last_exception_type": "",
            "last_exception_message": "",
            "mt5_identity_ok": bool(mt5_connection.get("identity_ok")),
            "mt5_terminal_path": str(mt5_connection.get("terminal_path") or ""),
            "mt5_login": int(mt5_connection.get("login") or 0),
            "mt5_server": str(mt5_connection.get("server") or ""),
            "positive_only_closes": bool(args.positive_only_closes),
        }
        bootstrap(
            engine,
            state_path,
            event_path,
            bool(args.fresh_start),
            metadata,
            shared_price_max_age_ms=max(0, int(args.shared_price_max_age_ms)),
        )
        direct_exec = None
        if args.direct_live:
            exec_state_path = Path(args.direct_exec_state_path)
            exec_log_path = Path(args.direct_exec_log_path)
            direct_exec = {
                "state": live_mirror.load_state(exec_state_path),
                "state_path": exec_state_path,
                "log_path": exec_log_path,
                "allowed_symbols": {symbol},
                "live_magic": metadata["live_magic"],
                "attached_live_magics": metadata["attached_broker_magics"],
                "live_comment_prefix": metadata["live_comment_prefix"],
                "live_volume": metadata["live_volume"],
            }
        try:
            run_once(
                engine,
                state_path=state_path,
                event_path=event_path,
                metadata=metadata,
                direct_exec=direct_exec,
                runner_status=runner_status,
                session_gate=bool(args.session_gate),
                shared_price_max_age_ms=max(0, int(args.shared_price_max_age_ms)),
            )
        except Exception as exc:
            runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
            runner_status["last_exception_at"] = utc_now_iso()
            runner_status["last_exception_type"] = type(exc).__name__
            runner_status["last_exception_message"] = str(exc)
            log_runner_exception(event_path, exc, phase="initial_run_once")
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once(
                    engine,
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    direct_exec=direct_exec,
                    runner_status=runner_status,
                    session_gate=bool(args.session_gate),
                    shared_price_max_age_ms=max(0, int(args.shared_price_max_age_ms)),
                )
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
