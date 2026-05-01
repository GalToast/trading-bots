#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import REARM_VARIANTS, RearmVariant
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, vwap_anchor
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from penetration_lattice_lab_v3_bounded import recent_range
from shared_price_feeder import read_cached_price, read_cached_ticks_since
from hungry_hippo_tier0_offensive_escape import check_offensive_escape


VOLUME = 0.01
COPY_TICKS_ALL = getattr(mt5, "COPY_TICKS_ALL", 0)
TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
}
TIMEFRAME_MT5 = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}
RAW_CLOSE_STYLES = {
    "outer",
    "inner",
    "all_profitable",
    "harvest_inner_hold_frontier",
    "stack_depth_scaled_gap",
    "range_sweep_trend_reclaim",
    "handoff_then_trail_75",
}


ActionSink = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class TickTicket:
    direction: str
    trigger_level: float
    fill_price: float
    opened_time: int
    opened_msc: int = 0
    level_idx: int = 0
    from_rearm: bool = False
    live_ticket: int = 0
    position_comment: str = ""
    base_step_px_at_open: float = 0.0
    spread_px_at_open: float = 0.0
    entry_context: str = ""
    session_bucket_at_open: str = ""
    regime_at_entry: str = ""
    latest_tick_source_last: str = ""
    tick_history_source_last: str = ""
    side_open_count_at_open: int = 0
    total_open_count_at_open: int = 0
    same_tick_open_burst_count_at_open: int = 0
    same_bar_open_burst_count_at_open: int = 0
    anchor_distance_px_at_open: float = 0.0
    max_favorable_excursion_pnl: float = 0.0
    max_adverse_excursion_pnl: float = 0.0
    peak_pnl_before_exit: float = 0.0
    first_green_seen: bool = False
    first_green_time: int = 0
    first_green_msc: int = 0
    stop_price: float | None = None
    best_price: float | None = None
    reclaimed_trigger_level_seen: bool = False
    retraced_0_25x_step_seen: bool = False
    retraced_0_5x_step_seen: bool = False


def deserialize_tick_ticket(ticket: dict[str, Any]) -> TickTicket:
    normalized = serialize_tick_ticket(ticket)
    return TickTicket(
        **{
            field_name: normalized.get(field_name)
            for field_name in TickTicket.__dataclass_fields__
        }
    )


@dataclass
class TickRearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until_time: int = 0
    anticipatory: bool = False
    created_time: int = 0
    armed_at_time: int = 0


@dataclass
class TickEngineState:
    symbol: str
    timeframe: str
    mode: str = "tick_stateful_rearm"
    anchor: float = 0.0
    next_sell_level: float = 0.0
    next_buy_level: float = 0.0
    open_tickets: list[dict[str, Any]] = field(default_factory=list)
    rearm_tokens: list[dict[str, Any]] = field(default_factory=list)
    rearm_opens: int = 0
    realized_net_usd: float = 0.0
    realized_closes: int = 0
    offensive_positive_close_ticket_profit_usd: float = 0.0
    offensive_spend_usd: float = 0.0
    last_offensive_close_bar_time: int = 0
    anchor_resets: int = 0
    anchor_resets_flat: int = 0
    anchor_resets_risk: int = 0
    max_open_total: int = 0
    lattice_started_time: int = 0
    last_tick_time: int = 0
    last_tick_msc: int = 0
    last_bar_time: int = 0
    max_floating_loss_usd: float = -15.0
    max_lattice_window_bars: int = 240
    breakout_buffer_pips: float = 0.0
    breakout_kill: float = 0.0
    escape_bars: int = 0
    escape_threshold_usd: float = 0.0
    # Cluster-aware escape: group same-fill positions and apply threshold to cluster total
    # When positions share the same fill price, they recover together — escaping individually
    # realizes losses at the same bad price. Set to True to enable cluster-aware escape.
    cluster_aware_escape: bool = False
    # Cluster fill price tolerance: positions within this px are considered same cluster
    cluster_fill_tolerance: float = 0.01
    guard_open_admission: bool = False
    suppress_additional_levels_after_burst: bool = False
    burst_open_threshold: int = 2
    max_entry_spread_ratio: float = 0.0
    liquidity_gap_spread_multiplier: float = 0.0
    liquidity_gap_spread_lookback: int = 0
    liquidity_gap_spread_floor_ratio: float = 0.0
    liquidity_gap_spread_max_ratio: float = 0.0
    adaptive_overlay_autopilot: bool = False
    adaptive_overlay_autopilot_triggered: bool = False
    adaptive_overlay_autopilot_triggered_time: int = 0
    adaptive_overlay_autopilot_reason: str = ""
    first_path_close_seen: bool = False
    first_path_close_time: int = 0
    first_path_close_action: str = ""
    first_path_close_realized_pnl: float | None = None
    first_path_verdict: str = ""
    positive_only_closes: bool = False
    positive_only_hold_active: bool = False
    positive_only_hold_reason: str = ""
    positive_only_hold_since: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def spread_px_from_tick(tick: dict[str, Any]) -> float:
    bid = float(tick.get("bid", 0.0) or 0.0)
    ask = float(tick.get("ask", 0.0) or 0.0)
    return max(0.0, ask - bid)


def session_bucket_for_epoch(epoch_seconds: int) -> str:
    hour = datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).hour
    return "good_session" if 7 <= hour < 21 else "off_session"


def serialize_tick_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "direction": str(ticket.get("direction", "") or "").upper(),
        "entry_price": float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0),
        "trigger_level": float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0),
        "fill_price": float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("entry_price", 0.0))) or 0.0),
        "entry_fill_price": float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("entry_price", 0.0))) or 0.0),
        "opened_time": int(ticket.get("opened_time", 0) or 0),
        "opened_msc": int(ticket.get("opened_msc", 0) or 0),
        "level_idx": int(ticket.get("level_idx", 0) or 0),
        "from_rearm": bool(ticket.get("from_rearm", False)),
        "live_ticket": int(ticket.get("live_ticket", 0) or 0),
        "position_comment": str(ticket.get("position_comment", "") or ""),
        "base_step_px_at_open": float(ticket.get("base_step_px_at_open", 0.0) or 0.0),
        "spread_px_at_open": float(ticket.get("spread_px_at_open", 0.0) or 0.0),
        "entry_context": str(ticket.get("entry_context", "") or ""),
        "session_bucket_at_open": str(ticket.get("session_bucket_at_open", "") or ""),
        "regime_at_entry": str(ticket.get("regime_at_entry", "") or ""),
        "latest_tick_source_last": str(ticket.get("latest_tick_source_last", "") or ""),
        "tick_history_source_last": str(ticket.get("tick_history_source_last", "") or ""),
        "side_open_count_at_open": int(ticket.get("side_open_count_at_open", 0) or 0),
        "total_open_count_at_open": int(ticket.get("total_open_count_at_open", 0) or 0),
        "same_tick_open_burst_count_at_open": int(ticket.get("same_tick_open_burst_count_at_open", 0) or 0),
        "same_bar_open_burst_count_at_open": int(ticket.get("same_bar_open_burst_count_at_open", 0) or 0),
        "anchor_distance_px_at_open": float(ticket.get("anchor_distance_px_at_open", 0.0) or 0.0),
        "max_favorable_excursion_pnl": float(ticket.get("max_favorable_excursion_pnl", 0.0) or 0.0),
        "max_adverse_excursion_pnl": float(ticket.get("max_adverse_excursion_pnl", 0.0) or 0.0),
        "peak_pnl_before_exit": float(ticket.get("peak_pnl_before_exit", 0.0) or 0.0),
        "first_green_seen": bool(ticket.get("first_green_seen", False)),
        "first_green_time": int(ticket.get("first_green_time", 0) or 0),
        "first_green_msc": int(ticket.get("first_green_msc", 0) or 0),
        "stop_price": float(ticket.get("stop_price")) if ticket.get("stop_price") is not None else None,
        "best_price": float(ticket.get("best_price")) if ticket.get("best_price") is not None else None,
        "reclaimed_trigger_level_seen": bool(ticket.get("reclaimed_trigger_level_seen", False)),
        "retraced_0_25x_step_seen": bool(ticket.get("retraced_0_25x_step_seen", False)),
        "retraced_0_5x_step_seen": bool(ticket.get("retraced_0_5x_step_seen", False)),
    }


def serialize_rearm_token(token: dict[str, Any]) -> dict[str, Any]:
    return {
        "direction": str(token.get("direction", "") or "").upper(),
        "level": float(token.get("level", token.get("entry_price", 0.0)) or 0.0),
        "level_idx": int(token.get("level_idx", 0) or 0),
        "armed": bool(token.get("armed", False)),
        "cooldown_until_time": int(token.get("cooldown_until_time", 0) or 0),
        "anticipatory": bool(token.get("anticipatory", False)),
        "created_time": int(token.get("created_time", 0) or 0),
        "armed_at_time": int(token.get("armed_at_time", 0) or 0),
    }


def classify_entry_context(*, from_rearm: bool, session_bucket: str, spread_px: float, base_step_px: float) -> str:
    spread_ratio = (float(spread_px) / float(base_step_px)) if float(base_step_px) > 0.0 else 0.0
    if spread_ratio >= 0.33:
        spread_band = "wide_spread"
    elif spread_ratio >= 0.15:
        spread_band = "normal_spread"
    else:
        spread_band = "tight_spread"
    entry_type = "rearm" if from_rearm else "main"
    return f"{entry_type}|{session_bucket}|{spread_band}"


def classify_regime_at_entry(
    *,
    session_bucket: str,
    spread_px: float,
    base_step_px: float,
    same_tick_open_burst_count: int,
    same_bar_open_burst_count: int,
) -> str:
    spread_ratio = (float(spread_px) / float(base_step_px)) if float(base_step_px) > 0.0 else 0.0
    if int(same_tick_open_burst_count or 0) >= 2:
        return "burst_expansion"
    if int(same_bar_open_burst_count or 0) >= 4:
        return "clustered_expansion"
    if str(session_bucket or "") != "good_session" and spread_ratio >= 0.33:
        return "thin_off_session"
    if spread_ratio >= 0.33:
        return "wide_spread_stress"
    if spread_ratio <= 0.15:
        return "orderly_reversion"
    return "normal_reversion"


def entry_spread_ratio(*, tick: dict[str, Any], base_step_px: float) -> tuple[float, float]:
    spread_px = spread_px_from_tick(tick)
    if float(base_step_px) <= 0.0:
        return spread_px, 0.0
    return spread_px, max(0.0, float(spread_px) / float(base_step_px))


def initialize_ticket_telemetry(
    ticket: TickTicket,
    *,
    tick: dict[str, Any],
    anchor: float,
    base_step_px: float,
    side_open_count: int,
    total_open_count: int,
    same_tick_open_burst_count: int,
    same_bar_open_burst_count: int,
) -> None:
    spread_px = spread_px_from_tick(tick)
    session_bucket = session_bucket_for_epoch(int(ticket.opened_time or tick.get("time", 0) or 0))
    ticket.base_step_px_at_open = float(base_step_px)
    ticket.spread_px_at_open = float(spread_px)
    ticket.session_bucket_at_open = session_bucket
    ticket.entry_context = classify_entry_context(
        from_rearm=bool(ticket.from_rearm),
        session_bucket=session_bucket,
        spread_px=spread_px,
        base_step_px=base_step_px,
    )
    ticket.regime_at_entry = classify_regime_at_entry(
        session_bucket=session_bucket,
        spread_px=spread_px,
        base_step_px=base_step_px,
        same_tick_open_burst_count=same_tick_open_burst_count,
        same_bar_open_burst_count=same_bar_open_burst_count,
    )
    ticket.latest_tick_source_last = str(tick.get("latest_tick_source_last", "") or "")
    ticket.tick_history_source_last = str(tick.get("tick_history_source_last", "") or "")
    ticket.side_open_count_at_open = int(side_open_count)
    ticket.total_open_count_at_open = int(total_open_count)
    ticket.same_tick_open_burst_count_at_open = int(same_tick_open_burst_count)
    ticket.same_bar_open_burst_count_at_open = int(same_bar_open_burst_count)
    ticket.anchor_distance_px_at_open = abs(float(ticket.trigger_level) - float(anchor))


def current_open_burst_counts(
    tickets: list[TickTicket],
    *,
    tick_time: int,
    tick_msc: int,
    timeframe_name: str,
    direction: str | None = None,
) -> tuple[int, int]:
    normalized_direction = str(direction or "").upper()
    scoped_tickets = tickets
    if normalized_direction:
        scoped_tickets = [ticket for ticket in tickets if ticket.direction == normalized_direction]
    same_tick_open_burst_count = sum(1 for ticket in scoped_tickets if int(ticket.opened_msc or 0) == int(tick_msc or 0))
    current_bar = bucket_start(int(tick_time or 0), timeframe_name)
    same_bar_open_burst_count = sum(
        1
        for ticket in scoped_tickets
        if bucket_start(int(ticket.opened_time or 0), timeframe_name) == current_bar
    )
    return same_tick_open_burst_count, same_bar_open_burst_count


def update_ticket_path_metrics(
    tickets: list[TickTicket],
    *,
    symbol: str,
    tick: dict[str, Any],
    volume: float,
) -> None:
    bid = float(tick["bid"])
    ask = float(tick["ask"])
    tick_time = int(tick["time"])
    tick_msc = int(tick["time_msc"])
    for ticket in tickets:
        mark = bid if ticket.direction == "BUY" else ask
        pnl = float(tick_pnl_usd(symbol, ticket.direction, ticket.fill_price, mark, volume=volume))
        ticket.max_favorable_excursion_pnl = max(float(ticket.max_favorable_excursion_pnl or 0.0), pnl)
        ticket.peak_pnl_before_exit = max(float(ticket.peak_pnl_before_exit or 0.0), pnl)
        ticket.max_adverse_excursion_pnl = min(float(ticket.max_adverse_excursion_pnl or 0.0), pnl)
        if pnl > 0.0 and not bool(ticket.first_green_seen):
            ticket.first_green_seen = True
            ticket.first_green_time = tick_time
            ticket.first_green_msc = tick_msc
        step_px = float(ticket.base_step_px_at_open or 0.0)
        if ticket.direction == "SELL":
            if ask <= float(ticket.trigger_level):
                ticket.reclaimed_trigger_level_seen = True
            if step_px > 0.0 and ask <= float(ticket.trigger_level) - (0.25 * step_px):
                ticket.retraced_0_25x_step_seen = True
            if step_px > 0.0 and ask <= float(ticket.trigger_level) - (0.5 * step_px):
                ticket.retraced_0_5x_step_seen = True
        else:
            if bid >= float(ticket.trigger_level):
                ticket.reclaimed_trigger_level_seen = True
            if step_px > 0.0 and bid >= float(ticket.trigger_level) + (0.25 * step_px):
                ticket.retraced_0_25x_step_seen = True
            if step_px > 0.0 and bid >= float(ticket.trigger_level) + (0.5 * step_px):
                ticket.retraced_0_5x_step_seen = True


def ticket_event_payload(
    ticket: TickTicket,
    *,
    tick: dict[str, Any],
    realized_pnl: float,
    timeframe_name: str,
) -> dict[str, Any]:
    hold_seconds = max(0, int(tick["time"]) - int(ticket.opened_time or 0))
    time_to_first_green_seconds = None
    if bool(ticket.first_green_seen) and int(ticket.first_green_time or 0) >= int(ticket.opened_time or 0):
        time_to_first_green_seconds = int(ticket.first_green_time) - int(ticket.opened_time or 0)
    same_bar_round_trip = bucket_start(int(ticket.opened_time or 0), timeframe_name) == bucket_start(int(tick["time"]), timeframe_name)
    rearm_to_first_green_seconds = time_to_first_green_seconds if bool(ticket.from_rearm) else None
    rearm_to_fail_seconds = hold_seconds if bool(ticket.from_rearm) and float(realized_pnl) <= 0.0 else None
    return {
        "hold_seconds": hold_seconds,
        "time_to_first_green_seconds": time_to_first_green_seconds,
        "rearm_to_first_green_seconds": rearm_to_first_green_seconds,
        "rearm_to_fail_seconds": rearm_to_fail_seconds,
        "max_favorable_excursion_pnl": round(float(ticket.max_favorable_excursion_pnl or 0.0), 3),
        "max_adverse_excursion_pnl": round(float(ticket.max_adverse_excursion_pnl or 0.0), 3),
        "peak_pnl_before_exit": round(float(ticket.peak_pnl_before_exit or 0.0), 3),
        "first_green_before_fail": bool(realized_pnl <= 0.0 and ticket.first_green_seen),
        "spread_at_entry": round(float(ticket.spread_px_at_open or 0.0), 6),
        "spread_at_exit": round(float(spread_px_from_tick(tick) or 0.0), 6),
        "entry_context": ticket.entry_context,
        "session_bucket": ticket.session_bucket_at_open,
        "regime_at_entry": ticket.regime_at_entry,
        "latest_tick_source_last": ticket.latest_tick_source_last,
        "tick_history_source_last": ticket.tick_history_source_last,
        "base_step_px_at_open": round(float(ticket.base_step_px_at_open or 0.0), 6),
        "anchor_distance_px_at_open": round(float(ticket.anchor_distance_px_at_open or 0.0), 6),
        "side_open_count_at_open": int(ticket.side_open_count_at_open or 0),
        "total_open_count_at_open": int(ticket.total_open_count_at_open or 0),
        "same_tick_open_burst_count_at_open": int(ticket.same_tick_open_burst_count_at_open or 0),
        "same_bar_open_burst_count_at_open": int(ticket.same_bar_open_burst_count_at_open or 0),
        "same_bar_round_trip": bool(same_bar_round_trip),
        "reclaimed_trigger_level_seen": bool(ticket.reclaimed_trigger_level_seen),
        "retraced_0_25x_step_seen": bool(ticket.retraced_0_25x_step_seen),
        "retraced_0_5x_step_seen": bool(ticket.retraced_0_5x_step_seen),
    }


def ticket_has_recovery_signal(ticket: TickTicket) -> bool:
    return bool(
        ticket.first_green_seen
        or ticket.reclaimed_trigger_level_seen
        or ticket.retraced_0_25x_step_seen
    )


def side_recovery_status(
    tickets: list[TickTicket],
    *,
    direction: str,
) -> tuple[int, int, int]:
    side_tickets = [ticket for ticket in tickets if ticket.direction == str(direction or "").upper()]
    if not side_tickets:
        return 0, 0, 0

    recovered_count = sum(1 for ticket in side_tickets if ticket_has_recovery_signal(ticket))
    level_idx_values = [int(ticket.level_idx or 0) for ticket in side_tickets if int(ticket.level_idx or 0) > 0]
    frontier_level_idx = max(level_idx_values, default=0)
    if frontier_level_idx > 0:
        frontier_tickets = [ticket for ticket in side_tickets if int(ticket.level_idx or 0) == frontier_level_idx]
    else:
        latest_open_msc = max(int(ticket.opened_msc or 0) for ticket in side_tickets)
        if latest_open_msc > 0:
            frontier_tickets = [ticket for ticket in side_tickets if int(ticket.opened_msc or 0) == latest_open_msc]
        else:
            latest_open_time = max(int(ticket.opened_time or 0) for ticket in side_tickets)
            frontier_tickets = [ticket for ticket in side_tickets if int(ticket.opened_time or 0) == latest_open_time]

    frontier_recovered_count = sum(1 for ticket in frontier_tickets if ticket_has_recovery_signal(ticket))
    return recovered_count, frontier_recovered_count, frontier_level_idx


def timeframe_seconds(name: str) -> int:
    out = TIMEFRAME_SECONDS.get(str(name or "").upper())
    if out is None:
        raise ValueError(f"Unsupported timeframe: {name}")
    return int(out)


def normalize_raw_close_style(value: str | None) -> str:
    style = str(value or "all_profitable").strip().lower()
    if style not in RAW_CLOSE_STYLES:
        raise ValueError(f"Unsupported raw close style: {value}")
    return style


def select_close_positions(side_len: int, gap: int, close_style: str, closeable_positions: list[int] | None = None) -> list[int]:
    if side_len <= gap:
        return []
    style = normalize_raw_close_style(close_style)
    closeable = set(int(pos) for pos in (closeable_positions or []))
    if style == "outer":
        return [0] if 0 in closeable else []
    if style == "inner":
        target = max(0, int(gap) - 1)
        return [target] if target in closeable else []
    if style == "harvest_inner_hold_frontier":
        return sorted(pos for pos in closeable if pos >= 1)
    if style == "stack_depth_scaled_gap":
        if side_len >= int(gap) + 8:
            hold_frontier = 2
        elif side_len >= int(gap) + 4:
            hold_frontier = 1
        else:
            hold_frontier = 0
        return sorted(pos for pos in closeable if pos >= hold_frontier)
    if style == "range_sweep_trend_reclaim":
        if side_len <= int(gap) + 2:
            return sorted(closeable)
        target = max(0, int(gap) - 1)
        return [target] if target in closeable else []
    return sorted(closeable)


def bucket_start(epoch_seconds: int, timeframe_name: str) -> int:
    step = timeframe_seconds(timeframe_name)
    return int(epoch_seconds // step) * step


def latest_closed_bar(symbol: str, timeframe_name: str) -> dict[str, Any] | None:
    timeframe = TIMEFRAME_MT5.get(str(timeframe_name).upper())
    if timeframe is None:
        return None
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, 1)
    if rates is None or len(rates) == 0:
        return None
    bar = rates[0]
    return {
        "time": int(bar[0]),
        "open": float(bar[1]),
        "high": float(bar[2]),
        "low": float(bar[3]),
        "close": float(bar[4]),
        "tick_volume": int(bar[5]),
    }


def load_recent_bars(symbol: str, timeframe_name: str, count: int) -> list[dict[str, Any]]:
    timeframe = TIMEFRAME_MT5.get(str(timeframe_name).upper())
    if timeframe is None:
        return []
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, int(count))
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def load_ticks_range(symbol: str, start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
    ticks = mt5.copy_ticks_range(symbol, start_utc, end_utc, COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return []
    out = []
    for tick in ticks:
        out.append(
            {
                "time": int(tick["time"]),
                "time_msc": int(tick["time_msc"]),
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                "last": float(tick["last"]),
                "flags": int(tick["flags"]),
                "volume": int(tick["volume"]),
                "volume_real": float(tick["volume_real"]),
            }
        )
    return out


def load_ticks_since(symbol: str, last_tick_msc: int, *, lookback_seconds: int = 120) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    if last_tick_msc > 0:
        start = datetime.fromtimestamp(max(0, int(last_tick_msc // 1000) - 1), tz=timezone.utc)
    else:
        start = now - timedelta(seconds=max(1, int(lookback_seconds)))
    ticks = load_ticks_range(symbol, start, now)
    if last_tick_msc <= 0:
        return ticks
    return [tick for tick in ticks if int(tick["time_msc"]) > int(last_tick_msc)]


def load_ticks_since_with_source(
    symbol: str,
    last_tick_msc: int,
    *,
    lookback_seconds: int = 120,
    shared_price_max_age_ms: int = 0,
) -> tuple[list[dict[str, Any]], str]:
    max_age_ms = max(0, int(shared_price_max_age_ms or 0))
    if max_age_ms > 0:
        cached_ticks = read_cached_ticks_since(
            symbol,
            int(last_tick_msc or 0),
            max_age_ms=max_age_ms,
            lookback_seconds=lookback_seconds,
        )
        if cached_ticks is not None:
            return cached_ticks, "shared_tick_cache"
    return load_ticks_since(symbol, last_tick_msc, lookback_seconds=lookback_seconds), "copy_ticks_range"


def _normalize_tick_payload(tick: Any) -> dict[str, Any]:
    return {
        "time": int(getattr(tick, "time", 0) or 0),
        "time_msc": int(getattr(tick, "time_msc", 0) or 0),
        "bid": float(getattr(tick, "bid", 0.0) or 0.0),
        "ask": float(getattr(tick, "ask", 0.0) or 0.0),
        "last": float(getattr(tick, "last", 0.0) or 0.0),
        "flags": int(getattr(tick, "flags", 0) or 0),
        "volume": int(getattr(tick, "volume", 0) or 0),
        "volume_real": float(getattr(tick, "volume_real", 0.0) or 0.0),
    }


def _cached_price_to_tick(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        ts = datetime.fromisoformat(str(payload.get("ts", "")).replace("Z", "+00:00"))
    except Exception:
        return None
    time_msc = int(ts.timestamp() * 1000)
    return {
        "time": int(time_msc // 1000),
        "time_msc": int(time_msc),
        "bid": float(payload.get("bid", 0.0) or 0.0),
        "ask": float(payload.get("ask", 0.0) or 0.0),
        "last": float(payload.get("last", 0.0) or 0.0),
        "flags": int(payload.get("flags", 0) or 0),
        "volume": int(payload.get("volume", 0) or 0),
        "volume_real": float(payload.get("volume_real", payload.get("volume", 0.0)) or 0.0),
    }


def load_latest_tick(symbol: str, *, shared_price_max_age_ms: int = 0) -> tuple[dict[str, Any] | None, str]:
    max_age_ms = max(0, int(shared_price_max_age_ms or 0))
    if max_age_ms > 0:
        cached_price = read_cached_price(symbol, max_age_ms=max_age_ms)
        if cached_price is not None:
            tick = _cached_price_to_tick(cached_price)
            if tick is not None:
                return tick, "shared_price_cache"
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return None, ""
    return _normalize_tick_payload(tick), "symbol_info_tick"


def tick_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, volume: float = VOLUME) -> float:
    order_type = mt5.ORDER_TYPE_BUY if str(direction or "").upper() == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, volume, float(entry_price), float(exit_price))
    if gross is None:
        return 0.0
    return float(gross)


def check_offensive_escape(
    *,
    open_tickets: list[Any],
    anchor: float,
    step: float,
    max_levels: int,
    current_price: float,
    pip_value: float,
    volume: float,
    escape_profit_threshold_pct: float = 0.001,
    escape_loss_threshold_pct: float = 0.0005,
) -> list[dict[str, Any]]:
    """Tier 0: Offensive extreme closure.

    Close profitable extreme positions when price approaches their level after
    a swing. These positions are unlikely to be revisited if the trend is reversing.

    Logic:
    - Find positions at the EXTREME edges of the lattice (levels > max_levels * 0.7)
    - If price has swung BACK toward the extreme (closing in on the position)
    - AND the position is only marginally profitable or starting to lose
    - → Close it now at ~breakeven or small profit

    This is the user's insight: "close out positions at the extremes that haven't
    been returned to and likely won't be returned to."
    """
    if not open_tickets or anchor <= 0 or step <= 0:
        return []

    escapes: list[dict[str, Any]] = []
    extreme_threshold_level = int(max_levels * 0.7) if max_levels > 0 else 3

    for ticket in open_tickets:
        direction = str(ticket.direction or "").upper()
        fill_price = float(ticket.fill_price)
        level_idx = tick_ticket_level_idx(ticket, anchor, step)

        # Only consider extreme positions
        if level_idx < extreme_threshold_level:
            continue

        # Compute current P/L
        # For offensive escape, we check if price is APPROACHING this position's level
        # after having moved away from it
        if direction == "BUY":
            # BUY position: profitable if current price > fill_price
            pnl_pct = (current_price - fill_price) / fill_price if fill_price > 0 else 0
            # Distance from anchor (how extreme is this?)
            distance_from_anchor = (anchor - fill_price) / step if step > 0 else 0
        else:
            # SELL position: profitable if current price < fill_price
            pnl_pct = (fill_price - current_price) / fill_price if fill_price > 0 else 0
            distance_from_anchor = (fill_price - anchor) / step if step > 0 else 0

        # Escape conditions:
        # 1. Position is at extreme level (already checked above)
        # 2. Profit is small (< threshold) OR starting to lose (< loss threshold)
        # 3. Price is moving AWAY from this position (approaching from the wrong side)
        if direction == "BUY":
            # BUY at extreme: escape if profit is small/losing AND price is below entry
            if pnl_pct < escape_profit_threshold_pct and current_price < fill_price:
                escapes.append({
                    "ticket": ticket,
                    "reason": f"BUY_extreme_{level_idx}_approaching_loss_pnl_{pnl_pct:.4f}",
                })
        else:
            # SELL at extreme: escape if profit is small/losing AND price is above entry
            if pnl_pct < escape_profit_threshold_pct and current_price > fill_price:
                escapes.append({
                    "ticket": ticket,
                    "reason": f"SELL_extreme_{level_idx}_approaching_loss_pnl_{pnl_pct:.4f}",
                })

    return escapes


def group_floating_by_fill_cluster(
    floating: list[tuple[Any, float]],
    fill_tolerance: float = 0.01,
) -> list[list[tuple[Any, float]]]:
    """Group floating positions by fill price cluster.

    When positions share the same fill price (within tolerance), they form a
    single risk unit — they'll ALL recover together or ALL fail together.
    Escaping them individually at the same bad price is wasteful.

    Args:
        floating: List of (ticket, pnl_val) tuples.
        fill_tolerance: Max price difference to consider positions in same cluster.

    Returns:
        List of clusters, where each cluster is a list of (ticket, pnl_val) tuples.
    """
    if not floating:
        return []

    # Sort by fill price for deterministic clustering
    sorted_floating = sorted(floating, key=lambda x: float(x[0].fill_price))

    clusters: list[list[tuple[Any, float]]] = []
    current_cluster: list[tuple[Any, float]] = [sorted_floating[0]]
    current_fill = float(sorted_floating[0][0].fill_price)

    for ticket, pnl_val in sorted_floating[1:]:
        fill = float(ticket.fill_price)
        if abs(fill - current_fill) <= fill_tolerance:
            current_cluster.append((ticket, pnl_val))
        else:
            clusters.append(current_cluster)
            current_cluster = [(ticket, pnl_val)]
            current_fill = fill

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def offensive_budget_cap_usd(
    positive_close_ticket_profit_usd: float,
    offensive_budget_share: float,
) -> float:
    return max(0.0, float(positive_close_ticket_profit_usd or 0.0)) * max(
        0.0,
        float(offensive_budget_share or 0.0),
    )


def offensive_budget_remaining_usd(
    positive_close_ticket_profit_usd: float,
    offensive_spend_usd: float,
    offensive_budget_share: float,
) -> float:
    return max(
        0.0,
        offensive_budget_cap_usd(
            positive_close_ticket_profit_usd,
            offensive_budget_share,
        ) - max(0.0, float(offensive_spend_usd or 0.0)),
    )


def tick_ticket_level_idx(ticket: TickTicket, anchor: float, base_step_px: float) -> int:
    if int(ticket.level_idx or 0) > 0:
        return int(ticket.level_idx)
    if base_step_px <= 0.0:
        return 0
    if str(ticket.direction or "").upper() == "SELL":
        return max(1, int(round((float(ticket.trigger_level) - float(anchor)) / float(base_step_px))))
    return max(1, int(round((float(anchor) - float(ticket.trigger_level)) / float(base_step_px))))


def tick_same_bar_hurdle_applies(
    *,
    ticket: TickTicket,
    tick_time: int,
    timeframe_name: str,
    pnl: float,
    min_pnl: float,
    shallow_level_cap: int,
    anchor: float,
    base_step_px: float,
) -> bool:
    if min_pnl <= 0.0 or shallow_level_cap <= 0:
        return False
    if bucket_start(int(ticket.opened_time or 0), timeframe_name) != bucket_start(int(tick_time or 0), timeframe_name):
        return False
    if tick_ticket_level_idx(ticket, anchor, base_step_px) > int(shallow_level_cap):
        return False
    return float(pnl) < float(min_pnl)


def purge_stale_rearm_tickets(
    engine: Any,
    *,
    now_ts: int | None = None,
    max_rearm_age_seconds: float = 120.0,
) -> list[dict[str, Any]]:
    now_ts = int(now_ts or time.time())
    keep: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for ticket in list(getattr(engine.state, "open_tickets", []) or []):
        if not bool(ticket.get("from_rearm", False)):
            keep.append(ticket)
            continue
        opened_time = int(ticket.get("opened_time", 0) or 0)
        age_seconds = None if opened_time <= 0 else max(0, now_ts - opened_time)
        if age_seconds is None or age_seconds > float(max_rearm_age_seconds):
            removed.append(
                {
                    "direction": str(ticket.get("direction", "") or "").upper(),
                    "entry_price": float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0),
                    "from_rearm": True,
                    "opened_time": opened_time,
                    "age_seconds": age_seconds,
                }
            )
            continue
        keep.append(ticket)
    if removed:
        engine.state.open_tickets = keep
        if not keep and hasattr(engine.state, "lattice_started_time"):
            engine.state.lattice_started_time = 0
    return removed


class TickStatefulRearmEngine:
    def __init__(
        self,
        symbol: str,
        cfg: RawConfig,
        symbol_info,
        *,
        timeframe_name: str,
        variant: RearmVariant,
        close_alpha: float = 0.0,
        momentum_gate: bool = False,
        cooldown_bars: int = 0,
        close_style: str = "all_profitable",
        handoff_steps: float = 0.5,
        sell_gap: int | None = None,
        buy_gap: int | None = None,
        step_sell: float | None = None,
        step_buy: float | None = None,
        volume: float = VOLUME,
        max_floating_loss_usd: float = -10.0,
        max_lattice_window_bars: int = 240,
        breakout_buffer_pips: float = 0.0,
        escape_bars: int = 0,
        escape_threshold_usd: float = 0.0,
        cluster_aware_escape: bool = False,
        cluster_fill_tolerance: float = 0.01,
        guard_open_admission: bool = False,
        offensive_closure_enabled: bool = True,
        offensive_safety_margin_usd: float = 2.0,
        offensive_safety_margin_pct: float = 0.20,
        offensive_cut_cooldown_bars: int = 5,
        offensive_breakeven_band_usd: float = 0.50,
        offensive_budget_share: float = 0.25,
        suppress_additional_levels_after_burst: bool = False,
        burst_open_threshold: int = 2,
        max_entry_spread_ratio: float = 0.0,
        liquidity_gap_spread_multiplier: float = 0.0,
        liquidity_gap_spread_lookback: int = 0,
        liquidity_gap_spread_floor_ratio: float = 0.0,
        liquidity_gap_spread_max_ratio: float = 0.0,
        adaptive_overlay_autopilot: bool = False,
        allow_dynamic_geometry: bool = True,
        proven_step_ceiling: float = 0.0,
        proven_step_buy_ceiling: float = 0.0,
        proven_step_sell_ceiling: float = 0.0,
        min_positive_close_profit_usd: float = 0.0,
        positive_only_closes: bool = False,
        close_at_float_zero: bool = False,
    ) -> None:
        self.symbol = symbol
        self.cfg = cfg
        self.symbol_info = symbol_info
        self.timeframe_name = str(timeframe_name).upper()
        self.variant = variant
        self.close_alpha = max(0.0, min(1.0, float(close_alpha)))
        self.momentum_gate = bool(momentum_gate)
        self.cooldown_bars = max(0, int(cooldown_bars))
        self.close_style = normalize_raw_close_style(close_style)
        self.handoff_steps = float(handoff_steps)
        self.sell_gap = max(0, int(1 if sell_gap is None else sell_gap))
        self.buy_gap = max(0, int(1 if buy_gap is None else buy_gap))
        self.volume = float(volume)
        self.max_floating_loss_usd = float(max_floating_loss_usd)
        self.max_lattice_window_bars = int(max_lattice_window_bars)
        self.breakout_buffer_pips = float(breakout_buffer_pips)
        self.escape_bars = max(0, int(escape_bars))
        self.escape_threshold_usd = float(escape_threshold_usd)
        self.cluster_aware_escape = bool(cluster_aware_escape)
        self.cluster_fill_tolerance = float(cluster_fill_tolerance)
        self.guard_open_admission = bool(guard_open_admission)
        # Offensive extreme closure affordability gate
        self.offensive_closure_enabled = bool(offensive_closure_enabled)
        self.offensive_safety_margin_usd = float(offensive_safety_margin_usd)
        self.offensive_safety_margin_pct = float(offensive_safety_margin_pct)
        self.offensive_cut_cooldown_bars = max(0, int(offensive_cut_cooldown_bars))
        self.offensive_breakeven_band_usd = float(offensive_breakeven_band_usd)
        self.offensive_budget_share = max(0.0, float(offensive_budget_share))
        self.suppress_additional_levels_after_burst = bool(suppress_additional_levels_after_burst)
        self.burst_open_threshold = max(1, int(burst_open_threshold))
        self.max_entry_spread_ratio = max(0.0, float(max_entry_spread_ratio or 0.0))
        self.liquidity_gap_spread_multiplier = max(0.0, float(liquidity_gap_spread_multiplier or 0.0))
        self.liquidity_gap_spread_lookback = max(0, int(liquidity_gap_spread_lookback or 0))
        self.liquidity_gap_spread_floor_ratio = max(0.0, float(liquidity_gap_spread_floor_ratio or 0.0))
        self.liquidity_gap_spread_max_ratio = max(0.0, float(liquidity_gap_spread_max_ratio or 0.0))
        self.adaptive_overlay_autopilot = bool(adaptive_overlay_autopilot)
        self.adaptive_overlay_autopilot_triggered = False
        self.adaptive_overlay_autopilot_triggered_time = 0
        self.adaptive_overlay_autopilot_reason = ""
        self.allow_dynamic_geometry = bool(allow_dynamic_geometry)
        self.proven_step_ceiling = float(proven_step_ceiling) if proven_step_ceiling > 0 else None
        self.proven_step_buy_ceiling = float(proven_step_buy_ceiling) if proven_step_buy_ceiling > 0 else None
        self.proven_step_sell_ceiling = float(proven_step_sell_ceiling) if proven_step_sell_ceiling > 0 else None
        self.min_positive_close_profit_usd = max(0.0, float(min_positive_close_profit_usd or 0.0))
        self.positive_only_closes = bool(positive_only_closes)
        self.close_at_float_zero = bool(close_at_float_zero)
        # Structure-aware shapeshifter tracking
        self._structure_bar_count = 0
        self._box_aware_bar_count = 0
        self._structure_check_interval = 5
        self._structure_hysteresis_bars = 3
        self._current_structure = None
        self._last_guard_open_signature: tuple[str, float, int, str] | None = None
        self._last_spread_block_signature: tuple[str, float, int, str] | None = None
        self.pip_size = float(pip_size_for(symbol_info) or 0.0)
        self.spread_px = float(spread_price(symbol_info) or 0.0)
        self.breakout_buffer_px = self.breakout_buffer_pips * self.pip_size
        if getattr(cfg, "step_is_price_units", False):
            self.base_step_px = float(cfg.step_pips)
            self.base_step_sell_px = (
                float(cfg.step_pips)
                if step_sell is None
                else float(step_sell)
            )
            self.base_step_buy_px = (
                float(cfg.step_pips)
                if step_buy is None
                else float(step_buy)
            )
        else:
            self.base_step_px = float(cfg.step_pips) * self.pip_size
            self.base_step_sell_px = (
                self.base_step_px
                if step_sell is None
                else float(step_sell) * self.pip_size
            )
            self.base_step_buy_px = (
                self.base_step_px
                if step_buy is None
                else float(step_buy) * self.pip_size
            )
        self.adapt_cfg = type(
            "Cfg",
            (),
            {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            },
        )()
        self.state = TickEngineState(
            symbol=symbol,
            timeframe=self.timeframe_name,
            max_floating_loss_usd=self.max_floating_loss_usd,
            max_lattice_window_bars=self.max_lattice_window_bars,
            breakout_buffer_pips=self.breakout_buffer_pips,
            escape_bars=self.escape_bars,
            escape_threshold_usd=self.escape_threshold_usd,
            max_entry_spread_ratio=self.max_entry_spread_ratio,
            liquidity_gap_spread_multiplier=self.liquidity_gap_spread_multiplier,
            liquidity_gap_spread_lookback=self.liquidity_gap_spread_lookback,
            liquidity_gap_spread_floor_ratio=self.liquidity_gap_spread_floor_ratio,
            liquidity_gap_spread_max_ratio=self.liquidity_gap_spread_max_ratio,
            positive_only_closes=self.positive_only_closes,
        )
        self._recent_entry_spread_ratios: deque[float] = deque(
            maxlen=max(4, self.liquidity_gap_spread_lookback or 4)
        )

    def _effective_buy_step_ceiling(self) -> float | None:
        if self.proven_step_buy_ceiling is not None:
            return self.proven_step_buy_ceiling
        return self.proven_step_ceiling

    def _effective_sell_step_ceiling(self) -> float | None:
        if self.proven_step_sell_ceiling is not None:
            return self.proven_step_sell_ceiling
        return self.proven_step_ceiling

    def _clamp_step_to_proven_ceiling(self, *, direction: str, step_px: float) -> float:
        ceiling = self._effective_buy_step_ceiling() if str(direction).upper() == "BUY" else self._effective_sell_step_ceiling()
        if ceiling is None:
            return float(step_px)
        return min(float(step_px), float(ceiling))

    def _activate_positive_only_hold(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        reason: str,
        blocked_pnl: float | None = None,
        blocked_ticket_count: int | None = None,
        emit: bool = True,
    ) -> None:
        if not self.positive_only_closes:
            return
        activated = not bool(self.state.positive_only_hold_active)
        self.state.positive_only_hold_active = True
        self.state.positive_only_hold_reason = str(reason or "positive_only_hold")
        self.state.positive_only_hold_since = max(
            int(self.state.positive_only_hold_since or 0),
            int(tick.get("time", 0) or 0),
        )
        self.state.guard_open_admission = True
        self.guard_open_admission = True
        self.state.suppress_additional_levels_after_burst = True
        self.suppress_additional_levels_after_burst = True
        self.state.adaptive_overlay_autopilot_triggered = True
        self.adaptive_overlay_autopilot_triggered = True
        if not str(self.state.adaptive_overlay_autopilot_reason or "").strip():
            self.state.adaptive_overlay_autopilot_reason = str(reason or "positive_only_hold")
        if not str(self.adaptive_overlay_autopilot_reason or "").strip():
            self.adaptive_overlay_autopilot_reason = str(reason or "positive_only_hold")
        if emit and activated and event_path is not None:
            payload: dict[str, Any] = {"reason": str(reason or "positive_only_hold")}
            if blocked_pnl is not None:
                payload["blocked_pnl"] = round(float(blocked_pnl), 3)
            if blocked_ticket_count is not None:
                payload["blocked_ticket_count"] = int(blocked_ticket_count)
            self._record_event(event_path, "positive_only_hold_activated", tick, **payload)

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self.state)
        payload["base_step_px"] = self.base_step_px
        payload["base_step_sell_px"] = self.base_step_sell_px
        payload["base_step_buy_px"] = self.base_step_buy_px
        payload["open_tickets"] = [serialize_tick_ticket(ticket) for ticket in payload.get("open_tickets") or []]
        payload["rearm_tokens"] = [serialize_rearm_token(token) for token in payload.get("rearm_tokens") or []]
        payload["reconcile_open_max_drift_px"] = max(float(self.spread_px or 0.0) * 2.0, float(self.base_step_px) * 0.25)
        payload["open_realism_mode"] = "tick_native"
        payload["close_realism_mode"] = "tick_native"
        payload["variant"] = self.variant.name
        payload["raw_close_alpha"] = self.close_alpha
        payload["raw_close_style"] = self.close_style
        payload["raw_handoff_steps"] = getattr(self, "handoff_steps", 0.5)
        payload["momentum_gate"] = self.momentum_gate
        payload["max_floating_loss_usd"] = self.max_floating_loss_usd
        payload["max_lattice_window_bars"] = self.max_lattice_window_bars
        payload["breakout_buffer_pips"] = self.breakout_buffer_pips
        payload["offensive_closure_enabled"] = self.offensive_closure_enabled
        payload["escape_bars"] = self.escape_bars
        payload["escape_threshold_usd"] = self.escape_threshold_usd
        payload["cluster_aware_escape"] = self.cluster_aware_escape
        payload["cluster_fill_tolerance"] = self.cluster_fill_tolerance
        payload["guard_open_admission"] = self.guard_open_admission
        payload["offensive_safety_margin_usd"] = self.offensive_safety_margin_usd
        payload["offensive_safety_margin_pct"] = self.offensive_safety_margin_pct
        payload["offensive_cut_cooldown_bars"] = self.offensive_cut_cooldown_bars
        payload["offensive_breakeven_band_usd"] = self.offensive_breakeven_band_usd
        payload["offensive_budget_share"] = self.offensive_budget_share
        payload["suppress_additional_levels_after_burst"] = self.suppress_additional_levels_after_burst
        payload["burst_open_threshold"] = self.burst_open_threshold
        payload["proven_step_ceiling"] = self.proven_step_ceiling
        payload["proven_step_buy_ceiling"] = self.proven_step_buy_ceiling
        payload["proven_step_sell_ceiling"] = self.proven_step_sell_ceiling
        payload["min_positive_close_profit_usd"] = self.min_positive_close_profit_usd
        payload["positive_only_closes"] = bool(self.positive_only_closes)
        payload["close_at_float_zero"] = bool(self.close_at_float_zero)
        payload["max_entry_spread_ratio"] = self.max_entry_spread_ratio
        payload["liquidity_gap_spread_multiplier"] = self.liquidity_gap_spread_multiplier
        payload["liquidity_gap_spread_lookback"] = self.liquidity_gap_spread_lookback
        payload["liquidity_gap_spread_floor_ratio"] = self.liquidity_gap_spread_floor_ratio
        payload["liquidity_gap_spread_max_ratio"] = self.liquidity_gap_spread_max_ratio
        payload["adaptive_overlay_autopilot"] = self.adaptive_overlay_autopilot
        payload["adaptive_overlay_autopilot_triggered"] = self.adaptive_overlay_autopilot_triggered
        payload["adaptive_overlay_autopilot_triggered_time"] = self.adaptive_overlay_autopilot_triggered_time
        payload["adaptive_overlay_autopilot_reason"] = self.adaptive_overlay_autopilot_reason
        return payload

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        configured_max_entry_spread_ratio = float(self.max_entry_spread_ratio or 0.0)
        configured_liquidity_gap_spread_multiplier = float(self.liquidity_gap_spread_multiplier or 0.0)
        configured_liquidity_gap_spread_lookback = int(self.liquidity_gap_spread_lookback or 0)
        configured_liquidity_gap_spread_floor_ratio = float(self.liquidity_gap_spread_floor_ratio or 0.0)
        configured_liquidity_gap_spread_max_ratio = float(self.liquidity_gap_spread_max_ratio or 0.0)
        configured_min_positive_close_profit_usd = float(self.min_positive_close_profit_usd or 0.0)
        configured_positive_only_closes = bool(self.positive_only_closes)
        configured_guard_open_admission = bool(self.guard_open_admission)
        configured_suppress_additional_levels_after_burst = bool(self.suppress_additional_levels_after_burst)
        configured_burst_open_threshold = int(self.burst_open_threshold or 0)
        configured_adaptive_overlay_autopilot = bool(self.adaptive_overlay_autopilot)
        converted = dict(payload or {})
        converted["open_tickets"] = [serialize_tick_ticket(ticket) for ticket in (payload.get("open_tickets") or [])]
        converted["rearm_tokens"] = [serialize_rearm_token(token) for token in (payload.get("rearm_tokens") or [])]
        for key, value in converted.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        if self.allow_dynamic_geometry:
            self.base_step_sell_px = self._clamp_step_to_proven_ceiling(
                direction="SELL",
                step_px=float(converted.get("base_step_sell_px", self.base_step_sell_px)),
            )
            self.base_step_buy_px = self._clamp_step_to_proven_ceiling(
                direction="BUY",
                step_px=float(converted.get("base_step_buy_px", self.base_step_buy_px)),
            )
        # Contract-level restart args must override stale persisted state on raw/live seats,
        # but already-armed runtime safety bits should survive a recycle.
        self.max_entry_spread_ratio = max(0.0, configured_max_entry_spread_ratio)
        self.state.max_entry_spread_ratio = self.max_entry_spread_ratio
        self.liquidity_gap_spread_multiplier = max(0.0, configured_liquidity_gap_spread_multiplier)
        self.state.liquidity_gap_spread_multiplier = self.liquidity_gap_spread_multiplier
        self.liquidity_gap_spread_lookback = max(0, configured_liquidity_gap_spread_lookback)
        self.state.liquidity_gap_spread_lookback = self.liquidity_gap_spread_lookback
        self.liquidity_gap_spread_floor_ratio = max(0.0, configured_liquidity_gap_spread_floor_ratio)
        self.state.liquidity_gap_spread_floor_ratio = self.liquidity_gap_spread_floor_ratio
        self.liquidity_gap_spread_max_ratio = max(0.0, configured_liquidity_gap_spread_max_ratio)
        self.state.liquidity_gap_spread_max_ratio = self.liquidity_gap_spread_max_ratio
        self.min_positive_close_profit_usd = max(0.0, configured_min_positive_close_profit_usd)
        self.state.min_positive_close_profit_usd = self.min_positive_close_profit_usd
        self.positive_only_closes = configured_positive_only_closes
        self.state.positive_only_closes = configured_positive_only_closes
        if not configured_positive_only_closes:
            self.state.positive_only_hold_active = False
            self.state.positive_only_hold_reason = ""
            self.state.positive_only_hold_since = 0
        guard_open_admission = bool(configured_guard_open_admission or self.guard_open_admission or self.state.guard_open_admission)
        self.guard_open_admission = guard_open_admission
        self.state.guard_open_admission = guard_open_admission
        suppress_after_burst = bool(
            configured_suppress_additional_levels_after_burst
            or self.suppress_additional_levels_after_burst
            or self.state.suppress_additional_levels_after_burst
        )
        self.suppress_additional_levels_after_burst = suppress_after_burst
        self.state.suppress_additional_levels_after_burst = suppress_after_burst
        if configured_burst_open_threshold > 0:
            self.burst_open_threshold = configured_burst_open_threshold
            self.state.burst_open_threshold = configured_burst_open_threshold
        adaptive_overlay_autopilot = bool(
            configured_adaptive_overlay_autopilot
            or self.adaptive_overlay_autopilot
            or self.state.adaptive_overlay_autopilot
        )
        self.adaptive_overlay_autopilot = adaptive_overlay_autopilot
        self.state.adaptive_overlay_autopilot = adaptive_overlay_autopilot
        self.adaptive_overlay_autopilot_triggered = bool(
            converted.get("adaptive_overlay_autopilot_triggered", self.adaptive_overlay_autopilot_triggered)
        )
        self.adaptive_overlay_autopilot_triggered_time = int(
            converted.get("adaptive_overlay_autopilot_triggered_time", self.adaptive_overlay_autopilot_triggered_time) or 0
        )
        self.adaptive_overlay_autopilot_reason = str(
            converted.get("adaptive_overlay_autopilot_reason", self.adaptive_overlay_autopilot_reason) or ""
        )

    def prime(self, anchor_price: float, anchor_time: int) -> None:
        anchor = float(anchor_price)
        self.state.anchor = anchor
        self.state.next_sell_level = anchor + float(self.base_step_sell_px)
        self.state.next_buy_level = anchor - float(self.base_step_buy_px)
        self.state.last_bar_time = int(anchor_time)

    def _record_event(self, event_path: Path | None, action: str, tick: dict[str, Any], **extra: Any) -> None:
        if event_path is None:
            return
        payload = dict(extra)
        trigger_level = payload.get("trigger_level")
        if trigger_level is not None and "entry_price" not in payload:
            payload["entry_price"] = trigger_level
        if "exit_fill_price" in payload and "exit_price" not in payload:
            payload["exit_price"] = payload["exit_fill_price"]
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": action,
                "symbol": self.symbol,
                "mode": self.state.mode,
                "time": int(tick["time"]),
                "time_msc": int(tick["time_msc"]),
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                **payload,
            },
        )

    def _tick_mid(self, tick: dict[str, Any]) -> float:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        if bid > 0.0 and ask > 0.0:
            return (bid + ask) / 2.0
        return ask if ask > 0.0 else bid

    def _base_step_px(self, direction: str) -> float:
        if str(direction or "").upper() == "SELL":
            return float(self.base_step_sell_px)
        return float(self.base_step_buy_px)

    def _dynamic_step_px(self, direction: str, open_count: int) -> float:
        return dynamic_step(self._base_step_px(direction), int(open_count), self.adapt_cfg)

    def _generate_anticipatory_rearm_tokens(self, tokens: list[TickRearmToken], tick: dict[str, Any], tick_time: int) -> list[TickRearmToken]:
        """Generate anticipatory SELL rearm tokens above current price during short squeezes."""
        n = int(getattr(self.variant, "anticipatory_tokens", 10) or 10)
        m = int(getattr(self.variant, "anticipatory_steps_above", 1) or 1)
        step = float(getattr(self.variant, "anticipatory_step_size", 50) or 50)
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid if bid > 0 else ask)

        # Low-priced symbols (FX, sub-$10 crypto, etc.) should not carry fixed
        # 50-unit anticipatory ladders; they create impossible armed tokens that
        # can interfere with honest rearm opens on later ticks.
        if mid > 0.0 and step > (mid * 0.5):
            return [t for t in tokens if not (t.anticipatory and t.direction == "SELL")]

        sell_tokens = [t for t in tokens if t.direction == "SELL" and not t.anticipatory]
        anticipatory_tokens = [t for t in tokens if t.anticipatory and t.direction == "SELL"]
        sell_positions = [t for t in [deserialize_tick_ticket(tt) for tt in self.state.open_tickets] if t.direction == "SELL" and not t.from_rearm]

        if len(sell_tokens) > 0 or len(sell_positions) == 0:
            return tokens
        if len(anticipatory_tokens) >= n:
            return tokens

        highest_existing = max(
            [t.level for t in tokens if t.direction == "SELL"] +
            [float(t.fill_price) for t in sell_positions if t.fill_price > 0] +
            [0.0]
        )

        start_level = highest_existing + (m * step)
        new_tokens = []
        for i in range(n - len(anticipatory_tokens)):
            entry_price = start_level + (i * step)
            level_idx = int(round((entry_price - float(self.state.anchor)) / float(self.base_step_sell_px))) if float(self.base_step_sell_px) > 0 else 0
            new_tokens.append(
                TickRearmToken(
                    direction="SELL",
                    level=entry_price,
                    level_idx=level_idx,
                    armed=False,
                    cooldown_until_time=0,
                    anticipatory=True,
                    created_time=tick_time,
                )
            )

        if sell_positions:
            highest_sell = max(float(t.fill_price) for t in sell_positions)
            if mid < highest_sell:
                tokens = [t for t in tokens if not (t.anticipatory and t.direction == "SELL")]
                return tokens + new_tokens

        return tokens + new_tokens

    def _update_token_arming(self, tokens: list[TickRearmToken], tick: dict[str, Any]) -> None:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        tick_time = int(tick["time"])
        excursion_px = float(self.variant.excursion_levels)
        for token in tokens:
            if token.armed:
                continue
            if tick_time < int(token.cooldown_until_time or 0):
                continue
            if token.direction == "SELL":
                if bid <= float(token.level) - (excursion_px * self._base_step_px("SELL")):
                    token.armed = True
                    if int(token.armed_at_time or 0) <= 0:
                        token.armed_at_time = tick_time
            else:
                if ask >= float(token.level) + (excursion_px * self._base_step_px("BUY")):
                    token.armed = True
                    if int(token.armed_at_time or 0) <= 0:
                        token.armed_at_time = tick_time

    def _momentum_gate_allows(self, direction: str, level: float, tick: dict[str, Any]) -> bool:
        if not self.momentum_gate:
            return True
        if str(direction or "").upper() == "SELL":
            return float(tick["bid"]) < float(level)
        return float(tick["ask"]) > float(level)

    def _guard_open_admission_allows(self, tickets: list[TickTicket], direction: str) -> tuple[bool, int]:
        if not self.guard_open_admission:
            return True, 0
        side_tickets = [ticket for ticket in tickets if ticket.direction == str(direction or "").upper()]
        if not side_tickets:
            return True, 0
        recovered_count, frontier_recovered_count, _frontier_level_idx = side_recovery_status(
            tickets,
            direction=direction,
        )
        return frontier_recovered_count > 0, recovered_count

    def _emit_guard_open_event(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        direction: str,
        stage: str,
        trigger_level: float,
        side_open_count: int,
        recovered_count: int,
    ) -> None:
        guard_signature = (
            str(direction or "").upper(),
            round(float(trigger_level), 6),
            int(bucket_start(int(tick["time"]), self.timeframe_name)),
            str(stage or ""),
        )
        if self._last_guard_open_signature == guard_signature:
            return
        self._last_guard_open_signature = guard_signature
        self._record_event(
            event_path,
            "open_guarded_admission",
            tick,
            direction=str(direction or "").upper(),
            stage=str(stage or ""),
            trigger_level=round(float(trigger_level), 6),
            side_open_count=int(side_open_count),
            recovery_signal_count=int(recovered_count),
        )

    def _liquidity_gap_threshold_ratio(self) -> tuple[float | None, float | None]:
        if self.liquidity_gap_spread_multiplier <= 0.0 or self.liquidity_gap_spread_lookback < 4:
            return None, None
        history = list(self._recent_entry_spread_ratios)
        if len(history) < min(self.liquidity_gap_spread_lookback, 4):
            return None, None
        sample = history[-self.liquidity_gap_spread_lookback :]
        baseline_ratio = float(median(sample))
        threshold_ratio = max(
            float(self.liquidity_gap_spread_floor_ratio or 0.0),
            baseline_ratio * float(self.liquidity_gap_spread_multiplier),
        )
        max_ratio = float(self.liquidity_gap_spread_max_ratio or 0.0)
        if max_ratio > 0.0:
            threshold_ratio = min(threshold_ratio, max_ratio)
        return baseline_ratio, threshold_ratio

    def _entry_spread_allows(
        self,
        *,
        tick: dict[str, Any],
        direction: str,
    ) -> tuple[bool, float, float, float, str, float | None, float | None]:
        base_step_px = float(self._base_step_px(direction))
        spread_px, spread_ratio = entry_spread_ratio(tick=tick, base_step_px=base_step_px)
        fixed_threshold = float(self.max_entry_spread_ratio or 0.0)
        baseline_ratio, liquidity_gap_threshold_ratio = self._liquidity_gap_threshold_ratio()
        allows = True
        block_mode = ""
        applied_threshold_ratio: float | None = None
        if fixed_threshold > 0.0 and spread_ratio > fixed_threshold:
            allows = False
            block_mode = "fixed_ratio"
            applied_threshold_ratio = fixed_threshold
        elif liquidity_gap_threshold_ratio is not None and spread_ratio > liquidity_gap_threshold_ratio:
            allows = False
            block_mode = "liquidity_gap"
            applied_threshold_ratio = liquidity_gap_threshold_ratio
        self._recent_entry_spread_ratios.append(float(spread_ratio))
        return allows, spread_px, spread_ratio, base_step_px, block_mode, baseline_ratio, applied_threshold_ratio

    def _emit_spread_block_event(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        direction: str,
        stage: str,
        trigger_level: float,
        spread_px: float,
        spread_ratio: float,
        base_step_px: float,
        spread_block_mode: str = "",
        liquidity_gap_baseline_ratio: float | None = None,
        liquidity_gap_threshold_ratio: float | None = None,
    ) -> None:
        spread_signature = (
            str(direction or "").upper(),
            round(float(trigger_level), 6),
            int(bucket_start(int(tick["time"]), self.timeframe_name)),
            str(stage or ""),
        )
        if self._last_spread_block_signature == spread_signature:
            return
        self._last_spread_block_signature = spread_signature
        self._record_event(
            event_path,
            "open_blocked_wide_spread",
            tick,
            direction=str(direction or "").upper(),
            stage=str(stage or ""),
            trigger_level=round(float(trigger_level), 6),
            spread_px=round(float(spread_px), 6),
            spread_ratio=round(float(spread_ratio), 6),
            base_step_px=round(float(base_step_px), 6),
            max_entry_spread_ratio=round(float(self.max_entry_spread_ratio or 0.0), 6),
            spread_block_mode=str(spread_block_mode or ""),
            liquidity_gap_baseline_ratio=None
            if liquidity_gap_baseline_ratio is None
            else round(float(liquidity_gap_baseline_ratio), 6),
            liquidity_gap_threshold_ratio=None
            if liquidity_gap_threshold_ratio is None
            else round(float(liquidity_gap_threshold_ratio), 6),
            liquidity_gap_spread_multiplier=round(float(self.liquidity_gap_spread_multiplier or 0.0), 6),
        )

    def _ticket_level_idx(self, direction: str, trigger_level: float) -> int:
        base_step_px = self._base_step_px(direction)
        if base_step_px <= 0.0:
            return 0
        if str(direction or "").upper() == "SELL":
            return max(1, int(round((float(trigger_level) - float(self.state.anchor)) / base_step_px)))
        return max(1, int(round((float(self.state.anchor) - float(trigger_level)) / base_step_px)))

    def _open_request(self, direction: str, trigger_level: float, tick: dict[str, Any], *, from_rearm: bool, level_idx: int) -> dict[str, Any]:
        executable_price = float(tick["ask"] if str(direction).upper() == "BUY" else tick["bid"])
        return {
            "kind": "open",
            "symbol": self.symbol,
            "direction": str(direction).upper(),
            "trigger_level": float(trigger_level),
            "fill_price": executable_price,
            "time": int(tick["time"]),
            "time_msc": int(tick["time_msc"]),
            "from_rearm": bool(from_rearm),
            "level_idx": int(level_idx),
        }

    def _close_request(self, ticket: TickTicket, close_threshold: float, tick: dict[str, Any]) -> dict[str, Any]:
        close_price = float(tick["bid"] if ticket.direction == "BUY" else tick["ask"])
        return {
            "kind": "close",
            "symbol": self.symbol,
            "direction": ticket.direction,
            "trigger_level": float(ticket.trigger_level),
            "fill_price": close_price,
            "close_threshold": float(close_threshold),
            "time": int(tick["time"]),
            "time_msc": int(tick["time_msc"]),
            "ticket": asdict(ticket),
        }

    def _execute_action(self, request: dict[str, Any], action_sink: ActionSink | None) -> dict[str, Any]:
        if action_sink is None:
            return {"ok": True, "fill_price": float(request["fill_price"])}
        return action_sink(request)

    def _hold_admission_allows_direction(self, tickets: list[TickTicket], direction: str) -> bool:
        if not (self.positive_only_closes and self.state.positive_only_hold_active and tickets):
            return True
        normalized_direction = str(direction or "").upper()
        live_directions = {
            str(ticket.direction or "").upper()
            for ticket in tickets
            if str(ticket.direction or "").upper() in {"SELL", "BUY"}
        }
        if live_directions == {"SELL"}:
            return normalized_direction == "BUY"
        if live_directions == {"BUY"}:
            return normalized_direction == "SELL"
        return False

    def _burst_suppression_state(
        self,
        tickets: list[TickTicket],
        *,
        direction: str,
        tick_time: int,
        tick_msc: int,
    ) -> tuple[bool, int, int]:
        same_tick_open_burst_count, same_bar_open_burst_count = current_open_burst_counts(
            tickets,
            tick_time=tick_time,
            tick_msc=tick_msc,
            timeframe_name=self.timeframe_name,
            direction=direction,
        )
        suppressed = bool(
            self.suppress_additional_levels_after_burst
            and (
                same_tick_open_burst_count >= int(self.burst_open_threshold)
                or same_bar_open_burst_count >= int(self.burst_open_threshold)
            )
        )
        return suppressed, same_tick_open_burst_count, same_bar_open_burst_count

    def _activate_adaptive_overlays(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        reason: str,
        burst_count: int,
        same_tick_open_burst_count: int,
        same_bar_open_burst_count: int,
        total_open_count: int,
    ) -> bool:
        enabled: list[str] = []
        if not self.guard_open_admission:
            self.guard_open_admission = True
            enabled.append("guard_open_admission")
        if not self.cluster_aware_escape:
            self.cluster_aware_escape = True
            enabled.append("cluster_aware_escape")
        if not self.suppress_additional_levels_after_burst:
            self.suppress_additional_levels_after_burst = True
            enabled.append("suppress_additional_levels_after_burst")
        if not enabled:
            return False
        self.adaptive_overlay_autopilot_triggered = True
        self.adaptive_overlay_autopilot_triggered_time = int(tick.get("time", 0) or 0)
        self.adaptive_overlay_autopilot_reason = str(reason or "")
        self._record_event(
            event_path,
            "adaptive_overlay_autopilot_armed",
            tick,
            reason=str(reason or ""),
            enabled_overlays=enabled,
            burst_open_threshold=int(self.burst_open_threshold),
            burst_count=int(burst_count),
            same_tick_open_burst_count=int(same_tick_open_burst_count),
            same_bar_open_burst_count=int(same_bar_open_burst_count),
            total_open_count=int(total_open_count),
        )
        return True

    def _maybe_activate_adaptive_overlays(
        self,
        tickets: list[TickTicket],
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        same_tick_open_burst_count: int | None = None,
        same_bar_open_burst_count: int | None = None,
    ) -> bool:
        if not self.adaptive_overlay_autopilot or self.adaptive_overlay_autopilot_triggered:
            return False
        observed_same_tick = max(
            int(same_tick_open_burst_count or 0),
            max((int(ticket.same_tick_open_burst_count_at_open or 0) for ticket in tickets), default=0),
        )
        observed_same_bar = max(
            int(same_bar_open_burst_count or 0),
            max((int(ticket.same_bar_open_burst_count_at_open or 0) for ticket in tickets), default=0),
        )
        burst_count = max(observed_same_tick, observed_same_bar)
        if burst_count < int(self.burst_open_threshold):
            return False
        return self._activate_adaptive_overlays(
            event_path=event_path,
            tick=tick,
            reason="burst_concentration_detected",
            burst_count=burst_count,
            same_tick_open_burst_count=observed_same_tick,
            same_bar_open_burst_count=observed_same_bar,
            total_open_count=len(tickets),
        )

    def _classify_first_path_verdict(self, *, ticket: TickTicket, realized_pnl: float) -> str:
        saw_green = bool(ticket.first_green_seen)
        if float(realized_pnl) < 0.0 and not saw_green:
            return "never_green_toxic_continuation"
        if float(realized_pnl) < 0.0 and saw_green:
            return "went_green_failed_monetization"
        if float(realized_pnl) >= 0.0 and saw_green:
            return "green_and_monetized"
        return "closed_without_recorded_green"

    def _register_first_path_close(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        action: str,
        ticket: TickTicket,
        realized_pnl: float,
        remaining_open_count: int,
        emit: bool,
    ) -> None:
        if bool(self.state.first_path_close_seen):
            return
        verdict = self._classify_first_path_verdict(ticket=ticket, realized_pnl=realized_pnl)
        self.state.first_path_close_seen = True
        self.state.first_path_close_time = int(tick.get("time", 0) or 0)
        self.state.first_path_close_action = str(action or "")
        self.state.first_path_close_realized_pnl = round(float(realized_pnl), 3)
        self.state.first_path_verdict = verdict
        if emit:
            self._record_event(
                event_path,
                "first_path_verdict_locked",
                tick,
                verdict=verdict,
                close_action=str(action or ""),
                direction=ticket.direction,
                trigger_level=round(float(ticket.trigger_level), 6),
                realized_pnl=round(float(realized_pnl), 3),
                first_green_seen=bool(ticket.first_green_seen),
                same_tick_open_burst_count=int(ticket.same_tick_open_burst_count_at_open or 0),
                same_bar_open_burst_count=int(ticket.same_bar_open_burst_count_at_open or 0),
                hold_seconds=max(0, int(tick.get("time", 0) or 0) - int(ticket.opened_time or 0)),
                remaining_open_count=max(0, int(remaining_open_count or 0)),
            )
        if verdict not in {"never_green_toxic_continuation", "went_green_failed_monetization"}:
            return
        same_tick_open_burst_count = int(ticket.same_tick_open_burst_count_at_open or 0)
        same_bar_open_burst_count = int(ticket.same_bar_open_burst_count_at_open or 0)
        self._activate_adaptive_overlays(
            event_path=event_path,
            tick=tick,
            reason=f"first_path_{verdict}",
            burst_count=max(same_tick_open_burst_count, same_bar_open_burst_count),
            same_tick_open_burst_count=same_tick_open_burst_count,
            same_bar_open_burst_count=same_bar_open_burst_count,
            total_open_count=max(0, int(remaining_open_count or 0)),
        )

    def process_tick(self, tick: dict[str, Any], *, action_sink: ActionSink | None = None, event_path: Path | None = None, emit: bool = True) -> None:
        if self.state.anchor == 0.0:
            self.prime(self._tick_mid(tick), bucket_start(int(tick["time"]), self.timeframe_name))
        tickets = [deserialize_tick_ticket(t) for t in self.state.open_tickets]
        tokens = [TickRearmToken(**t) for t in self.state.rearm_tokens]
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        tick_time = int(tick["time"])
        tick_msc = int(tick["time_msc"])
        self._update_token_arming(tokens, tick)
        update_ticket_path_metrics(tickets, symbol=self.symbol, tick=tick, volume=self.volume)
        self._maybe_activate_adaptive_overlays(
            tickets,
            event_path=event_path,
            tick=tick,
        )

        open_sell_main = sum(1 for t in tickets if t.direction == "SELL" and not bool(t.from_rearm))
        open_buy_main = sum(1 for t in tickets if t.direction == "BUY" and not bool(t.from_rearm))
        current_sell_step = self._dynamic_step_px("SELL", open_sell_main)
        current_buy_step = self._dynamic_step_px("BUY", open_buy_main)
        hold_active = bool(self.positive_only_closes and self.state.positive_only_hold_active and tickets)
        allow_sell_during_hold = self._hold_admission_allows_direction(tickets, "SELL")
        allow_buy_during_hold = self._hold_admission_allows_direction(tickets, "BUY")

        while allow_sell_during_hold and bid >= float(self.state.next_sell_level) and open_sell_main < int(self.cfg.max_open_per_side):
            guard_allows, recovered_count = self._guard_open_admission_allows(tickets, "SELL")
            if not guard_allows:
                if emit:
                    self._emit_guard_open_event(
                        event_path=event_path,
                        tick=tick,
                        direction="SELL",
                        stage="main",
                        trigger_level=float(self.state.next_sell_level),
                        side_open_count=sum(1 for ticket in tickets if ticket.direction == "SELL"),
                        recovered_count=recovered_count,
                    )
                break
            suppressed, same_tick_open_burst_count, same_bar_open_burst_count = self._burst_suppression_state(
                tickets,
                direction="SELL",
                tick_time=tick_time,
                tick_msc=tick_msc,
            )
            if suppressed:
                if emit:
                    self._record_event(
                        event_path,
                        "open_suppressed_after_burst",
                        tick,
                        direction="SELL",
                        stage="main",
                        burst_open_threshold=int(self.burst_open_threshold),
                        same_tick_open_burst_count=same_tick_open_burst_count,
                        same_bar_open_burst_count=same_bar_open_burst_count,
                    )
                break
            (
                spread_allows,
                spread_px,
                spread_ratio,
                base_step_px,
                spread_block_mode,
                liquidity_gap_baseline_ratio,
                liquidity_gap_threshold_ratio,
            ) = self._entry_spread_allows(
                tick=tick,
                direction="SELL",
            )
            if not spread_allows:
                if emit:
                    self._emit_spread_block_event(
                        event_path=event_path,
                        tick=tick,
                        direction="SELL",
                        stage="main",
                        trigger_level=float(self.state.next_sell_level),
                        spread_px=spread_px,
                        spread_ratio=spread_ratio,
                        base_step_px=base_step_px,
                        spread_block_mode=spread_block_mode,
                        liquidity_gap_baseline_ratio=liquidity_gap_baseline_ratio,
                        liquidity_gap_threshold_ratio=liquidity_gap_threshold_ratio,
                    )
                break
            trigger_level = float(self.state.next_sell_level)
            level_idx = self._ticket_level_idx("SELL", trigger_level)
            request = self._open_request("SELL", trigger_level, tick, from_rearm=False, level_idx=level_idx)
            result = self._execute_action(request, action_sink)
            if not result.get("ok"):
                break
            ticket_obj = TickTicket(
                direction="SELL",
                trigger_level=trigger_level,
                fill_price=float(result.get("fill_price", request["fill_price"])),
                opened_time=tick_time,
                opened_msc=tick_msc,
                level_idx=level_idx,
                from_rearm=False,
                live_ticket=int(result.get("live_ticket", 0) or 0),
                position_comment=str(result.get("position_comment", "") or ""),
            )
            self.state.realized_net_usd += float(result.get("realized_pnl", 0.0) or 0.0)
            tickets.append(ticket_obj)
            open_sell_main += 1
            initialize_ticket_telemetry(
                ticket_obj,
                tick=tick,
                anchor=float(self.state.anchor),
                base_step_px=self._base_step_px("SELL"),
                side_open_count=open_sell_main,
                total_open_count=len(tickets),
                same_tick_open_burst_count=current_open_burst_counts(
                    tickets,
                    tick_time=tick_time,
                    tick_msc=tick_msc,
                    timeframe_name=self.timeframe_name,
                )[0],
                same_bar_open_burst_count=current_open_burst_counts(
                    tickets,
                    tick_time=tick_time,
                    tick_msc=tick_msc,
                    timeframe_name=self.timeframe_name,
                )[1],
            )
            if emit:
                self._record_event(
                    event_path,
                    "open_ticket",
                    tick,
                    direction="SELL",
                    trigger_level=round(trigger_level, 6),
                    fill_price=round(ticket_obj.fill_price, 6),
                    level_idx=level_idx,
                    spread_at_entry=round(ticket_obj.spread_px_at_open, 6),
                    entry_context=ticket_obj.entry_context,
                    session_bucket=ticket_obj.session_bucket_at_open,
                    regime_at_entry=ticket_obj.regime_at_entry,
                    latest_tick_source_last=ticket_obj.latest_tick_source_last,
                    tick_history_source_last=ticket_obj.tick_history_source_last,
                    base_step_px_at_open=round(ticket_obj.base_step_px_at_open, 6),
                    side_open_count_at_open=ticket_obj.side_open_count_at_open,
                    total_open_count_at_open=ticket_obj.total_open_count_at_open,
                    same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                    same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
                    anchor_distance_px_at_open=round(ticket_obj.anchor_distance_px_at_open, 6),
                )
            self._maybe_activate_adaptive_overlays(
                tickets,
                event_path=event_path,
                tick=tick,
                same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
            )
            current_sell_step = self._dynamic_step_px("SELL", open_sell_main)
            self.state.next_sell_level += current_sell_step

        while allow_buy_during_hold and ask <= float(self.state.next_buy_level) and open_buy_main < int(self.cfg.max_open_per_side):
            guard_allows, recovered_count = self._guard_open_admission_allows(tickets, "BUY")
            if not guard_allows:
                if emit:
                    self._emit_guard_open_event(
                        event_path=event_path,
                        tick=tick,
                        direction="BUY",
                        stage="main",
                        trigger_level=float(self.state.next_buy_level),
                        side_open_count=sum(1 for ticket in tickets if ticket.direction == "BUY"),
                        recovered_count=recovered_count,
                    )
                break
            suppressed, same_tick_open_burst_count, same_bar_open_burst_count = self._burst_suppression_state(
                tickets,
                direction="BUY",
                tick_time=tick_time,
                tick_msc=tick_msc,
            )
            if suppressed:
                if emit:
                    self._record_event(
                        event_path,
                        "open_suppressed_after_burst",
                        tick,
                        direction="BUY",
                        stage="main",
                        burst_open_threshold=int(self.burst_open_threshold),
                        same_tick_open_burst_count=same_tick_open_burst_count,
                        same_bar_open_burst_count=same_bar_open_burst_count,
                    )
                break
            (
                spread_allows,
                spread_px,
                spread_ratio,
                base_step_px,
                spread_block_mode,
                liquidity_gap_baseline_ratio,
                liquidity_gap_threshold_ratio,
            ) = self._entry_spread_allows(
                tick=tick,
                direction="BUY",
            )
            if not spread_allows:
                if emit:
                    self._emit_spread_block_event(
                        event_path=event_path,
                        tick=tick,
                        direction="BUY",
                        stage="main",
                        trigger_level=float(self.state.next_buy_level),
                        spread_px=spread_px,
                        spread_ratio=spread_ratio,
                        base_step_px=base_step_px,
                        spread_block_mode=spread_block_mode,
                        liquidity_gap_baseline_ratio=liquidity_gap_baseline_ratio,
                        liquidity_gap_threshold_ratio=liquidity_gap_threshold_ratio,
                    )
                break
            trigger_level = float(self.state.next_buy_level)
            level_idx = self._ticket_level_idx("BUY", trigger_level)
            request = self._open_request("BUY", trigger_level, tick, from_rearm=False, level_idx=level_idx)
            result = self._execute_action(request, action_sink)
            if not result.get("ok"):
                break
            ticket_obj = TickTicket(
                direction="BUY",
                trigger_level=trigger_level,
                fill_price=float(result.get("fill_price", request["fill_price"])),
                opened_time=tick_time,
                opened_msc=tick_msc,
                level_idx=level_idx,
                from_rearm=False,
                live_ticket=int(result.get("live_ticket", 0) or 0),
                position_comment=str(result.get("position_comment", "") or ""),
            )
            self.state.realized_net_usd += float(result.get("realized_pnl", 0.0) or 0.0)
            tickets.append(ticket_obj)
            open_buy_main += 1
            initialize_ticket_telemetry(
                ticket_obj,
                tick=tick,
                anchor=float(self.state.anchor),
                base_step_px=self._base_step_px("BUY"),
                side_open_count=open_buy_main,
                total_open_count=len(tickets),
                same_tick_open_burst_count=current_open_burst_counts(
                    tickets,
                    tick_time=tick_time,
                    tick_msc=tick_msc,
                    timeframe_name=self.timeframe_name,
                )[0],
                same_bar_open_burst_count=current_open_burst_counts(
                    tickets,
                    tick_time=tick_time,
                    tick_msc=tick_msc,
                    timeframe_name=self.timeframe_name,
                )[1],
            )
            if emit:
                self._record_event(
                    event_path,
                    "open_ticket",
                    tick,
                    direction="BUY",
                    trigger_level=round(trigger_level, 6),
                    fill_price=round(ticket_obj.fill_price, 6),
                    level_idx=level_idx,
                    spread_at_entry=round(ticket_obj.spread_px_at_open, 6),
                    entry_context=ticket_obj.entry_context,
                    session_bucket=ticket_obj.session_bucket_at_open,
                    regime_at_entry=ticket_obj.regime_at_entry,
                    latest_tick_source_last=ticket_obj.latest_tick_source_last,
                    tick_history_source_last=ticket_obj.tick_history_source_last,
                    base_step_px_at_open=round(ticket_obj.base_step_px_at_open, 6),
                    side_open_count_at_open=ticket_obj.side_open_count_at_open,
                    total_open_count_at_open=ticket_obj.total_open_count_at_open,
                    same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                    same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
                    anchor_distance_px_at_open=round(ticket_obj.anchor_distance_px_at_open, 6),
                )
            self._maybe_activate_adaptive_overlays(
                tickets,
                event_path=event_path,
                tick=tick,
                same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
            )
            current_buy_step = self._dynamic_step_px("BUY", open_buy_main)
            self.state.next_buy_level -= current_buy_step

        for direction in ("SELL", "BUY"):
            if hold_active and not self._hold_admission_allows_direction(tickets, direction):
                continue
            side_open = sum(1 for t in tickets if t.direction == direction and bool(t.from_rearm))
            for token in list(tokens):
                if token.direction != direction or not token.armed:
                    continue
                guard_allows, recovered_count = self._guard_open_admission_allows(tickets, direction)
                if not guard_allows:
                    if emit:
                        self._emit_guard_open_event(
                            event_path=event_path,
                            tick=tick,
                            direction=direction,
                            stage="rearm",
                            trigger_level=float(token.level),
                            side_open_count=sum(1 for ticket in tickets if ticket.direction == direction),
                            recovered_count=recovered_count,
                        )
                    break
                if side_open >= int(self.cfg.max_open_per_side):
                    break
                suppressed, same_tick_open_burst_count, same_bar_open_burst_count = self._burst_suppression_state(
                    tickets,
                    direction=direction,
                    tick_time=tick_time,
                    tick_msc=tick_msc,
                )
                if suppressed:
                    if emit:
                        self._record_event(
                            event_path,
                            "open_suppressed_after_burst",
                            tick,
                            direction=direction,
                            stage="rearm",
                            burst_open_threshold=int(self.burst_open_threshold),
                            same_tick_open_burst_count=same_tick_open_burst_count,
                            same_bar_open_burst_count=same_bar_open_burst_count,
                            rearm_open=True,
                        )
                    break
                (
                    spread_allows,
                    spread_px,
                    spread_ratio,
                    base_step_px,
                    spread_block_mode,
                    liquidity_gap_baseline_ratio,
                    liquidity_gap_threshold_ratio,
                ) = self._entry_spread_allows(
                    tick=tick,
                    direction=direction,
                )
                if not spread_allows:
                    if emit:
                        self._emit_spread_block_event(
                            event_path=event_path,
                            tick=tick,
                            direction=direction,
                            stage="rearm",
                            trigger_level=float(token.level),
                            spread_px=spread_px,
                            spread_ratio=spread_ratio,
                            base_step_px=base_step_px,
                            spread_block_mode=spread_block_mode,
                            liquidity_gap_baseline_ratio=liquidity_gap_baseline_ratio,
                            liquidity_gap_threshold_ratio=liquidity_gap_threshold_ratio,
                        )
                    break
                if not self._momentum_gate_allows(direction, token.level, tick):
                    continue
                if direction == "SELL" and bid < float(token.level):
                    continue
                if direction == "BUY" and ask > float(token.level):
                    continue
                request = self._open_request(direction, float(token.level), tick, from_rearm=True, level_idx=int(token.level_idx))
                result = self._execute_action(request, action_sink)
                if not result.get("ok"):
                    continue
                ticket_obj = TickTicket(
                    direction=direction,
                    trigger_level=float(token.level),
                    fill_price=float(result.get("fill_price", request["fill_price"])),
                    opened_time=tick_time,
                    opened_msc=tick_msc,
                    level_idx=int(token.level_idx),
                    from_rearm=True,
                    live_ticket=int(result.get("live_ticket", 0) or 0),
                    position_comment=str(result.get("position_comment", "") or ""),
                )
                self.state.realized_net_usd += float(result.get("realized_pnl", 0.0) or 0.0)
                tickets.append(ticket_obj)
                tokens.remove(token)
                side_open += 1
                self.state.rearm_opens += 1
                initialize_ticket_telemetry(
                    ticket_obj,
                    tick=tick,
                    anchor=float(self.state.anchor),
                    base_step_px=self._base_step_px(direction),
                    side_open_count=side_open,
                    total_open_count=len(tickets),
                    same_tick_open_burst_count=current_open_burst_counts(
                        tickets,
                        tick_time=tick_time,
                        tick_msc=tick_msc,
                        timeframe_name=self.timeframe_name,
                    )[0],
                    same_bar_open_burst_count=current_open_burst_counts(
                        tickets,
                        tick_time=tick_time,
                        tick_msc=tick_msc,
                        timeframe_name=self.timeframe_name,
                    )[1],
                )
                if emit:
                    created_time = int(token.created_time or 0)
                    armed_at_time = int(token.armed_at_time or 0)
                    self._record_event(
                        event_path,
                        "open_ticket",
                        tick,
                        direction=direction,
                        trigger_level=round(ticket_obj.trigger_level, 6),
                        fill_price=round(ticket_obj.fill_price, 6),
                        level_idx=int(token.level_idx),
                        rearm_open=True,
                        rearm_variant=self.variant.name,
                        spread_at_entry=round(ticket_obj.spread_px_at_open, 6),
                        entry_context=ticket_obj.entry_context,
                        session_bucket=ticket_obj.session_bucket_at_open,
                        regime_at_entry=ticket_obj.regime_at_entry,
                        latest_tick_source_last=ticket_obj.latest_tick_source_last,
                        tick_history_source_last=ticket_obj.tick_history_source_last,
                        base_step_px_at_open=round(ticket_obj.base_step_px_at_open, 6),
                        side_open_count_at_open=ticket_obj.side_open_count_at_open,
                        total_open_count_at_open=ticket_obj.total_open_count_at_open,
                        same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                        same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
                        anchor_distance_px_at_open=round(ticket_obj.anchor_distance_px_at_open, 6),
                        token_age_at_fire=(tick_time - created_time) if created_time > 0 else None,
                        token_age_at_fire_seconds=(tick_time - created_time) if created_time > 0 else None,
                        armed_duration_seconds=(tick_time - armed_at_time) if armed_at_time > 0 else None,
                    )

        
        if self.close_style == "handoff_then_trail_75" and float(self.base_step_px) > 0.0:
            retain_ratio = 0.75
            activation_px = 1.5 * self.base_step_px
            floor_px = 0.25 * self.base_step_px
            handoff_px = getattr(self, "handoff_steps", 0.5) * self.base_step_px
            
            for ticket in list(tickets):
                close_this = False
                exit_price = None
                
                if getattr(ticket, "best_price", None) is None:
                    ticket.best_price = ticket.fill_price
                
                if ticket.direction == "SELL":
                    if ask < ticket.best_price:
                        ticket.best_price = ask
                    mfe_px = float(ticket.fill_price) - float(ticket.best_price)
                    handoff_threshold = float(self.state.anchor) + handoff_px
                    if ask <= handoff_threshold:
                        close_this = True
                        exit_price = ask
                    else:
                        if mfe_px >= activation_px:
                            retained_px = max(floor_px, mfe_px * retain_ratio)
                            next_stop = float(ticket.fill_price) - retained_px
                            if getattr(ticket, "stop_price", None) is None or next_stop < float(ticket.stop_price):
                                ticket.stop_price = next_stop
                        if getattr(ticket, "stop_price", None) is not None and ask >= float(ticket.stop_price):
                            close_this = True
                            exit_price = ask
                else:
                    if bid > ticket.best_price:
                        ticket.best_price = bid
                    mfe_px = float(ticket.best_price) - float(ticket.fill_price)
                    handoff_threshold = float(self.state.anchor) - handoff_px
                    if bid >= handoff_threshold:
                        close_this = True
                        exit_price = bid
                    else:
                        if mfe_px >= activation_px:
                            retained_px = max(floor_px, mfe_px * retain_ratio)
                            next_stop = float(ticket.fill_price) + retained_px
                            if getattr(ticket, "stop_price", None) is None or next_stop > float(ticket.stop_price):
                                ticket.stop_price = next_stop
                        if getattr(ticket, "stop_price", None) is not None and bid <= float(ticket.stop_price):
                            close_this = True
                            exit_price = bid
                
                if close_this and exit_price is not None:
                    pnl = tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, exit_price, volume=self.volume)
                    if pnl > 0:
                        result = self._execute_action(self._close_request(ticket, exit_price, tick), action_sink)
                        if result.get("ok"):
                            close_fill = float(result.get("fill_price", exit_price))
                            real_pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                            self.state.realized_net_usd += real_pnl
                            self.state.realized_closes += 1
                            tickets.remove(ticket)
                            self._register_first_path_close(
                                event_path=event_path, tick=tick, action="close_ticket", ticket=ticket,
                                realized_pnl=real_pnl, remaining_open_count=len(tickets), emit=emit
                            )
                            if int(ticket.level_idx or 0) >= int(self.variant.min_level_idx):
                                tokens.append(TickRearmToken(direction=ticket.direction, level=float(ticket.trigger_level), level_idx=int(ticket.level_idx), created_time=tick_time))
                            if emit:
                                self._record_event(event_path, "close_ticket", tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(real_pnl, 3), rearm_variant=self.variant.name, **ticket_event_payload(ticket, tick=tick, realized_pnl=real_pnl, timeframe_name=self.timeframe_name))

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.trigger_level, reverse=True) 
        while len(sells) > self.sell_gap and ask <= float(sells[self.sell_gap].trigger_level):
            close_threshold = float(sells[self.sell_gap].trigger_level)
            outer = sells[0]
            projected_pnl = tick_pnl_usd(self.symbol, "SELL", outer.fill_price, ask, volume=self.volume)
            if projected_pnl < self.min_positive_close_profit_usd:
                break
            request = self._close_request(outer, close_threshold, tick)
            result = self._execute_action(request, action_sink)
            if not result.get("ok"):
                break
            close_fill = float(result.get("fill_price", request["fill_price"]))
            pnl = tick_pnl_usd(self.symbol, "SELL", outer.fill_price, close_fill, volume=self.volume)
            self.state.realized_net_usd += pnl
            self.state.realized_closes += 1
            if pnl > 0:
                self.state.offensive_positive_close_ticket_profit_usd += pnl
            tickets.remove(outer)
            self._register_first_path_close(
                event_path=event_path,
                tick=tick,
                action="close_ticket",
                ticket=outer,
                realized_pnl=pnl,
                remaining_open_count=len(tickets),
                emit=emit,
            )
            if int(outer.level_idx or 0) >= int(self.variant.min_level_idx):
                tokens.append(
                    TickRearmToken(
                        direction="SELL",
                        level=float(outer.trigger_level),
                        level_idx=int(outer.level_idx),
                        cooldown_until_time=tick_time + (self.cooldown_bars * timeframe_seconds(self.timeframe_name)),
                        created_time=tick_time,
                    )
                )
            if emit:
                self._record_event(
                    event_path,
                    "close_ticket",
                    tick,
                    direction="SELL",
                    trigger_level=round(outer.trigger_level, 6),
                    entry_fill_price=round(outer.fill_price, 6),
                    exit_fill_price=round(close_fill, 6),
                    realized_pnl=round(pnl, 3),
                    close_alpha=self.close_alpha,
                    **ticket_event_payload(outer, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name),
                )
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.trigger_level, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.trigger_level)
        while len(buys) > self.buy_gap and bid >= float(buys[self.buy_gap].trigger_level):
            close_threshold = float(buys[self.buy_gap].trigger_level)
            outer = buys[0]
            projected_pnl = tick_pnl_usd(self.symbol, "BUY", outer.fill_price, bid, volume=self.volume)
            if projected_pnl < self.min_positive_close_profit_usd:
                break
            request = self._close_request(outer, close_threshold, tick)
            result = self._execute_action(request, action_sink)
            if not result.get("ok"):
                break
            close_fill = float(result.get("fill_price", request["fill_price"]))
            pnl = tick_pnl_usd(self.symbol, "BUY", outer.fill_price, close_fill, volume=self.volume)
            self.state.realized_net_usd += pnl
            self.state.realized_closes += 1
            if pnl > 0:
                self.state.offensive_positive_close_ticket_profit_usd += pnl
            tickets.remove(outer)
            self._register_first_path_close(
                event_path=event_path,
                tick=tick,
                action="close_ticket",
                ticket=outer,
                realized_pnl=pnl,
                remaining_open_count=len(tickets),
                emit=emit,
            )
            if int(outer.level_idx or 0) >= int(self.variant.min_level_idx):
                tokens.append(
                    TickRearmToken(
                        direction="BUY",
                        level=float(outer.trigger_level),
                        level_idx=int(outer.level_idx),
                        cooldown_until_time=tick_time + (self.cooldown_bars * timeframe_seconds(self.timeframe_name)),
                        created_time=tick_time,
                    )
                )
            if emit:
                self._record_event(
                    event_path,
                    "close_ticket",
                    tick,
                    direction="BUY",
                    trigger_level=round(outer.trigger_level, 6),
                    entry_fill_price=round(outer.fill_price, 6),
                    exit_fill_price=round(close_fill, 6),
                    realized_pnl=round(pnl, 3),
                    close_alpha=self.close_alpha,
                    **ticket_event_payload(outer, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name),
                )
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.trigger_level)

        if tickets:
            mid = self._tick_mid(tick)
            floating = [(ticket, tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, bid if ticket.direction == "BUY" else ask, volume=self.volume)) for ticket in tickets]
            worst_pnl = min(pnl for _, pnl in floating)

            # Breakout check
            breakout_up = self.breakout_buffer_px > 0 and mid >= float(self.state.anchor) + self.breakout_buffer_px + (self.base_step_px * 0.5)
            breakout_down = self.breakout_buffer_px > 0 and mid <= float(self.state.anchor) - self.breakout_buffer_px - (self.base_step_px * 0.5)

            # Time-out check
            timed_out = self.max_lattice_window_bars > 0 and self.state.lattice_started_time > 0 and (tick_time - int(self.state.lattice_started_time)) >= (int(self.max_lattice_window_bars) * timeframe_seconds(self.timeframe_name))

            # === ESCAPE HATCH: Tier 0 — Offensive extreme closure (BEFORE defensive tiers) ===
            # Close extreme positions that are approaching breakeven after being in profit
            # These are the FIRST positions to become deep losers when trend reverses
            if self.offensive_closure_enabled:
                try:
                    extreme_escapes = check_offensive_escape(
                        open_tickets=list(tickets),
                        anchor=float(self.state.anchor),
                        step=float(self.base_step_px) if self.base_step_px > 0 else 0.0001,
                        max_levels=self.state.max_open_total,
                        current_price=mid,
                        pip_value=self.pip_size * self.volume * 100000 if self.pip_size > 0 else 0.10,
                        volume=self.volume,
                        escape_profit_threshold_pct=0.001,   # Close if profit < 0.1%
                        escape_loss_threshold_pct=0.0005,    # Cut if loss < 0.05%
                    )
                    for escape_action in extreme_escapes:
                        ticket = escape_action["ticket"]

                        # === AFFORDABILITY GATE (novel): realized profit must subsidize the cut ===
                        # Pre-estimate the PnL before executing to check if we can afford it
                        est_close_price = bid if ticket.direction == "BUY" else ask
                        est_pnl = tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, est_close_price, volume=self.volume)
                        safety_margin_usd = self.offensive_safety_margin_usd
                        safety_margin_pct = self.offensive_safety_margin_pct
                        required_margin = max(safety_margin_usd, abs(self.state.realized_net_usd) * safety_margin_pct)
                        affordable = (est_pnl >= 0) or (self.state.realized_net_usd >= abs(est_pnl) + required_margin)
                        budget_remaining_before = offensive_budget_remaining_usd(
                            self.state.offensive_positive_close_ticket_profit_usd,
                            self.state.offensive_spend_usd,
                            self.offensive_budget_share,
                        )
                        budget_ok = (est_pnl >= 0) or (budget_remaining_before >= abs(est_pnl))
                        if self.positive_only_closes and est_pnl < 0:
                            self._activate_positive_only_hold(
                                event_path=event_path,
                                tick=tick,
                                reason="offensive_negative_cut_blocked",
                                blocked_pnl=est_pnl,
                                blocked_ticket_count=len(tickets),
                                emit=emit,
                            )
                            continue
                        # Cooldown check
                        tf_sec = timeframe_seconds(self.timeframe_name)
                        bars_since_last = int((tick_time - self.state.last_offensive_close_bar_time) / tf_sec) if tf_sec > 0 and self.state.last_offensive_close_bar_time > 0 else self.offensive_cut_cooldown_bars
                        cooldown_ok = bars_since_last >= self.offensive_cut_cooldown_bars
                        if not affordable or not cooldown_ok or not budget_ok:
                            continue  # skip — can't afford, over budget, or in cooldown

                        request = self._close_request(ticket, float(ticket.trigger_level), tick)
                        result = self._execute_action(request, action_sink)
                        if result.get("ok"):
                            close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                            escape_pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                            subsidized = (escape_pnl < 0)
                            realized_before = round(self.state.realized_net_usd, 3)
                            offensive_spend_before = round(self.state.offensive_spend_usd, 3)
                            self.state.realized_net_usd += escape_pnl
                            if subsidized:
                                self.state.offensive_spend_usd += abs(escape_pnl)
                            self.state.realized_closes += 1
                            budget_cap = offensive_budget_cap_usd(
                                self.state.offensive_positive_close_ticket_profit_usd,
                                self.offensive_budget_share,
                            )
                            budget_remaining_after = offensive_budget_remaining_usd(
                                self.state.offensive_positive_close_ticket_profit_usd,
                                self.state.offensive_spend_usd,
                                self.offensive_budget_share,
                            )
                            if emit:
                                self._record_event(event_path, "escape_tier0_offensive", tick,
                                    direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6),
                                    entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6),
                                    realized_pnl=round(escape_pnl, 3), reason=escape_action.get("reason", "extreme_approach"),
                                    subsidized=subsidized, realized_net_before=realized_before,
                                    realized_net_after=round(self.state.realized_net_usd, 3),
                                    estimated_cost=round(abs(est_pnl), 3), safety_margin=round(required_margin, 3),
                                    positive_close_ticket_profit_usd=round(self.state.offensive_positive_close_ticket_profit_usd, 3),
                                    offensive_budget_cap_usd=round(budget_cap, 3),
                                    offensive_spend_before_usd=offensive_spend_before,
                                    offensive_spend_after_usd=round(self.state.offensive_spend_usd, 3),
                                    offensive_budget_remaining_before_usd=round(budget_remaining_before, 3),
                                    offensive_budget_remaining_after_usd=round(budget_remaining_after, 3),
                                    **ticket_event_payload(ticket, tick=tick, realized_pnl=escape_pnl, timeframe_name=self.timeframe_name))
                            self.state.last_offensive_close_bar_time = tick_time  # reset cooldown
                            tickets.remove(ticket)
                            self._register_first_path_close(
                                event_path=event_path,
                                tick=tick,
                                action="escape_tier0_offensive",
                                ticket=ticket,
                                realized_pnl=escape_pnl,
                                remaining_open_count=len(tickets),
                                emit=emit,
                            )
                            floating = [(t, tick_pnl_usd(self.symbol, t.direction, t.fill_price, bid if t.direction == "BUY" else ask, volume=self.volume)) for t in tickets]
                            if floating:
                                worst_pnl = min(p for _, p in floating)
                except Exception:
                    pass  # Tier 0 is optional — don't break the runner if import fails

            # === ESCAPE HATCH: Tier 1 — Time-based breakeven escape ===
            if self.escape_bars > 0 and self.state.lattice_started_time > 0:
                tf_sec = timeframe_seconds(self.timeframe_name)
                for ticket, pnl_val in list(floating):
                    age_bars = (tick_time - int(ticket.opened_time)) / tf_sec if tf_sec > 0 else 0
                    if age_bars >= self.escape_bars and pnl_val < 0:
                        if self.positive_only_closes:
                            self._activate_positive_only_hold(
                                event_path=event_path,
                                tick=tick,
                                reason="breakeven_escape_blocked_negative",
                                blocked_pnl=pnl_val,
                                blocked_ticket_count=len(tickets),
                                emit=emit,
                            )
                            continue
                        request = self._close_request(ticket, float(ticket.trigger_level), tick)
                        result = self._execute_action(request, action_sink)
                        if result.get("ok"):
                            close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                            escape_pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                            self.state.realized_net_usd += escape_pnl
                            self.state.realized_closes += 1
                            if emit:
                                self._record_event(event_path, "escape_tier1_breakeven", tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(escape_pnl, 3), age_bars=round(age_bars, 1), **ticket_event_payload(ticket, tick=tick, realized_pnl=escape_pnl, timeframe_name=self.timeframe_name))
                            tickets.remove(ticket)
                            self._register_first_path_close(
                                event_path=event_path,
                                tick=tick,
                                action="escape_tier1_breakeven",
                                ticket=ticket,
                                realized_pnl=escape_pnl,
                                remaining_open_count=len(tickets),
                                emit=emit,
                            )
                            floating = [(t, tick_pnl_usd(self.symbol, t.direction, t.fill_price, bid if t.direction == "BUY" else ask, volume=self.volume)) for t in tickets]
                            if floating:
                                worst_pnl = min(p for _, p in floating)

            # === ESCAPE HATCH: Tier 2 — Surgical cut on deep loss ===
            if self.escape_threshold_usd > 0 and floating:
                if getattr(self, 'cluster_aware_escape', False):
                    # Cluster-aware escape: group same-fill positions and apply threshold to cluster total
                    # When positions share the same fill price, they recover together — escaping individually
                    # realizes losses at the same bad price. Only escape when cluster TOTAL exceeds threshold.
                    clusters = group_floating_by_fill_cluster(
                        floating, fill_tolerance=getattr(self, 'cluster_fill_tolerance', 0.01)
                    )
                    escaped_any = True
                    while escaped_any and floating:
                        escaped_any = False
                        clusters = group_floating_by_fill_cluster(
                            floating, fill_tolerance=getattr(self, 'cluster_fill_tolerance', 0.01)
                        )
                        for cluster in clusters:
                            cluster_total_pnl = sum(pnl for _, pnl in cluster)
                            cluster_size = len(cluster)
                            # Scale threshold by sqrt(cluster_size): larger clusters get more breathing room
                            # because they represent a coordinated bet on mean-reversion at that price level
                            scaled_threshold = float(self.escape_threshold_usd) * (cluster_size ** 0.5)
                            if cluster_total_pnl <= -scaled_threshold:
                                if self.positive_only_closes:
                                    self._activate_positive_only_hold(
                                        event_path=event_path,
                                        tick=tick,
                                        reason="cluster_escape_blocked_negative",
                                        blocked_pnl=cluster_total_pnl,
                                        blocked_ticket_count=cluster_size,
                                        emit=emit,
                                    )
                                    break
                                # Escape entire cluster — they all share the same fate
                                for ticket, pnl_val in list(cluster):
                                    request = self._close_request(ticket, float(ticket.trigger_level), tick)
                                    result = self._execute_action(request, action_sink)
                                    if result.get("ok"):
                                        close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                                        escape_pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                                        self.state.realized_net_usd += escape_pnl
                                        self.state.realized_closes += 1
                                        if emit:
                                            self._record_event(event_path, "escape_tier2_surgical_cluster", tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(escape_pnl, 3), cluster_size=cluster_size, cluster_total_pnl=round(cluster_total_pnl, 3), scaled_threshold=round(scaled_threshold, 3), **ticket_event_payload(ticket, tick=tick, realized_pnl=escape_pnl, timeframe_name=self.timeframe_name))
                                        tickets.remove(ticket)
                                        self._register_first_path_close(
                                            event_path=event_path,
                                            tick=tick,
                                            action="escape_tier2_surgical_cluster",
                                            ticket=ticket,
                                            realized_pnl=escape_pnl,
                                            remaining_open_count=len(tickets),
                                            emit=emit,
                                        )
                                        escaped_any = True
                                floating = [(t, tick_pnl_usd(self.symbol, t.direction, t.fill_price, bid if t.direction == "BUY" else ask, volume=self.volume)) for t in tickets]
                                if floating:
                                    worst_pnl = min(p for _, p in floating)
                                break  # Re-clusters after escape
                else:
                    # Legacy per-position escape (unchanged behavior)
                    for ticket, pnl_val in list(floating):
                        if pnl_val <= -self.escape_threshold_usd:
                            if self.positive_only_closes:
                                self._activate_positive_only_hold(
                                    event_path=event_path,
                                    tick=tick,
                                    reason="surgical_escape_blocked_negative",
                                    blocked_pnl=pnl_val,
                                    blocked_ticket_count=len(tickets),
                                    emit=emit,
                                )
                                continue
                            request = self._close_request(ticket, float(ticket.trigger_level), tick)
                            result = self._execute_action(request, action_sink)
                            if result.get("ok"):
                                close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                                escape_pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                                self.state.realized_net_usd += escape_pnl
                                self.state.realized_closes += 1
                                if emit:
                                    self._record_event(event_path, "escape_tier2_surgical", tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(escape_pnl, 3), **ticket_event_payload(ticket, tick=tick, realized_pnl=escape_pnl, timeframe_name=self.timeframe_name))
                                tickets.remove(ticket)
                                self._register_first_path_close(
                                    event_path=event_path,
                                    tick=tick,
                                    action="escape_tier2_surgical",
                                    ticket=ticket,
                                    realized_pnl=escape_pnl,
                                    remaining_open_count=len(tickets),
                                    emit=emit,
                                )
                                floating = [(t, tick_pnl_usd(self.symbol, t.direction, t.fill_price, bid if t.direction == "BUY" else ask, volume=self.volume)) for t in tickets]
                                if floating:
                                    worst_pnl = min(p for _, p in floating)

            # === ESCAPE HATCH: Tier 3 — Full kill (existing) ===
            if worst_pnl <= float(self.max_floating_loss_usd) or breakout_up or breakout_down or timed_out:
                if self.positive_only_closes and worst_pnl < 0:
                    self._activate_positive_only_hold(
                        event_path=event_path,
                        tick=tick,
                        reason="forced_unwind_blocked_negative",
                        blocked_pnl=worst_pnl,
                        blocked_ticket_count=len(tickets),
                        emit=emit,
                    )
                    tokens = []
                else:
                    for ticket, _pnl in list(floating):
                        request = self._close_request(ticket, float(ticket.trigger_level), tick)
                        result = self._execute_action(request, action_sink)
                        if not result.get("ok"):
                            continue
                        close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                        pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                        self.state.realized_net_usd += pnl
                        self.state.realized_closes += 1
                        if emit:
                            reason = "forced_unwind" if worst_pnl <= float(self.max_floating_loss_usd) else ("breakout_kill" if breakout_up or breakout_down else "timed_kill")
                            self._record_event(event_path, reason, tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(pnl, 3), **ticket_event_payload(ticket, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name))
                        tickets.remove(ticket)
                        self._register_first_path_close(
                            event_path=event_path,
                            tick=tick,
                            action="forced_unwind" if worst_pnl <= float(self.max_floating_loss_usd) else ("breakout_kill" if breakout_up or breakout_down else "timed_kill"),
                            ticket=ticket,
                            realized_pnl=pnl,
                            remaining_open_count=len(tickets),
                            emit=emit,
                        )
                    tokens = []
                    self.state.lattice_started_time = 0
                    self.state.anchor_resets += 1
                    self.state.anchor_resets_risk += 1

        if not tickets:
            self.state.lattice_started_time = 0
            self.state.offensive_positive_close_ticket_profit_usd = 0.0
            self.state.offensive_spend_usd = 0.0
            self.state.last_offensive_close_bar_time = 0
            self.state.positive_only_hold_active = False
            self.state.positive_only_hold_reason = ""
            self.state.positive_only_hold_since = 0
            mark = self._tick_mid(tick)
            if mark >= float(self.state.anchor) + float(self.base_step_sell_px) or mark <= float(self.state.anchor) - float(self.base_step_buy_px):
                self.state.anchor = mark
                self.state.next_sell_level = mark + float(self.base_step_sell_px)
                self.state.next_buy_level = mark - float(self.base_step_buy_px)
                self.state.anchor_resets += 1
                self.state.anchor_resets_flat += 1

        # Generate anticipatory rearm tokens during short squeezes
        tokens = self._generate_anticipatory_rearm_tokens(tokens, tick, tick_time)

        # Structure-aware shapeshifter: check every N bars and adapt geometry
        self._structure_bar_count += 1
        if self.allow_dynamic_geometry and self._structure_bar_count >= self._structure_check_interval and hasattr(self, 'history') and self.history:
            self._structure_bar_count = 0
            try:
                from structure_shapeshifter_bridge import check_and_adapt
                result = check_and_adapt(
                    engine=self,
                    bars=self.history[-60:],
                    current_bar=getattr(self, '_current_bar', None),
                    check_interval_bars=self._structure_check_interval,
                    hysteresis_bars=self._structure_hysteresis_bars,
                )
                if result.get("changed") and event_path:
                    self._record_event(event_path, "structure_flip", tick,
                                       from_structure=result.get("from_structure"),
                                       to_structure=result.get("to_structure"),
                                       step_buy=result.get("to_step_buy"),
                                       step_sell=result.get("to_step_sell"),
                                       alpha=result.get("to_alpha"),
                                       mode=result.get("mode"),
                                       reason=result.get("reason"))
            except Exception:
                pass  # Don't break the runner if structure check fails

        # === BOX-AWARE GEOMETRY ADJUSTMENT (runs every N bars) ===
        self._box_aware_bar_count += 1
        if self.allow_dynamic_geometry and self._box_aware_bar_count >= self._structure_check_interval and hasattr(self, 'symbol') and event_path:
            self._box_aware_bar_count = 0
            try:
                from box_aware_geometry import compute_geometry_for_symbol
                configured_step = float(getattr(self, 'base_step_px', 0.0) or 0.0)
                geom = compute_geometry_for_symbol(self.symbol, configured_step=configured_step)
                if geom and event_path:
                    old_buy = getattr(self, 'base_step_buy_px', 0)
                    old_sell = getattr(self, 'base_step_sell_px', 0)
                    # Apply proven-step ceiling constraint: never widen beyond validated optimum.
                    effective_buy_ceiling = self._effective_buy_step_ceiling()
                    effective_sell_ceiling = self._effective_sell_step_ceiling()
                    new_step_buy = geom.step_buy
                    new_step_sell = geom.step_sell
                    new_step_buy = self._clamp_step_to_proven_ceiling(direction="BUY", step_px=new_step_buy)
                    new_step_sell = self._clamp_step_to_proven_ceiling(direction="SELL", step_px=new_step_sell)
                    # Apply box-aware geometry (clamped to proven ceiling[s])
                    if hasattr(self, 'base_step_buy_px'):
                        self.base_step_buy_px = new_step_buy
                    if hasattr(self, 'base_step_sell_px'):
                        self.base_step_sell_px = new_step_sell
                    # Log the adjustment (show both raw and clamped values)
                    if old_buy > 0 and (abs(old_buy - new_step_buy) / old_buy > 0.05 or abs(old_sell - new_step_sell) / old_sell > 0.05):
                        was_clamped = (
                            (effective_buy_ceiling is not None and geom.step_buy > effective_buy_ceiling)
                            or (effective_sell_ceiling is not None and geom.step_sell > effective_sell_ceiling)
                        )
                        self._record_event(event_path, "box_geometry_adjust", tick,
                                           symbol=self.symbol,
                                           box_position=geom.box_position,
                                           box_bottom=geom.box_bottom,
                                           box_top=geom.box_top,
                                           pattern=geom.pattern,
                                           step_buy_from=round(old_buy, 6),
                                           step_buy_to=round(new_step_buy, 6),
                                           step_sell_from=round(old_sell, 6),
                                           step_sell_to=round(new_step_sell, 6),
                                           asymmetry=geom.asymmetry_ratio,
                                           reason=geom.adjustment_reason,
                                           proven_ceiling=self.proven_step_ceiling,
                                           proven_buy_ceiling=effective_buy_ceiling,
                                           proven_sell_ceiling=effective_sell_ceiling,
                                           ceiling_clamped=was_clamped,
                                           raw_step_buy=round(geom.step_buy, 6),
                                           raw_step_sell=round(geom.step_sell, 6))
            except Exception:
                pass  # Don't break the runner if box check fails

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.rearm_tokens = [asdict(t) for t in tokens]
        self.state.last_tick_time = tick_time
        self.state.last_tick_msc = tick_msc
        self.state.last_bar_time = bucket_start(tick_time, self.timeframe_name)
        self.state.max_open_total = max(int(self.state.max_open_total or 0), len(tickets))

    def process_ticks(self, ticks: list[dict[str, Any]], *, action_sink: ActionSink | None = None, event_path: Path | None = None, emit: bool = True) -> int:
        count = 0
        for tick in sorted(ticks, key=lambda item: (int(item["time_msc"]), int(item["time"]))):
            if int(tick["time_msc"]) <= int(self.state.last_tick_msc or 0):
                continue
            self.process_tick(tick, action_sink=action_sink, event_path=event_path, emit=emit)
            count += 1
        return count


class TickBoundedRearmEngine:
    def __init__(
        self,
        symbol: str,
        cfg: BoundedConfig,
        symbol_info,
        *,
        timeframe_name: str,
        variant: RearmVariant,
        close_gap: int = 1,
        close_style: str = "all_profitable",
        same_bar_min_pnl: float = 0.0,
        same_bar_shallow_level_cap: int = 0,
        cluster_aware_escape: bool = False,
        cluster_fill_tolerance: float = 0.01,
        guard_open_admission: bool = False,
        suppress_additional_levels_after_burst: bool = False,
        burst_open_threshold: int = 2,
        max_entry_spread_ratio: float = 0.0,
        adaptive_overlay_autopilot: bool = False,
        min_positive_close_profit_usd: float = 0.0,
        positive_only_closes: bool = False,
        close_at_float_zero: bool = False,
        volume: float = VOLUME,
    ) -> None:
        self.symbol = symbol
        self.cfg = cfg
        self.symbol_info = symbol_info
        self.timeframe_name = str(timeframe_name).upper()
        self.variant = variant
        self.close_gap = max(1, int(close_gap))
        self.close_style = normalize_raw_close_style(close_style)
        self.same_bar_min_pnl = max(0.0, float(same_bar_min_pnl))
        self.same_bar_shallow_level_cap = max(0, int(same_bar_shallow_level_cap))
        self.min_positive_close_profit_usd = max(0.0, float(min_positive_close_profit_usd or 0.0))
        self.positive_only_closes = bool(positive_only_closes)
        self.close_at_float_zero = bool(close_at_float_zero)
        self.volume = float(volume)
        self.pip_size = float(pip_size_for(symbol_info) or 0.0)
        self.spread_px = float(spread_price(symbol_info) or 0.0)
        self.base_step_px = float(cfg.step_pips) * self.pip_size
        self.breakout_buffer_px = float(cfg.breakout_buffer_pips) * self.pip_size
        self.state = TickEngineState(
            symbol=symbol, 
            timeframe=self.timeframe_name, 
            mode="tick_bounded_rearm",
            max_floating_loss_usd=float(cfg.max_floating_loss_usd),
            max_lattice_window_bars=int(cfg.max_lattice_window_bars),
            breakout_buffer_pips=float(cfg.breakout_buffer_pips),
            cluster_aware_escape=bool(cluster_aware_escape),
            cluster_fill_tolerance=float(cluster_fill_tolerance),
            guard_open_admission=bool(guard_open_admission),
            suppress_additional_levels_after_burst=bool(suppress_additional_levels_after_burst),
            burst_open_threshold=max(1, int(burst_open_threshold)),
            max_entry_spread_ratio=max(0.0, float(max_entry_spread_ratio or 0.0)),
            adaptive_overlay_autopilot=bool(adaptive_overlay_autopilot),
            positive_only_closes=self.positive_only_closes,
        )
        self.history: list[dict[str, Any]] = []
        self._current_bar: dict[str, Any] | None = None
        self._last_guard_open_signature: tuple[str, float, int, str] | None = None
        self._last_spread_block_signature: tuple[str, float, int, str] | None = None

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self.state)
        payload["open_tickets"] = [serialize_tick_ticket(ticket) for ticket in payload.get("open_tickets") or []]
        payload["rearm_tokens"] = [serialize_rearm_token(token) for token in payload.get("rearm_tokens") or []]
        payload["base_step_px"] = self.base_step_px
        payload["breakout_buffer_px"] = self.breakout_buffer_px
        payload["reconcile_open_max_drift_px"] = max(float(self.spread_px or 0.0) * 2.0, float(self.base_step_px) * 0.25)
        payload["close_gap"] = self.close_gap
        payload["same_bar_min_pnl"] = self.same_bar_min_pnl
        payload["same_bar_shallow_level_cap"] = self.same_bar_shallow_level_cap
        payload["min_positive_close_profit_usd"] = self.min_positive_close_profit_usd
        payload["positive_only_closes"] = bool(self.positive_only_closes)
        payload["open_realism_mode"] = "tick_native"
        payload["close_realism_mode"] = "tick_native"
        payload["variant"] = self.variant.name
        payload["close_at_float_zero"] = bool(self.close_at_float_zero)
        return payload

    def _activate_positive_only_hold(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        reason: str,
        blocked_pnl: float | None = None,
        blocked_ticket_count: int | None = None,
        emit: bool = True,
    ) -> None:
        if not self.positive_only_closes:
            return
        activated = not bool(self.state.positive_only_hold_active)
        self.state.positive_only_hold_active = True
        self.state.positive_only_hold_reason = str(reason or "positive_only_hold")
        self.state.positive_only_hold_since = max(
            int(self.state.positive_only_hold_since or 0),
            int(tick.get("time", 0) or 0),
        )
        self.state.guard_open_admission = True
        self.state.suppress_additional_levels_after_burst = True
        self.state.adaptive_overlay_autopilot_triggered = True
        if not str(self.state.adaptive_overlay_autopilot_reason or "").strip():
            self.state.adaptive_overlay_autopilot_reason = str(reason or "positive_only_hold")
        if emit and activated and event_path is not None:
            payload: dict[str, Any] = {"reason": str(reason or "positive_only_hold")}
            if blocked_pnl is not None:
                payload["blocked_pnl"] = round(float(blocked_pnl), 3)
            if blocked_ticket_count is not None:
                payload["blocked_ticket_count"] = int(blocked_ticket_count)
            self._record_event(event_path, "positive_only_hold_activated", tick, **payload)

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        configured_max_entry_spread_ratio = float(self.state.max_entry_spread_ratio or 0.0)
        configured_positive_only_closes = bool(self.positive_only_closes)
        converted = dict(payload or {})
        converted["open_tickets"] = [serialize_tick_ticket(ticket) for ticket in (payload.get("open_tickets") or [])]
        converted["rearm_tokens"] = [serialize_rearm_token(token) for token in (payload.get("rearm_tokens") or [])]
        for key, value in converted.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        # Contract-level restart args must override stale persisted state on bounded seats.
        self.state.max_entry_spread_ratio = max(0.0, configured_max_entry_spread_ratio)
        self.state.positive_only_closes = configured_positive_only_closes
        if not configured_positive_only_closes:
            self.state.positive_only_hold_active = False
            self.state.positive_only_hold_reason = ""
            self.state.positive_only_hold_since = 0

    def hydrate_history(self, bars: list[dict[str, Any]]) -> None:
        self.history = list(bars)[-600:]

    def _record_event(self, event_path: Path | None, action: str, tick: dict[str, Any], **extra: Any) -> None:
        if event_path is None:
            return
        payload = dict(extra)
        trigger_level = payload.get("trigger_level")
        if trigger_level is not None and "entry_price" not in payload:
            payload["entry_price"] = trigger_level
        if "exit_fill_price" in payload and "exit_price" not in payload:
            payload["exit_price"] = payload["exit_fill_price"]
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": action,
                "symbol": self.symbol,
                "mode": self.state.mode,
                "time": int(tick["time"]),
                "time_msc": int(tick["time_msc"]),
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                **payload,
            },
        )

    def _tick_mid(self, tick: dict[str, Any]) -> float:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        if bid > 0.0 and ask > 0.0:
            return (bid + ask) / 2.0
        return ask if ask > 0.0 else bid

    def _update_bar(self, tick: dict[str, Any]) -> None:
        bucket = bucket_start(int(tick["time"]), self.timeframe_name)
        price = self._tick_mid(tick)
        if self._current_bar is None:
            self._current_bar = {"time": bucket, "open": price, "high": price, "low": price, "close": price, "tick_volume": 1}
            return
        if int(self._current_bar["time"]) != bucket:
            self.history.append(dict(self._current_bar))
            self.history = self.history[-600:]
            self._current_bar = {"time": bucket, "open": price, "high": price, "low": price, "close": price, "tick_volume": 1}
            self.state.last_bar_time = bucket - timeframe_seconds(self.timeframe_name)
            return
        self._current_bar["high"] = max(float(self._current_bar["high"]), price)
        self._current_bar["low"] = min(float(self._current_bar["low"]), price)
        self._current_bar["close"] = price
        self._current_bar["tick_volume"] = int(self._current_bar["tick_volume"]) + 1

    def _ticket_level_idx(self, direction: str, trigger_level: float) -> int:
        if self.base_step_px <= 0.0:
            return 0
        if str(direction or "").upper() == "SELL":
            return max(1, int(round((float(trigger_level) - float(self.state.anchor)) / self.base_step_px)))
        return max(1, int(round((float(self.state.anchor) - float(trigger_level)) / self.base_step_px)))

    def _guard_open_admission_allows(self, tickets: list[TickTicket], direction: str) -> tuple[bool, int]:
        if not bool(self.state.guard_open_admission):
            return True, 0
        side_tickets = [ticket for ticket in tickets if ticket.direction == str(direction or "").upper()]
        if not side_tickets:
            return True, 0
        recovered_count, frontier_recovered_count, _frontier_level_idx = side_recovery_status(
            tickets,
            direction=direction,
        )
        return frontier_recovered_count > 0, recovered_count

    def _emit_guard_open_event(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        direction: str,
        stage: str,
        trigger_level: float,
        side_open_count: int,
        recovered_count: int,
    ) -> None:
        guard_signature = (
            str(direction or "").upper(),
            round(float(trigger_level), 6),
            int(bucket_start(int(tick["time"]), self.timeframe_name)),
            str(stage or ""),
        )
        if self._last_guard_open_signature == guard_signature:
            return
        self._last_guard_open_signature = guard_signature
        self._record_event(
            event_path,
            "open_guarded_admission",
            tick,
            direction=str(direction or "").upper(),
            stage=str(stage or ""),
            trigger_level=round(float(trigger_level), 6),
            side_open_count=int(side_open_count),
            recovery_signal_count=int(recovered_count),
        )

    def _entry_spread_allows(self, *, tick: dict[str, Any]) -> tuple[bool, float, float]:
        spread_px, spread_ratio = entry_spread_ratio(tick=tick, base_step_px=self.base_step_px)
        threshold = float(self.state.max_entry_spread_ratio or 0.0)
        if threshold <= 0.0:
            return True, spread_px, spread_ratio
        return spread_ratio <= threshold, spread_px, spread_ratio

    def _emit_spread_block_event(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        direction: str,
        stage: str,
        trigger_level: float,
        spread_px: float,
        spread_ratio: float,
    ) -> None:
        spread_signature = (
            str(direction or "").upper(),
            round(float(trigger_level), 6),
            int(bucket_start(int(tick["time"]), self.timeframe_name)),
            str(stage or ""),
        )
        if self._last_spread_block_signature == spread_signature:
            return
        self._last_spread_block_signature = spread_signature
        self._record_event(
            event_path,
            "open_blocked_wide_spread",
            tick,
            direction=str(direction or "").upper(),
            stage=str(stage or ""),
            trigger_level=round(float(trigger_level), 6),
            spread_px=round(float(spread_px), 6),
            spread_ratio=round(float(spread_ratio), 6),
            base_step_px=round(float(self.base_step_px), 6),
            max_entry_spread_ratio=round(float(self.state.max_entry_spread_ratio or 0.0), 6),
        )

    def _burst_suppression_state(
        self,
        tickets: list[TickTicket],
        *,
        direction: str,
        tick_time: int,
        tick_msc: int,
    ) -> tuple[bool, int, int]:
        same_tick_open_burst_count, same_bar_open_burst_count = current_open_burst_counts(
            tickets,
            tick_time=tick_time,
            tick_msc=tick_msc,
            timeframe_name=self.timeframe_name,
            direction=direction,
        )
        suppressed = bool(
            self.state.suppress_additional_levels_after_burst
            and (
                same_tick_open_burst_count >= int(self.state.burst_open_threshold or 0)
                or same_bar_open_burst_count >= int(self.state.burst_open_threshold or 0)
            )
        )
        return suppressed, same_tick_open_burst_count, same_bar_open_burst_count

    def _activate_adaptive_overlays(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        reason: str,
        burst_count: int,
        same_tick_open_burst_count: int,
        same_bar_open_burst_count: int,
        total_open_count: int,
    ) -> bool:
        enabled: list[str] = []
        if not bool(self.state.guard_open_admission):
            self.state.guard_open_admission = True
            enabled.append("guard_open_admission")
        if not bool(self.state.cluster_aware_escape):
            self.state.cluster_aware_escape = True
            enabled.append("cluster_aware_escape")
        if not bool(self.state.suppress_additional_levels_after_burst):
            self.state.suppress_additional_levels_after_burst = True
            enabled.append("suppress_additional_levels_after_burst")
        if not enabled:
            return False
        self.state.adaptive_overlay_autopilot_triggered = True
        self.state.adaptive_overlay_autopilot_triggered_time = int(tick.get("time", 0) or 0)
        self.state.adaptive_overlay_autopilot_reason = str(reason or "")
        self._record_event(
            event_path,
            "adaptive_overlay_autopilot_armed",
            tick,
            reason=str(reason or ""),
            enabled_overlays=enabled,
            burst_open_threshold=int(self.state.burst_open_threshold or 0),
            burst_count=int(burst_count),
            same_tick_open_burst_count=int(same_tick_open_burst_count),
            same_bar_open_burst_count=int(same_bar_open_burst_count),
            total_open_count=int(total_open_count),
        )
        return True

    def _maybe_activate_adaptive_overlays(
        self,
        tickets: list[TickTicket],
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        same_tick_open_burst_count: int | None = None,
        same_bar_open_burst_count: int | None = None,
    ) -> bool:
        if not bool(self.state.adaptive_overlay_autopilot) or bool(self.state.adaptive_overlay_autopilot_triggered):
            return False
        observed_same_tick = max(
            int(same_tick_open_burst_count or 0),
            max((int(ticket.same_tick_open_burst_count_at_open or 0) for ticket in tickets), default=0),
        )
        observed_same_bar = max(
            int(same_bar_open_burst_count or 0),
            max((int(ticket.same_bar_open_burst_count_at_open or 0) for ticket in tickets), default=0),
        )
        burst_count = max(observed_same_tick, observed_same_bar)
        if burst_count < int(self.state.burst_open_threshold or 0):
            return False
        return self._activate_adaptive_overlays(
            event_path=event_path,
            tick=tick,
            reason="burst_concentration_detected",
            burst_count=burst_count,
            same_tick_open_burst_count=observed_same_tick,
            same_bar_open_burst_count=observed_same_bar,
            total_open_count=len(tickets),
        )

    def _classify_first_path_verdict(self, *, ticket: TickTicket, realized_pnl: float) -> str:
        saw_green = bool(ticket.first_green_seen)
        if float(realized_pnl) < 0.0 and not saw_green:
            return "never_green_toxic_continuation"
        if float(realized_pnl) < 0.0 and saw_green:
            return "went_green_failed_monetization"
        if float(realized_pnl) >= 0.0 and saw_green:
            return "green_and_monetized"
        return "closed_without_recorded_green"

    def _register_first_path_close(
        self,
        *,
        event_path: Path | None,
        tick: dict[str, Any],
        action: str,
        ticket: TickTicket,
        realized_pnl: float,
        remaining_open_count: int,
        emit: bool,
    ) -> None:
        if bool(self.state.first_path_close_seen):
            return
        verdict = self._classify_first_path_verdict(ticket=ticket, realized_pnl=realized_pnl)
        self.state.first_path_close_seen = True
        self.state.first_path_close_time = int(tick.get("time", 0) or 0)
        self.state.first_path_close_action = str(action or "")
        self.state.first_path_close_realized_pnl = round(float(realized_pnl), 3)
        self.state.first_path_verdict = verdict
        if emit:
            self._record_event(
                event_path,
                "first_path_verdict_locked",
                tick,
                verdict=verdict,
                close_action=str(action or ""),
                direction=ticket.direction,
                trigger_level=round(float(ticket.trigger_level), 6),
                realized_pnl=round(float(realized_pnl), 3),
                first_green_seen=bool(ticket.first_green_seen),
                same_tick_open_burst_count=int(ticket.same_tick_open_burst_count_at_open or 0),
                same_bar_open_burst_count=int(ticket.same_bar_open_burst_count_at_open or 0),
                hold_seconds=max(0, int(tick.get("time", 0) or 0) - int(ticket.opened_time or 0)),
                remaining_open_count=max(0, int(remaining_open_count or 0)),
            )
        if verdict not in {"never_green_toxic_continuation", "went_green_failed_monetization"}:
            return
        same_tick_open_burst_count = int(ticket.same_tick_open_burst_count_at_open or 0)
        same_bar_open_burst_count = int(ticket.same_bar_open_burst_count_at_open or 0)
        self._activate_adaptive_overlays(
            event_path=event_path,
            tick=tick,
            reason=f"first_path_{verdict}",
            burst_count=max(same_tick_open_burst_count, same_bar_open_burst_count),
            same_tick_open_burst_count=same_tick_open_burst_count,
            same_bar_open_burst_count=same_bar_open_burst_count,
            total_open_count=max(0, int(remaining_open_count or 0)),
        )

    def _execute_action(self, request: dict[str, Any], action_sink: ActionSink | None) -> dict[str, Any]:
        if action_sink is None:
            return {"ok": True, "fill_price": float(request["fill_price"])}
        return action_sink(request)

    def _hold_admission_allows_direction(self, tickets: list[TickTicket], direction: str) -> bool:
        if not (self.positive_only_closes and self.state.positive_only_hold_active and tickets):
            return True
        normalized_direction = str(direction or "").upper()
        live_directions = {
            str(ticket.direction or "").upper()
            for ticket in tickets
            if str(ticket.direction or "").upper() in {"SELL", "BUY"}
        }
        if live_directions == {"SELL"}:
            return normalized_direction == "BUY"
        if live_directions == {"BUY"}:
            return normalized_direction == "SELL"
        return False

    def _open_request(self, direction: str, trigger_level: float, tick: dict[str, Any], *, level_idx: int, from_rearm: bool) -> dict[str, Any]:
        executable_price = float(tick["ask"] if str(direction).upper() == "BUY" else tick["bid"])
        return {
            "kind": "open",
            "symbol": self.symbol,
            "direction": str(direction).upper(),
            "trigger_level": float(trigger_level),
            "fill_price": executable_price,
            "time": int(tick["time"]),
            "time_msc": int(tick["time_msc"]),
            "from_rearm": bool(from_rearm),
            "level_idx": int(level_idx),
        }

    def _close_request(self, ticket: TickTicket, close_threshold: float, tick: dict[str, Any]) -> dict[str, Any]:
        close_price = float(tick["bid"] if ticket.direction == "BUY" else tick["ask"])
        return {
            "kind": "close",
            "symbol": self.symbol,
            "direction": ticket.direction,
            "trigger_level": float(ticket.trigger_level),
            "fill_price": close_price,
            "close_threshold": float(close_threshold),
            "time": int(tick["time"]),
            "time_msc": int(tick["time_msc"]),
            "ticket": asdict(ticket),
        }

    def _ensure_anchor_if_flat(self) -> bool:
        if self.state.anchor != 0.0:
            return True
        bars = self.history[-max(int(self.cfg.regime_lookback_bars), int(self.cfg.vwap_lookback), 1):]
        if not bars:
            return False
        idx = len(self.history)
        regime_high, regime_low = recent_range(self.history, idx, self.cfg.regime_lookback_bars)
        regime_width_pips = (regime_high - regime_low) / self.pip_size if self.pip_size > 0 else 0.0
        if regime_width_pips > float(self.cfg.max_range_pips):
            return False
        self.state.anchor = vwap_anchor(self.history, idx, self.cfg.vwap_lookback)
        self.state.next_sell_level = self.state.anchor + self.base_step_px
        self.state.next_buy_level = self.state.anchor - self.base_step_px
        return True

    def process_tick(self, tick: dict[str, Any], *, action_sink: ActionSink | None = None, event_path: Path | None = None, emit: bool = True) -> None:
        self._update_bar(tick)
        if not self._ensure_anchor_if_flat():
            self.state.last_tick_time = int(tick["time"])
            self.state.last_tick_msc = int(tick["time_msc"])
            return
        tickets = [deserialize_tick_ticket(t) for t in self.state.open_tickets]
        tokens = [TickRearmToken(**t) for t in self.state.rearm_tokens]
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        tick_time = int(tick["time"])
        tick_msc = int(tick["time_msc"])
        mid = self._tick_mid(tick)
        update_ticket_path_metrics(tickets, symbol=self.symbol, tick=tick, volume=self.volume)
        self._maybe_activate_adaptive_overlays(
            tickets,
            event_path=event_path,
            tick=tick,
        )

        for token in tokens:
            if token.armed:
                continue
            if tick_time < int(token.cooldown_until_time or 0):
                continue
            if token.direction == "SELL":
                if bid <= float(token.level) - (self.variant.excursion_levels * self.base_step_px):
                    token.armed = True
                    if int(token.armed_at_time or 0) <= 0:
                        token.armed_at_time = tick_time
            else:
                if ask >= float(token.level) + (self.variant.excursion_levels * self.base_step_px):
                    token.armed = True
                    if int(token.armed_at_time or 0) <= 0:
                        token.armed_at_time = tick_time

        open_buy = sum(1 for t in tickets if t.direction == "BUY")
        open_sell = sum(1 for t in tickets if t.direction == "SELL")
        current_sell_step = dynamic_step(self.base_step_px, open_sell, self.cfg)
        current_buy_step = dynamic_step(self.base_step_px, open_buy, self.cfg)
        hold_active = bool(self.positive_only_closes and self.state.positive_only_hold_active and tickets)
        allow_sell_during_hold = self._hold_admission_allows_direction(tickets, "SELL")
        allow_buy_during_hold = self._hold_admission_allows_direction(tickets, "BUY")

        while allow_sell_during_hold and bid >= float(self.state.next_sell_level) and open_sell < int(self.cfg.max_open_per_side):
            guard_allows, recovered_count = self._guard_open_admission_allows(tickets, "SELL")
            if not guard_allows:
                if emit:
                    self._emit_guard_open_event(
                        event_path=event_path,
                        tick=tick,
                        direction="SELL",
                        stage="main",
                        trigger_level=float(self.state.next_sell_level),
                        side_open_count=sum(1 for ticket in tickets if ticket.direction == "SELL"),
                        recovered_count=recovered_count,
                    )
                break
            suppressed, same_tick_open_burst_count, same_bar_open_burst_count = self._burst_suppression_state(
                tickets,
                direction="SELL",
                tick_time=tick_time,
                tick_msc=tick_msc,
            )
            if suppressed:
                if emit:
                    self._record_event(
                        event_path,
                        "open_suppressed_after_burst",
                        tick,
                        direction="SELL",
                        stage="main",
                        burst_open_threshold=int(self.state.burst_open_threshold or 0),
                        same_tick_open_burst_count=same_tick_open_burst_count,
                        same_bar_open_burst_count=same_bar_open_burst_count,
                    )
                break
            spread_allows, spread_px, spread_ratio = self._entry_spread_allows(tick=tick)
            if not spread_allows:
                if emit:
                    self._emit_spread_block_event(
                        event_path=event_path,
                        tick=tick,
                        direction="SELL",
                        stage="main",
                        trigger_level=float(self.state.next_sell_level),
                        spread_px=spread_px,
                        spread_ratio=spread_ratio,
                    )
                break
            trigger_level = float(self.state.next_sell_level)
            level_idx = self._ticket_level_idx("SELL", trigger_level)
            result = self._execute_action(self._open_request("SELL", trigger_level, tick, level_idx=level_idx, from_rearm=False), action_sink)
            if not result.get("ok"):
                break
            ticket_obj = TickTicket(direction="SELL", trigger_level=trigger_level, fill_price=float(result.get("fill_price", bid)), opened_time=tick_time, opened_msc=tick_msc, level_idx=level_idx, live_ticket=int(result.get("live_ticket", 0) or 0), position_comment=str(result.get("position_comment", "") or ""))
            self.state.realized_net_usd += float(result.get("realized_pnl", 0.0) or 0.0)
            tickets.append(ticket_obj)
            if self.state.lattice_started_time <= 0:
                self.state.lattice_started_time = tick_time
            open_sell += 1
            initialize_ticket_telemetry(
                ticket_obj,
                tick=tick,
                anchor=float(self.state.anchor),
                base_step_px=self.base_step_px,
                side_open_count=open_sell,
                total_open_count=len(tickets),
                same_tick_open_burst_count=sum(1 for ticket in tickets if int(ticket.opened_msc or 0) == tick_msc),
                same_bar_open_burst_count=sum(
                    1 for ticket in tickets if bucket_start(int(ticket.opened_time or 0), self.timeframe_name) == bucket_start(tick_time, self.timeframe_name)
                ),
            )
            if emit:
                self._record_event(event_path, "open_ticket", tick, direction="SELL", trigger_level=round(trigger_level, 6), fill_price=round(ticket_obj.fill_price, 6), level_idx=level_idx, spread_at_entry=round(ticket_obj.spread_px_at_open, 6), entry_context=ticket_obj.entry_context, session_bucket=ticket_obj.session_bucket_at_open, regime_at_entry=ticket_obj.regime_at_entry, latest_tick_source_last=ticket_obj.latest_tick_source_last, tick_history_source_last=ticket_obj.tick_history_source_last, base_step_px_at_open=round(ticket_obj.base_step_px_at_open, 6), side_open_count_at_open=ticket_obj.side_open_count_at_open, total_open_count_at_open=ticket_obj.total_open_count_at_open, same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open, same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open, anchor_distance_px_at_open=round(ticket_obj.anchor_distance_px_at_open, 6))
            self._maybe_activate_adaptive_overlays(
                tickets,
                event_path=event_path,
                tick=tick,
                same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
            )
            current_sell_step = dynamic_step(self.base_step_px, open_sell, self.cfg)
            self.state.next_sell_level += current_sell_step

        while allow_buy_during_hold and ask <= float(self.state.next_buy_level) and open_buy < int(self.cfg.max_open_per_side):
            guard_allows, recovered_count = self._guard_open_admission_allows(tickets, "BUY")
            if not guard_allows:
                if emit:
                    self._emit_guard_open_event(
                        event_path=event_path,
                        tick=tick,
                        direction="BUY",
                        stage="main",
                        trigger_level=float(self.state.next_buy_level),
                        side_open_count=sum(1 for ticket in tickets if ticket.direction == "BUY"),
                        recovered_count=recovered_count,
                    )
                break
            suppressed, same_tick_open_burst_count, same_bar_open_burst_count = self._burst_suppression_state(
                tickets,
                direction="BUY",
                tick_time=tick_time,
                tick_msc=tick_msc,
            )
            if suppressed:
                if emit:
                    self._record_event(
                        event_path,
                        "open_suppressed_after_burst",
                        tick,
                        direction="BUY",
                        stage="main",
                        burst_open_threshold=int(self.state.burst_open_threshold or 0),
                        same_tick_open_burst_count=same_tick_open_burst_count,
                        same_bar_open_burst_count=same_bar_open_burst_count,
                    )
                break
            spread_allows, spread_px, spread_ratio = self._entry_spread_allows(tick=tick)
            if not spread_allows:
                if emit:
                    self._emit_spread_block_event(
                        event_path=event_path,
                        tick=tick,
                        direction="BUY",
                        stage="main",
                        trigger_level=float(self.state.next_buy_level),
                        spread_px=spread_px,
                        spread_ratio=spread_ratio,
                    )
                break
            trigger_level = float(self.state.next_buy_level)
            level_idx = self._ticket_level_idx("BUY", trigger_level)
            result = self._execute_action(self._open_request("BUY", trigger_level, tick, level_idx=level_idx, from_rearm=False), action_sink)
            if not result.get("ok"):
                break
            ticket_obj = TickTicket(direction="BUY", trigger_level=trigger_level, fill_price=float(result.get("fill_price", ask)), opened_time=tick_time, opened_msc=tick_msc, level_idx=level_idx, live_ticket=int(result.get("live_ticket", 0) or 0), position_comment=str(result.get("position_comment", "") or ""))
            self.state.realized_net_usd += float(result.get("realized_pnl", 0.0) or 0.0)
            tickets.append(ticket_obj)
            if self.state.lattice_started_time <= 0:
                self.state.lattice_started_time = tick_time
            open_buy += 1
            initialize_ticket_telemetry(
                ticket_obj,
                tick=tick,
                anchor=float(self.state.anchor),
                base_step_px=self.base_step_px,
                side_open_count=open_buy,
                total_open_count=len(tickets),
                same_tick_open_burst_count=sum(1 for ticket in tickets if int(ticket.opened_msc or 0) == tick_msc),
                same_bar_open_burst_count=sum(
                    1 for ticket in tickets if bucket_start(int(ticket.opened_time or 0), self.timeframe_name) == bucket_start(tick_time, self.timeframe_name)
                ),
            )
            if emit:
                self._record_event(event_path, "open_ticket", tick, direction="BUY", trigger_level=round(trigger_level, 6), fill_price=round(ticket_obj.fill_price, 6), level_idx=level_idx, spread_at_entry=round(ticket_obj.spread_px_at_open, 6), entry_context=ticket_obj.entry_context, session_bucket=ticket_obj.session_bucket_at_open, regime_at_entry=ticket_obj.regime_at_entry, latest_tick_source_last=ticket_obj.latest_tick_source_last, tick_history_source_last=ticket_obj.tick_history_source_last, base_step_px_at_open=round(ticket_obj.base_step_px_at_open, 6), side_open_count_at_open=ticket_obj.side_open_count_at_open, total_open_count_at_open=ticket_obj.total_open_count_at_open, same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open, same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open, anchor_distance_px_at_open=round(ticket_obj.anchor_distance_px_at_open, 6))
            self._maybe_activate_adaptive_overlays(
                tickets,
                event_path=event_path,
                tick=tick,
                same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
            )
            current_buy_step = dynamic_step(self.base_step_px, open_buy, self.cfg)
            self.state.next_buy_level -= current_buy_step

        for direction in ("SELL", "BUY"):
            if hold_active and not self._hold_admission_allows_direction(tickets, direction):
                continue
            side_open = sum(1 for t in tickets if t.direction == direction)
            for token in list(tokens):
                if token.direction != direction or not token.armed:
                    continue
                guard_allows, recovered_count = self._guard_open_admission_allows(tickets, direction)
                if not guard_allows:
                    if emit:
                        self._emit_guard_open_event(
                            event_path=event_path,
                            tick=tick,
                            direction=direction,
                            stage="rearm",
                            trigger_level=float(token.level),
                            side_open_count=sum(1 for ticket in tickets if ticket.direction == direction),
                            recovered_count=recovered_count,
                        )
                    break
                if side_open >= int(self.cfg.max_open_per_side):
                    break
                suppressed, same_tick_open_burst_count, same_bar_open_burst_count = self._burst_suppression_state(
                    tickets,
                    direction=direction,
                    tick_time=tick_time,
                    tick_msc=tick_msc,
                )
                if suppressed:
                    if emit:
                        self._record_event(
                            event_path,
                            "open_suppressed_after_burst",
                            tick,
                            direction=direction,
                            stage="rearm",
                            burst_open_threshold=int(self.state.burst_open_threshold or 0),
                            same_tick_open_burst_count=same_tick_open_burst_count,
                            same_bar_open_burst_count=same_bar_open_burst_count,
                            rearm_open=True,
                        )
                    break
                spread_allows, spread_px, spread_ratio = self._entry_spread_allows(tick=tick)
                if not spread_allows:
                    if emit:
                        self._emit_spread_block_event(
                            event_path=event_path,
                            tick=tick,
                            direction=direction,
                            stage="rearm",
                            trigger_level=float(token.level),
                            spread_px=spread_px,
                            spread_ratio=spread_ratio,
                        )
                    break
                if direction == "SELL" and bid < float(token.level):
                    continue
                if direction == "BUY" and ask > float(token.level):
                    continue
                result = self._execute_action(self._open_request(direction, float(token.level), tick, level_idx=int(token.level_idx), from_rearm=True), action_sink)
                if not result.get("ok"):
                    continue
                ticket_obj = TickTicket(direction=direction, trigger_level=float(token.level), fill_price=float(result.get("fill_price", ask if direction == "BUY" else bid)), opened_time=tick_time, opened_msc=tick_msc, level_idx=int(token.level_idx), from_rearm=True, live_ticket=int(result.get("live_ticket", 0) or 0), position_comment=str(result.get("position_comment", "") or ""))
                self.state.realized_net_usd += float(result.get("realized_pnl", 0.0) or 0.0)
                tickets.append(ticket_obj)
                tokens.remove(token)
                side_open += 1
                self.state.rearm_opens += 1
                initialize_ticket_telemetry(
                    ticket_obj,
                    tick=tick,
                    anchor=float(self.state.anchor),
                    base_step_px=self.base_step_px,
                    side_open_count=side_open,
                    total_open_count=len(tickets),
                    same_tick_open_burst_count=sum(1 for ticket in tickets if int(ticket.opened_msc or 0) == tick_msc),
                    same_bar_open_burst_count=sum(
                        1 for ticket in tickets if bucket_start(int(ticket.opened_time or 0), self.timeframe_name) == bucket_start(tick_time, self.timeframe_name)
                    ),
                )
                if emit:
                    created_time = int(token.created_time or 0)
                    armed_at_time = int(token.armed_at_time or 0)
                    self._record_event(event_path, "open_ticket", tick, direction=direction, trigger_level=round(ticket_obj.trigger_level, 6), fill_price=round(ticket_obj.fill_price, 6), level_idx=int(token.level_idx), rearm_open=True, rearm_variant=self.variant.name, spread_at_entry=round(ticket_obj.spread_px_at_open, 6), entry_context=ticket_obj.entry_context, session_bucket=ticket_obj.session_bucket_at_open, regime_at_entry=ticket_obj.regime_at_entry, latest_tick_source_last=ticket_obj.latest_tick_source_last, tick_history_source_last=ticket_obj.tick_history_source_last, base_step_px_at_open=round(ticket_obj.base_step_px_at_open, 6), side_open_count_at_open=ticket_obj.side_open_count_at_open, total_open_count_at_open=ticket_obj.total_open_count_at_open, same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open, same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open, anchor_distance_px_at_open=round(ticket_obj.anchor_distance_px_at_open, 6), token_age_at_fire=(tick_time - created_time) if created_time > 0 else None, token_age_at_fire_seconds=(tick_time - created_time) if created_time > 0 else None, armed_duration_seconds=(tick_time - armed_at_time) if armed_at_time > 0 else None)
                self._maybe_activate_adaptive_overlays(
                    tickets,
                    event_path=event_path,
                    tick=tick,
                    same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                    same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
                )
                self._maybe_activate_adaptive_overlays(
                    tickets,
                    event_path=event_path,
                    tick=tick,
                    same_tick_open_burst_count=ticket_obj.same_tick_open_burst_count_at_open,
                    same_bar_open_burst_count=ticket_obj.same_bar_open_burst_count_at_open,
                )

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.trigger_level, reverse=True)
        while len(sells) > self.close_gap and ask <= float(sells[self.close_gap].trigger_level):
            closeable_positions: list[int] = []
            for pos, ticket in enumerate(sells):
                pnl = tick_pnl_usd(self.symbol, "SELL", ticket.fill_price, ask, volume=self.volume)
                if pnl < self.min_positive_close_profit_usd:
                    continue
                if tick_same_bar_hurdle_applies(
                    ticket=ticket,
                    tick_time=tick_time,
                    timeframe_name=self.timeframe_name,
                    pnl=pnl,
                    min_pnl=self.same_bar_min_pnl,
                    shallow_level_cap=self.same_bar_shallow_level_cap,
                    anchor=float(self.state.anchor),
                    base_step_px=self.base_step_px,
                ):
                    continue
                closeable_positions.append(pos)
            close_positions = select_close_positions(len(sells), self.close_gap, self.close_style, closeable_positions)
            if not close_positions:
                break
            closed_any = False
            for pos in sorted(set(close_positions), reverse=True):
                ticket = sells[pos]
                result = self._execute_action(self._close_request(ticket, float(sells[self.close_gap].trigger_level), tick), action_sink)
                if not result.get("ok"):
                    continue
                close_fill = float(result.get("fill_price", ask))
                pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, "SELL", ticket.fill_price, close_fill, volume=self.volume)))
                self.state.realized_net_usd += pnl
                self.state.realized_closes += 1
                tickets.remove(ticket)
                self._register_first_path_close(
                    event_path=event_path,
                    tick=tick,
                    action="close_ticket",
                    ticket=ticket,
                    realized_pnl=pnl,
                    remaining_open_count=len(tickets),
                    emit=emit,
                )
                if int(ticket.level_idx or 0) >= int(self.variant.min_level_idx):
                    tokens.append(TickRearmToken(direction="SELL", level=float(ticket.trigger_level), level_idx=int(ticket.level_idx), created_time=tick_time))
                if emit:
                    self._record_event(event_path, "close_ticket", tick, direction="SELL", trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(pnl, 3), rearm_variant=self.variant.name, **ticket_event_payload(ticket, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name))
                closed_any = True
            if not closed_any:
                break
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.trigger_level, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.trigger_level)
        while len(buys) > self.close_gap and bid >= float(buys[self.close_gap].trigger_level):
            closeable_positions: list[int] = []
            for pos, ticket in enumerate(buys):
                pnl = tick_pnl_usd(self.symbol, "BUY", ticket.fill_price, bid, volume=self.volume)
                if pnl < self.min_positive_close_profit_usd:
                    continue
                if tick_same_bar_hurdle_applies(
                    ticket=ticket,
                    tick_time=tick_time,
                    timeframe_name=self.timeframe_name,
                    pnl=pnl,
                    min_pnl=self.same_bar_min_pnl,
                    shallow_level_cap=self.same_bar_shallow_level_cap,
                    anchor=float(self.state.anchor),
                    base_step_px=self.base_step_px,
                ):
                    continue
                closeable_positions.append(pos)
            close_positions = select_close_positions(len(buys), self.close_gap, self.close_style, closeable_positions)
            if not close_positions:
                break
            closed_any = False
            for pos in sorted(set(close_positions), reverse=True):
                ticket = buys[pos]
                result = self._execute_action(self._close_request(ticket, float(buys[self.close_gap].trigger_level), tick), action_sink)
                if not result.get("ok"):
                    continue
                close_fill = float(result.get("fill_price", bid))
                pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, "BUY", ticket.fill_price, close_fill, volume=self.volume)))
                self.state.realized_net_usd += pnl
                self.state.realized_closes += 1
                tickets.remove(ticket)
                self._register_first_path_close(
                    event_path=event_path,
                    tick=tick,
                    action="close_ticket",
                    ticket=ticket,
                    realized_pnl=pnl,
                    remaining_open_count=len(tickets),
                    emit=emit,
                )
                if int(ticket.level_idx or 0) >= int(self.variant.min_level_idx):
                    tokens.append(TickRearmToken(direction="BUY", level=float(ticket.trigger_level), level_idx=int(ticket.level_idx), created_time=tick_time))
                if emit:
                    self._record_event(event_path, "close_ticket", tick, direction="BUY", trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(pnl, 3), rearm_variant=self.variant.name, **ticket_event_payload(ticket, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name))
                closed_any = True
            if not closed_any:
                break
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.trigger_level)

        # ── Close-at-float-zero: harvest all profitable positions when total floating PnL >= 0
        if self.close_at_float_zero and tickets:
            total_floating = sum(
                tick_pnl_usd(self.symbol, t.direction, t.fill_price, bid if t.direction == "BUY" else ask, volume=self.volume)
                for t in tickets
            )
            if total_floating >= 0:
                for ticket in list(tickets):
                    pnl = tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, bid if ticket.direction == "BUY" else ask, volume=self.volume)
                    if pnl >= self.min_positive_close_profit_usd:
                        result = self._execute_action(self._close_request(ticket, float(ticket.trigger_level), tick), action_sink)
                        if result.get("ok"):
                            close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                            pnl = float(result.get("realized_pnl", pnl))
                            self.state.realized_net_usd += pnl
                            self.state.realized_closes += 1
                            tickets.remove(ticket)
                            self._register_first_path_close(
                                event_path=event_path,
                                tick=tick,
                                action="close_at_float_zero",
                                ticket=ticket,
                                realized_pnl=pnl,
                                remaining_open_count=len(tickets),
                                emit=emit,
                            )
                            if emit:
                                self._record_event(event_path, "close_at_float_zero", tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(pnl, 3), rearm_variant=self.variant.name, **ticket_event_payload(ticket, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name))

        if tickets:
            floating = [(ticket, tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, bid if ticket.direction == "BUY" else ask, volume=self.volume)) for ticket in tickets]
            worst_pnl = min(pnl for _, pnl in floating)
            breakout_up = mid >= float(self.state.anchor) + self.breakout_buffer_px + (self.base_step_px * 0.5)
            breakout_down = mid <= float(self.state.anchor) - self.breakout_buffer_px - (self.base_step_px * 0.5)
            timed_out = self.state.lattice_started_time > 0 and (tick_time - int(self.state.lattice_started_time)) >= (int(self.cfg.max_lattice_window_bars) * timeframe_seconds(self.timeframe_name))
            if worst_pnl <= float(self.cfg.max_floating_loss_usd) and bool(self.state.cluster_aware_escape):
                clusters = group_floating_by_fill_cluster(
                    floating,
                    fill_tolerance=float(self.state.cluster_fill_tolerance or 0.01),
                )
                escaped_any = False
                for cluster in clusters:
                    cluster_total_pnl = sum(pnl for _, pnl in cluster)
                    cluster_size = len(cluster)
                    scaled_threshold = abs(float(self.cfg.max_floating_loss_usd)) * (cluster_size ** 0.5)
                    if cluster_total_pnl > -scaled_threshold:
                        continue
                    if self.positive_only_closes:
                        self._activate_positive_only_hold(
                            event_path=event_path,
                            tick=tick,
                            reason="bounded_cluster_escape_blocked_negative",
                            blocked_pnl=cluster_total_pnl,
                            blocked_ticket_count=cluster_size,
                            emit=emit,
                        )
                        break
                    for ticket, _cluster_pnl in list(cluster):
                        result = self._execute_action(self._close_request(ticket, float(ticket.trigger_level), tick), action_sink)
                        if not result.get("ok"):
                            continue
                        close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                        pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                        self.state.realized_net_usd += pnl
                        self.state.realized_closes += 1
                        tickets.remove(ticket)
                        self._register_first_path_close(
                            event_path=event_path,
                            tick=tick,
                            action="bounded_cluster_escape",
                            ticket=ticket,
                            realized_pnl=pnl,
                            remaining_open_count=len(tickets),
                            emit=emit,
                        )
                        if emit:
                            self._record_event(
                                event_path,
                                "bounded_cluster_escape",
                                tick,
                                direction=ticket.direction,
                                trigger_level=round(ticket.trigger_level, 6),
                                entry_fill_price=round(ticket.fill_price, 6),
                                exit_fill_price=round(close_fill, 6),
                                realized_pnl=round(pnl, 3),
                                cluster_size=cluster_size,
                                cluster_total_pnl=round(cluster_total_pnl, 3),
                                scaled_threshold=round(scaled_threshold, 3),
                                **ticket_event_payload(ticket, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name),
                            )
                        escaped_any = True
                    break
                if escaped_any:
                    floating = [
                        (ticket, tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, bid if ticket.direction == "BUY" else ask, volume=self.volume))
                        for ticket in tickets
                    ]
                    worst_pnl = min((pnl for _, pnl in floating), default=0.0)
            if worst_pnl <= float(self.cfg.max_floating_loss_usd) or breakout_up or breakout_down or timed_out:
                if self.positive_only_closes and worst_pnl < 0:
                    self._activate_positive_only_hold(
                        event_path=event_path,
                        tick=tick,
                        reason="bounded_forced_unwind_blocked_negative",
                        blocked_pnl=worst_pnl,
                        blocked_ticket_count=len(tickets),
                        emit=emit,
                    )
                    tokens = []
                else:
                    for ticket, _pnl in list(floating):
                        result = self._execute_action(self._close_request(ticket, float(ticket.trigger_level), tick), action_sink)
                        if not result.get("ok"):
                            continue
                        close_fill = float(result.get("fill_price", bid if ticket.direction == "BUY" else ask))
                        pnl = float(result.get("realized_pnl", tick_pnl_usd(self.symbol, ticket.direction, ticket.fill_price, close_fill, volume=self.volume)))
                        self.state.realized_net_usd += pnl
                        self.state.realized_closes += 1
                        self._register_first_path_close(
                            event_path=event_path,
                            tick=tick,
                            action="forced_unwind" if worst_pnl <= float(self.cfg.max_floating_loss_usd) else ("breakout_kill" if breakout_up or breakout_down else "timed_kill"),
                            ticket=ticket,
                            realized_pnl=pnl,
                            remaining_open_count=max(0, len(tickets) - 1),
                            emit=emit,
                        )
                        if emit:
                            reason = "forced_unwind" if worst_pnl <= float(self.cfg.max_floating_loss_usd) else ("breakout_kill" if breakout_up or breakout_down else "timed_kill")
                            self._record_event(event_path, reason, tick, direction=ticket.direction, trigger_level=round(ticket.trigger_level, 6), entry_fill_price=round(ticket.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(pnl, 3), **ticket_event_payload(ticket, tick=tick, realized_pnl=pnl, timeframe_name=self.timeframe_name))
                        tickets.remove(ticket)
                    tokens = []
                    self.state.lattice_started_time = 0
                    self.state.anchor_resets += 1
                    self.state.anchor_resets_risk += 1

        if not tickets:
            self.state.lattice_started_time = 0
            self.state.positive_only_hold_active = False
            self.state.positive_only_hold_reason = ""
            self.state.positive_only_hold_since = 0
            idx = len(self.history)
            if idx > 0:
                candidate_anchor = vwap_anchor(self.history, idx, self.cfg.vwap_lookback)
                if abs(candidate_anchor - float(self.state.anchor)) >= self.base_step_px:
                    self.state.anchor = candidate_anchor
                    self.state.next_sell_level = self.state.anchor + self.base_step_px
                    self.state.next_buy_level = self.state.anchor - self.base_step_px
                    self.state.anchor_resets += 1
                    self.state.anchor_resets_flat += 1
                    tokens = []

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.rearm_tokens = [asdict(t) for t in tokens]
        self.state.last_tick_time = tick_time
        self.state.last_tick_msc = tick_msc
        self.state.last_bar_time = bucket_start(tick_time, self.timeframe_name)
        self.state.max_open_total = max(int(self.state.max_open_total or 0), len(tickets))

    def process_ticks(self, ticks: list[dict[str, Any]], *, action_sink: ActionSink | None = None, event_path: Path | None = None, emit: bool = True) -> int:
        count = 0
        for tick in sorted(ticks, key=lambda item: (int(item["time_msc"]), int(item["time"]))):
            if int(tick["time_msc"]) <= int(self.state.last_tick_msc or 0):
                continue
            self.process_tick(tick, action_sink=action_sink, event_path=event_path, emit=emit)
            count += 1
        return count


def engine_from_args(
    *,
    symbol: str,
    timeframe_name: str,
    step: float,
    max_open_per_side: int,
    variant_name: str,
    close_alpha: float = 0.0,
    close_style: str = "all_profitable",
    momentum_gate: bool,
    cooldown_bars: int,
    sell_gap: int,
    buy_gap: int,
    step_sell: float | None = None,
    step_buy: float | None = None,
    volume: float = VOLUME,
    max_floating_loss_usd: float = -10.0,
    max_lattice_window_bars: int = 240,
    breakout_buffer_pips: float = 0.0,
    escape_bars: int = 0,
    escape_threshold_usd: float = 0.0,
    cluster_aware_escape: bool = False,
    cluster_fill_tolerance: float = 0.01,
    guard_open_admission: bool = False,
    offensive_closure_enabled: bool = True,
    offensive_safety_margin_usd: float = 2.0,
    offensive_safety_margin_pct: float = 0.20,
    offensive_cut_cooldown_bars: int = 5,
    offensive_breakeven_band_usd: float = 0.50,
    offensive_budget_share: float = 0.25,
    suppress_additional_levels_after_burst: bool = False,
    burst_open_threshold: int = 2,
    max_entry_spread_ratio: float = 0.0,
    liquidity_gap_spread_multiplier: float = 0.0,
    liquidity_gap_spread_lookback: int = 0,
    liquidity_gap_spread_floor_ratio: float = 0.0,
    liquidity_gap_spread_max_ratio: float = 0.0,
    adaptive_overlay_autopilot: bool = False,
    allow_dynamic_geometry: bool = True,
    proven_step_ceiling: float = 0.0,
    proven_step_buy_ceiling: float = 0.0,
    proven_step_sell_ceiling: float = 0.0,
    min_positive_close_profit_usd: float = 0.0,
    positive_only_closes: bool = False,
    close_at_float_zero: bool = False,
) -> TickStatefulRearmEngine:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Missing symbol info for {symbol}")
    pip_size = float(pip_size_for(info) or 0.0)
    if pip_size <= 0.0:
        raise RuntimeError(f"Invalid pip size for {symbol}")
    variant = REARM_VARIANTS.get(str(variant_name or ""))
    if variant is None:
        raise RuntimeError(f"Unknown rearm variant: {variant_name}")
    cfg = RawConfig(step_pips=float(step) / pip_size, max_open_per_side=int(max_open_per_side), close_mode="two_level")
    return TickStatefulRearmEngine(
        symbol,
        cfg,
        info,
        timeframe_name=timeframe_name,
        variant=variant,
        close_alpha=close_alpha,
        close_style=close_style,
        momentum_gate=momentum_gate,
        cooldown_bars=cooldown_bars,
        sell_gap=sell_gap,
        buy_gap=buy_gap,
        step_sell=step_sell,
        step_buy=step_buy,
        volume=volume,
        max_floating_loss_usd=max_floating_loss_usd,
        max_lattice_window_bars=max_lattice_window_bars,
        breakout_buffer_pips=breakout_buffer_pips,
        escape_bars=escape_bars,
        escape_threshold_usd=escape_threshold_usd,
        cluster_aware_escape=cluster_aware_escape,
        cluster_fill_tolerance=cluster_fill_tolerance,
        guard_open_admission=guard_open_admission,
        offensive_closure_enabled=offensive_closure_enabled,
        offensive_safety_margin_usd=offensive_safety_margin_usd,
        offensive_safety_margin_pct=offensive_safety_margin_pct,
        offensive_cut_cooldown_bars=offensive_cut_cooldown_bars,
        offensive_breakeven_band_usd=offensive_breakeven_band_usd,
        offensive_budget_share=offensive_budget_share,
        suppress_additional_levels_after_burst=suppress_additional_levels_after_burst,
        burst_open_threshold=burst_open_threshold,
        max_entry_spread_ratio=max_entry_spread_ratio,
        liquidity_gap_spread_multiplier=liquidity_gap_spread_multiplier,
        liquidity_gap_spread_lookback=liquidity_gap_spread_lookback,
        liquidity_gap_spread_floor_ratio=liquidity_gap_spread_floor_ratio,
        liquidity_gap_spread_max_ratio=liquidity_gap_spread_max_ratio,
        adaptive_overlay_autopilot=adaptive_overlay_autopilot,
        allow_dynamic_geometry=allow_dynamic_geometry,
        proven_step_ceiling=proven_step_ceiling,
        proven_step_buy_ceiling=proven_step_buy_ceiling,
        proven_step_sell_ceiling=proven_step_sell_ceiling,
        min_positive_close_profit_usd=min_positive_close_profit_usd,
        positive_only_closes=positive_only_closes,
        close_at_float_zero=close_at_float_zero,
    )


def bounded_engine_from_args(
    *,
    symbol: str,
    timeframe_name: str,
    cfg: BoundedConfig,
    variant_name: str,
    close_gap: int,
    close_style: str = "all_profitable",
    same_bar_min_pnl: float = 0.0,
    same_bar_shallow_level_cap: int = 0,
    cluster_aware_escape: bool = False,
    cluster_fill_tolerance: float = 0.01,
    guard_open_admission: bool = False,
    suppress_additional_levels_after_burst: bool = False,
    burst_open_threshold: int = 2,
    max_entry_spread_ratio: float = 0.0,
    adaptive_overlay_autopilot: bool = False,
    min_positive_close_profit_usd: float = 0.0,
    positive_only_closes: bool = False,
    close_at_float_zero: bool = False,
    volume: float = VOLUME,
) -> TickBoundedRearmEngine:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Missing symbol info for {symbol}")
    variant = REARM_VARIANTS.get(str(variant_name or ""))
    if variant is None:
        raise RuntimeError(f"Unknown rearm variant: {variant_name}")
    return TickBoundedRearmEngine(
        symbol,
        cfg,
        info,
        timeframe_name=timeframe_name,
        variant=variant,
        close_gap=close_gap,
        close_style=close_style,
        same_bar_min_pnl=same_bar_min_pnl,
        same_bar_shallow_level_cap=same_bar_shallow_level_cap,
        cluster_aware_escape=cluster_aware_escape,
        cluster_fill_tolerance=cluster_fill_tolerance,
        guard_open_admission=guard_open_admission,
        suppress_additional_levels_after_burst=suppress_additional_levels_after_burst,
        burst_open_threshold=burst_open_threshold,
        max_entry_spread_ratio=max_entry_spread_ratio,
        adaptive_overlay_autopilot=adaptive_overlay_autopilot,
        min_positive_close_profit_usd=min_positive_close_profit_usd,
        positive_only_closes=positive_only_closes,
        close_at_float_zero=close_at_float_zero,
        volume=volume,
    )
