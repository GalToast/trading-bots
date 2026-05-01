#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from statistics import median
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import MetaTrader5 as mt5

import live_penetration_lattice_mirror as live_mirror
import mt5_terminal_guard
from backtest_adaptive_deployment_study import compute_ema_ladders
from backtest_snake_counter_web import (
    SnakeContract,
    SnakeTicket,
    _cross_down_levels,
    _cross_up_levels,
    _resolve_controller_state,
    research_unit_pnl_usd,
)
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso
from penetration_lattice_lab_v2 import pip_size_for
from tick_penetration_lattice_core import (
    load_latest_tick,
    load_recent_bars,
    load_ticks_since_with_source,
    timeframe_seconds,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "snake_counter_web_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "snake_counter_web_shadow_events.jsonl"
DEFAULT_DIRECT_EXEC_STATE_PATH = ROOT / "reports" / "snake_counter_web_live_mirror_state.json"
DEFAULT_DIRECT_EXEC_LOG_PATH = ROOT / "reports" / "snake_counter_web_live_mirror_events.jsonl"
CHECKPOINT_TICK_INTERVAL = 25


@dataclass
class SnakeShadowState:
    symbol: str
    anchor: float = 0.0
    last_price: float = 0.0
    last_tick_msc: int = 0
    high_level: int = 0
    low_level: int = 0
    open_tickets: list[SnakeTicket] = field(default_factory=list)
    realized_net_usd: float = 0.0
    gross_positive_booked_usd: float = 0.0
    realized_closes: int = 0
    wins: int = 0
    opens: int = 0
    float_zero_closes: int = 0
    max_open_total: int = 0
    max_open_sell: int = 0
    max_open_buy: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "anchor": self.anchor,
            "last_price": self.last_price,
            "last_tick_msc": self.last_tick_msc,
            "high_level": self.high_level,
            "low_level": self.low_level,
            "open_tickets": [asdict(ticket) for ticket in self.open_tickets],
            "realized_net_usd": self.realized_net_usd,
            "gross_positive_booked_usd": self.gross_positive_booked_usd,
            "realized_closes": self.realized_closes,
            "wins": self.wins,
            "opens": self.opens,
            "float_zero_closes": self.float_zero_closes,
            "max_open_total": self.max_open_total,
            "max_open_sell": self.max_open_sell,
            "max_open_buy": self.max_open_buy,
            "open_count": len(self.open_tickets),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, symbol: str) -> "SnakeShadowState":
        state = cls(symbol=symbol)
        state.anchor = float(payload.get("anchor", 0.0) or 0.0)
        state.last_price = float(payload.get("last_price", 0.0) or 0.0)
        state.last_tick_msc = int(payload.get("last_tick_msc", 0) or 0)
        state.high_level = int(payload.get("high_level", 0) or 0)
        state.low_level = int(payload.get("low_level", 0) or 0)
        state.realized_net_usd = float(payload.get("realized_net_usd", 0.0) or 0.0)
        state.gross_positive_booked_usd = float(payload.get("gross_positive_booked_usd", 0.0) or 0.0)
        state.realized_closes = int(payload.get("realized_closes", 0) or 0)
        state.wins = int(payload.get("wins", 0) or 0)
        state.opens = int(payload.get("opens", 0) or 0)
        state.float_zero_closes = int(payload.get("float_zero_closes", 0) or 0)
        state.max_open_total = int(payload.get("max_open_total", 0) or 0)
        state.max_open_sell = int(payload.get("max_open_sell", 0) or 0)
        state.max_open_buy = int(payload.get("max_open_buy", 0) or 0)
        state.open_tickets = [
            SnakeTicket(
                direction=str(ticket.get("direction") or "").upper(),
                entry_price=float(ticket.get("entry_price", 0.0) or 0.0),
                opened_time=int(ticket.get("opened_time", 0) or 0),
                ticket_kind=str(ticket.get("ticket_kind") or "core"),
                live_ticket=int(ticket.get("live_ticket", 0) or 0),
                position_comment=str(ticket.get("position_comment") or ""),
                pair_id=int(ticket.get("pair_id", 0) or 0),
            )
            for ticket in (payload.get("open_tickets") or [])
        ]
        return state


def deal_net_usd(fill: dict[str, Any] | None) -> float:
    if not isinstance(fill, dict):
        return 0.0
    return float(fill.get("profit", 0.0) or 0.0) + float(fill.get("commission", 0.0) or 0.0) + float(fill.get("swap", 0.0) or 0.0) + float(fill.get("fee", 0.0) or 0.0)


def _deal_field(deal: Any, name: str, default: Any = None) -> Any:
    if isinstance(deal, dict):
        return deal.get(name, default)
    return getattr(deal, name, default)


def _deal_payload(deal: Any, *, fallback_symbol: str) -> dict[str, Any]:
    return {
        "ticket": int(_deal_field(deal, "ticket", 0) or 0),
        "order": int(_deal_field(deal, "order", 0) or 0),
        "position_id": int(_deal_field(deal, "position_id", 0) or 0),
        "entry": int(_deal_field(deal, "entry", -1) or -1),
        "symbol": str(_deal_field(deal, "symbol", fallback_symbol) or "").upper(),
        "magic": int(_deal_field(deal, "magic", 0) or 0),
        "profit": float(_deal_field(deal, "profit", 0.0) or 0.0),
        "commission": float(_deal_field(deal, "commission", 0.0) or 0.0),
        "swap": float(_deal_field(deal, "swap", 0.0) or 0.0),
        "fee": float(_deal_field(deal, "fee", 0.0) or 0.0),
        "comment": str(_deal_field(deal, "comment", "") or ""),
        "time": int(_deal_field(deal, "time", 0) or 0),
        "time_msc": int(_deal_field(deal, "time_msc", 0) or 0),
    }


def is_exit_deal(deal: dict[str, Any] | Any) -> bool:
    entry_code = int(_deal_field(deal, "entry", -1) or -1)
    exit_codes = {int(getattr(mt5, "DEAL_ENTRY_OUT", 1) or 1)}
    out_by = getattr(mt5, "DEAL_ENTRY_OUT_BY", None)
    if out_by is not None:
        exit_codes.add(int(out_by))
    return entry_code in exit_codes


def _parse_started_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def exact_logged_deals(
    exec_log_path: Path,
    *,
    symbol: str,
    live_magic: int,
    live_comment_prefix: str = "",
) -> list[dict[str, Any]]:
    if not exec_log_path.exists():
        return []
    resolved: list[dict[str, Any]] = []
    seen_tickets: set[int] = set()
    symbol = str(symbol or "").upper()
    prefix = str(live_comment_prefix or "").strip()
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
            event_symbol = str(event.get("symbol", payload.get("symbol", "")) or "").upper()
            if event_symbol and symbol and event_symbol != symbol:
                continue
            for attempt in result.get("attempts") or []:
                deal_ticket = int(_deal_field(attempt, "deal", 0) or 0)
                if deal_ticket <= 0 or deal_ticket in seen_tickets:
                    continue
                seen_tickets.add(deal_ticket)
                deals = mt5.history_deals_get(ticket=deal_ticket) or []
                if deals:
                    resolved.append(_deal_payload(deals[-1], fallback_symbol=symbol))
                    continue
                broker_fill = result.get("broker_fill")
                if not isinstance(broker_fill, dict):
                    continue
                resolved.append(_deal_payload(broker_fill, fallback_symbol=symbol))

    if not resolved:
        return []

    output: list[dict[str, Any]] = []
    for deal in resolved:
        if symbol and str(deal.get("symbol", symbol)).upper() != symbol:
            continue
        if int(deal.get("magic", 0) or 0) and int(deal.get("magic", 0) or 0) not in {0, int(live_magic)}:
            continue
        deal_comment = str(deal.get("comment", "") or "")
        if prefix and deal_comment and not deal_comment.startswith(prefix):
            continue
        output.append(deal)
    return output


def live_broker_deals(
    *,
    symbol: str,
    live_magic: int,
    started_at: Any = None,
    live_comment_prefix: str = "",
) -> list[dict[str, Any]]:
    started = _parse_started_at(started_at)
    start = started or datetime.fromtimestamp(0, tz=timezone.utc)
    end = datetime.now(timezone.utc)
    raw_deals = mt5.history_deals_get(start, end) or []
    symbol = str(symbol or "").upper()
    prefix = str(live_comment_prefix or "").strip()
    output: list[dict[str, Any]] = []
    for deal in raw_deals:
        deal_symbol = str(_deal_field(deal, "symbol", "") or "").upper()
        if symbol and deal_symbol != symbol:
            continue
        deal_magic = int(_deal_field(deal, "magic", 0) or 0)
        if int(live_magic) and deal_magic not in {0, int(live_magic)}:
            continue
        comment = str(_deal_field(deal, "comment", "") or "")
        if prefix and comment and not comment.startswith(prefix):
            continue
        output.append(_deal_payload(deal, fallback_symbol=symbol))
    return output


def is_good_session() -> bool:
    utc_hour = datetime.now(timezone.utc).hour
    return 7 <= utc_hour < 21


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent shadow runner for the snake counter-order web.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True, choices=["M1", "M5", "M15", "H1"])
    parser.add_argument("--step-pips", type=float, required=True)
    parser.add_argument("--retrace-steps", type=int, required=True)
    parser.add_argument("--hold-frontier", type=int, default=0)
    parser.add_argument("--rebase-on-flat", action="store_true")
    parser.add_argument("--max-open-per-side", type=int, default=64)
    parser.add_argument(
        "--controller-mode",
        choices=["static", "ema_ribbon", "ema_ribbon_aggressive", "ema_ribbon_hyper"],
        default="static",
    )
    parser.add_argument(
        "--portfolio-close-mode",
        choices=["none", "float_zero", "funded_rescue"],
        default="none",
    )
    parser.add_argument(
        "--hedge-mode",
        choices=["none", "same_level", "depth_threshold"],
        default="none",
    )
    parser.add_argument(
        "--hedge-trigger-depth",
        type=int,
        default=4,
        help="When hedge_mode=depth_threshold, begin opening opposite hedge tickets once same-direction core depth reaches this value.",
    )
    parser.add_argument("--variant-label", default="")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--shared-price-max-age-ms", type=int, default=0)
    parser.add_argument("--max-entry-spread-ratio", type=float, default=0.0)
    parser.add_argument(
        "--liquidity-gap-spread-multiplier",
        type=float,
        default=0.0,
        help="Optional rolling spread blowout gate. Blocks new opens only when spread ratio exceeds the recent median by this multiplier. Disabled at 0.",
    )
    parser.add_argument(
        "--liquidity-gap-spread-lookback",
        type=int,
        default=0,
        help="Rolling tick count used for the liquidity-gap spread baseline. Disabled below 4.",
    )
    parser.add_argument(
        "--liquidity-gap-spread-floor-ratio",
        type=float,
        default=0.0,
        help="Minimum spread ratio required before the liquidity-gap gate may block an open.",
    )
    parser.add_argument("--require-live-admissibility", action="store_true")
    parser.add_argument("--session-gate", action="store_true")
    parser.add_argument("--direct-live", action="store_true")
    parser.add_argument("--direct-exec-state-path", default=str(DEFAULT_DIRECT_EXEC_STATE_PATH))
    parser.add_argument("--direct-exec-log-path", default=str(DEFAULT_DIRECT_EXEC_LOG_PATH))
    parser.add_argument("--live-magic", type=int, default=live_mirror.DEFAULT_LIVE_MAGIC)
    parser.add_argument("--live-comment-prefix", default=live_mirror.DEFAULT_LIVE_COMMENT_PREFIX)
    parser.add_argument("--live-volume", type=float, default=live_mirror.DEFAULT_LIVE_VOLUME)
    parser.add_argument(
        "--block-on-prestart-open-carry",
        action="store_true",
        help="For direct-live seats, refuse to start a fresh book if broker positions already exist under the same magic.",
    )
    parser.add_argument("--min-harvest-profit-usd", type=float, default=0.0)
    parser.add_argument(
        "--positive-only-closes",
        action="store_true",
        help="Never intentionally realize a negative live close. "
        "Ordinary buffered harvests remain; net-nonnegative funded-rescue pairs are still allowed.",
    )
    return parser.parse_args()


def save_state(path: Path, state: SnakeShadowState, *, metadata: dict[str, Any], runner: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "metadata": metadata,
        "runner": runner,
        "symbols": {
            state.symbol: state.snapshot(),
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_state(path: Path, *, symbol: str) -> SnakeShadowState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    snap = (payload.get("symbols") or {}).get(symbol)
    if not isinstance(snap, dict):
        return None
    return SnakeShadowState.from_payload(snap, symbol=symbol)


def reconcile_state_with_broker(
    *,
    state: SnakeShadowState,
    event_path: Path,
    direct_exec: dict[str, Any] | None,
) -> bool:
    if direct_exec is None:
        return False
    broker_positions = live_mirror.broker_live_positions(
        symbol=state.symbol,
        live_magic=int(direct_exec["live_magic"]),
    )
    old_open_count = len(state.open_tickets)
    broker_by_ticket = {
        int(row.get("ticket") or 0): row
        for row in broker_positions
        if int(row.get("ticket") or 0) > 0
    }
    changed = False
    if not broker_by_ticket:
        if state.open_tickets:
            state.open_tickets = []
            changed = True
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "direct_live_broker_sync",
                    "symbol": state.symbol,
                    "reason": "broker_flat_cleared_stale_state",
                    "old_open_count": int(old_open_count),
                    "new_open_count": 0,
                },
            )
        return changed

    next_open_tickets: list[SnakeTicket] = []
    seen_broker_tickets: set[int] = set()
    for ticket in state.open_tickets:
        live_ticket = int(ticket.live_ticket or 0)
        broker_row = broker_by_ticket.get(live_ticket)
        if broker_row is None:
            changed = True
            continue
        seen_broker_tickets.add(live_ticket)
        next_open_tickets.append(
            SnakeTicket(
                direction=str(broker_row.get("direction") or ticket.direction).upper(),
                entry_price=float(broker_row.get("price_open") or ticket.entry_price),
                opened_time=int(broker_row.get("time") or ticket.opened_time),
                ticket_kind=str(ticket.ticket_kind or "core"),
                live_ticket=live_ticket,
                position_comment=str(broker_row.get("comment") or ticket.position_comment or ""),
                pair_id=int(ticket.pair_id or 0),
            )
        )
    for broker_ticket, broker_row in broker_by_ticket.items():
        if broker_ticket in seen_broker_tickets:
            continue
        changed = True
        next_open_tickets.append(
            SnakeTicket(
                direction=str(broker_row.get("direction") or "").upper(),
                entry_price=float(broker_row.get("price_open") or 0.0),
                opened_time=int(broker_row.get("time") or 0),
                ticket_kind="core",
                live_ticket=int(broker_ticket),
                position_comment=str(broker_row.get("comment") or ""),
                pair_id=0,
            )
        )
    if changed:
        state.open_tickets = next_open_tickets
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "direct_live_broker_sync",
                "symbol": state.symbol,
                "reason": "broker_truth_reconciled",
                "old_open_count": int(old_open_count),
                "new_open_count": int(len(state.open_tickets)),
                "broker_open_count": int(len(broker_positions)),
            },
        )
    return changed


def detect_prestart_open_carry(
    *,
    state: SnakeShadowState,
    direct_exec: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if direct_exec is None:
        return []
    if not bool(direct_exec.get("block_on_prestart_open_carry")):
        return []
    if state.open_tickets or int(state.opens or 0) > 0 or int(state.realized_closes or 0) > 0:
        return []
    return live_mirror.broker_live_positions(
        symbol=state.symbol,
        live_magic=int(direct_exec["live_magic"]),
    )


def ordered_tickets(tickets: list[SnakeTicket], direction: str) -> list[SnakeTicket]:
    side = [ticket for ticket in tickets if ticket.direction == direction]
    if direction == "SELL":
        return sorted(side, key=lambda ticket: ticket.entry_price, reverse=True)
    return sorted(side, key=lambda ticket: ticket.entry_price)


def ticket_event_payload(*, tick: dict[str, Any], ticket: SnakeTicket, pnl: float | None = None) -> dict[str, Any]:
    payload = {
        "symbol": "",
        "time": int(tick.get("time", 0) or 0),
        "time_msc": int(tick.get("time_msc", 0) or 0),
        "bid": float(tick.get("bid", 0.0) or 0.0),
        "ask": float(tick.get("ask", 0.0) or 0.0),
        "last": float(tick.get("last", 0.0) or 0.0),
        "direction": ticket.direction,
        "entry_price": round(float(ticket.entry_price), 8),
        "opened_time": int(ticket.opened_time),
    }
    if pnl is not None:
        payload["realized_pnl"] = round(float(pnl), 6)
    return payload


def current_mid(tick: dict[str, Any], *, fallback: float = 0.0) -> float:
    bid = float(tick.get("bid", 0.0) or 0.0)
    ask = float(tick.get("ask", 0.0) or 0.0)
    last = float(tick.get("last", 0.0) or 0.0)
    if bid > 0.0 and ask > 0.0:
        return (bid + ask) / 2.0
    if last > 0.0:
        return last
    return float(fallback)


def compute_spread_ratio(*, tick: dict[str, Any], step_px: float) -> float | None:
    if step_px <= 0.0:
        return None
    bid = float(tick.get("bid", 0.0) or 0.0)
    ask = float(tick.get("ask", 0.0) or 0.0)
    if bid <= 0.0 or ask <= 0.0:
        return None
    spread_px = max(0.0, ask - bid)
    return float(spread_px / step_px)


def liquidity_gap_threshold_ratio(
    *,
    spread_ratio_history: list[float],
    liquidity_gap_spread_multiplier: float,
    liquidity_gap_spread_lookback: int,
    liquidity_gap_spread_floor_ratio: float,
) -> tuple[float | None, float | None]:
    if liquidity_gap_spread_multiplier <= 0.0 or liquidity_gap_spread_lookback < 4:
        return None, None
    if len(spread_ratio_history) < min(int(liquidity_gap_spread_lookback), 4):
        return None, None
    sample = list(spread_ratio_history)[-int(liquidity_gap_spread_lookback) :]
    baseline_ratio = float(median(sample))
    threshold_ratio = max(
        float(liquidity_gap_spread_floor_ratio or 0.0),
        baseline_ratio * float(liquidity_gap_spread_multiplier),
    )
    return baseline_ratio, threshold_ratio


def entry_spread_allows(
    *,
    tick: dict[str, Any],
    step_px: float,
    max_entry_spread_ratio: float,
    spread_ratio_history: list[float],
    liquidity_gap_spread_multiplier: float,
    liquidity_gap_spread_lookback: int,
    liquidity_gap_spread_floor_ratio: float,
) -> tuple[bool, float, float, str, float | None, float | None]:
    bid = float(tick.get("bid", 0.0) or 0.0)
    ask = float(tick.get("ask", 0.0) or 0.0)
    spread_px = max(0.0, ask - bid) if bid > 0.0 and ask > 0.0 else 0.0
    spread_ratio = compute_spread_ratio(tick=tick, step_px=float(step_px))
    if spread_ratio is None:
        return True, spread_px, 0.0, "", None, None
    fixed_threshold = float(max_entry_spread_ratio or 0.0)
    baseline_ratio, gap_threshold_ratio = liquidity_gap_threshold_ratio(
        spread_ratio_history=list(spread_ratio_history),
        liquidity_gap_spread_multiplier=float(liquidity_gap_spread_multiplier or 0.0),
        liquidity_gap_spread_lookback=int(liquidity_gap_spread_lookback or 0),
        liquidity_gap_spread_floor_ratio=float(liquidity_gap_spread_floor_ratio or 0.0),
    )
    block_mode = ""
    applied_threshold_ratio: float | None = None
    allows = True
    if fixed_threshold > 0.0 and spread_ratio > fixed_threshold:
        allows = False
        block_mode = "fixed_ratio"
        applied_threshold_ratio = fixed_threshold
    elif gap_threshold_ratio is not None and spread_ratio > gap_threshold_ratio:
        allows = False
        block_mode = "liquidity_gap"
        applied_threshold_ratio = gap_threshold_ratio
    return allows, spread_px, float(spread_ratio), block_mode, baseline_ratio, applied_threshold_ratio


def projected_close_pnl_usd(
    *,
    state: SnakeShadowState,
    ticket: SnakeTicket,
    symbol_info: Any,
    mid_price: float,
    spread_px: float,
    tick: dict[str, Any],
    direct_exec: dict[str, Any] | None,
) -> float:
    exit_price = float(mid_price)
    projected_spread_px = float(spread_px)
    if direct_exec is not None:
        bid = float(tick.get("bid", 0.0) or 0.0)
        ask = float(tick.get("ask", 0.0) or 0.0)
        if str(ticket.direction).upper() == "BUY" and bid > 0.0:
            exit_price = bid
            projected_spread_px = 0.0
        elif str(ticket.direction).upper() == "SELL" and ask > 0.0:
            exit_price = ask
            projected_spread_px = 0.0
    return research_unit_pnl_usd(
        state.symbol,
        ticket.direction,
        float(ticket.entry_price),
        float(exit_price),
        float(projected_spread_px),
        symbol_info,
    )


def sync_state_to_broker_positions(
    *,
    state: SnakeShadowState,
    event_path: Path,
    live_magic: int,
    direct_exec: dict[str, Any] | None = None,
) -> bool:
    max_open_per_side = int((direct_exec or {}).get("max_open_per_side", 0) or 0) if direct_exec else 0
    current_by_live_ticket = {
        int(ticket.live_ticket): ticket
        for ticket in list(state.open_tickets)
        if int(ticket.live_ticket or 0) > 0
    }
    broker_positions = live_mirror.broker_live_positions(symbol=state.symbol, live_magic=live_magic)
    aligned: list[SnakeTicket] = []
    for row in broker_positions:
        live_ticket = int(row.get("ticket", 0) or 0)
        existing = current_by_live_ticket.get(live_ticket)
        aligned.append(
            SnakeTicket(
                direction=str(row.get("direction", existing.direction if existing else "") or "").upper(),
                entry_price=float(row.get("price_open", existing.entry_price if existing else 0.0) or 0.0),
                opened_time=int(existing.opened_time if existing else int(row.get("time", 0) or 0)),
                ticket_kind=str(existing.ticket_kind if existing else "core"),
                live_ticket=live_ticket,
                position_comment=str(row.get("comment", existing.position_comment if existing else "") or ""),
                pair_id=int(existing.pair_id if existing else 0),
            )
        )
    old_tickets = list(state.open_tickets)
    old_ticket_ids = [int(ticket.live_ticket or 0) for ticket in old_tickets]
    new_ticket_ids = [int(ticket.live_ticket or 0) for ticket in aligned]
    open_sell = sum(1 for ticket in aligned if ticket.direction == "SELL")
    open_buy = sum(1 for ticket in aligned if ticket.direction == "BUY")
    open_total = open_sell + open_buy
    tracked_deals: list[dict[str, Any]] = []
    if direct_exec is not None:
        log_path = direct_exec.get("log_path")
        if log_path is not None:
            tracked_deals = exact_logged_deals(
                Path(log_path),
                symbol=state.symbol,
                live_magic=int(direct_exec["live_magic"]),
                live_comment_prefix=str(direct_exec.get("live_comment_prefix", "")),
            )
        if not tracked_deals:
            tracked_deals = live_broker_deals(
                symbol=state.symbol,
                live_magic=int(direct_exec["live_magic"]),
                started_at=direct_exec.get("started_at"),
                live_comment_prefix=str(direct_exec.get("live_comment_prefix", "")),
            )
    realized_deals = [deal for deal in tracked_deals if is_exit_deal(deal)]
    realized_net_usd = sum(deal_net_usd(deal) for deal in realized_deals)
    realized_closes = len(realized_deals)
    wins = sum(1 for deal in realized_deals if deal_net_usd(deal) > 0.0)
    gross_positive_booked_usd = sum(max(0.0, deal_net_usd(deal)) for deal in realized_deals)
    old_realized_net_usd = float(state.realized_net_usd)
    old_gross_positive_booked_usd = float(state.gross_positive_booked_usd)
    old_realized_closes = int(state.realized_closes)
    old_wins = int(state.wins)
    old_max_open_total = int(state.max_open_total)
    old_max_open_sell = int(state.max_open_sell)
    old_max_open_buy = int(state.max_open_buy)
    desired_max_open_total = max(
        old_max_open_total,
        open_total,
        max_open_per_side * 2,
    )
    desired_max_open_sell = max(old_max_open_sell, open_sell)
    desired_max_open_buy = max(old_max_open_buy, open_buy)
    changed = (
        old_ticket_ids != new_ticket_ids
        or len(old_tickets) != len(aligned)
        or direct_exec is not None
        and (
            abs(old_realized_net_usd - realized_net_usd) > 1e-9
            or abs(old_gross_positive_booked_usd - gross_positive_booked_usd) > 1e-9
            or old_realized_closes != int(realized_closes)
            or old_wins != int(wins)
            or old_max_open_total != desired_max_open_total
            or old_max_open_sell != desired_max_open_sell
            or old_max_open_buy != desired_max_open_buy
        )
    )
    if changed:
        state.open_tickets = aligned
        if direct_exec is not None:
            state.realized_net_usd = float(realized_net_usd)
            state.gross_positive_booked_usd = float(gross_positive_booked_usd)
            state.realized_closes = int(realized_closes)
            state.wins = int(wins)
            state.max_open_total = int(desired_max_open_total)
            state.max_open_sell = int(desired_max_open_sell)
            state.max_open_buy = int(desired_max_open_buy)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "direct_live_broker_sync",
                "symbol": state.symbol,
                "old_open_count": len(old_tickets),
                "new_open_count": len(aligned),
                "old_realized_net_usd": old_realized_net_usd,
                "new_realized_net_usd": float(realized_net_usd),
                "old_gross_positive_booked_usd": old_gross_positive_booked_usd,
                "new_gross_positive_booked_usd": float(gross_positive_booked_usd),
                "old_realized_closes": old_realized_closes,
                "new_realized_closes": int(realized_closes),
                "old_wins": old_wins,
                "new_wins": int(wins),
                "old_max_open_total": old_max_open_total,
                "new_max_open_total": int(desired_max_open_total),
                "old_max_open_sell": old_max_open_sell,
                "new_max_open_sell": int(desired_max_open_sell),
                "old_max_open_buy": old_max_open_buy,
                "new_max_open_buy": int(desired_max_open_buy),
                "dropped_live_tickets": [ticket_id for ticket_id in old_ticket_ids if ticket_id and ticket_id not in set(new_ticket_ids)],
                "rehydrated_live_tickets": [ticket_id for ticket_id in new_ticket_ids if ticket_id and ticket_id not in set(old_ticket_ids)],
            },
        )
    return changed


def same_direction_core_depth(tickets: list[SnakeTicket], direction: str) -> int:
    return sum(1 for ticket in tickets if ticket.direction == direction and ticket.ticket_kind == "core")


def compute_dynamic_context(symbol: str, timeframe: str, controller_mode: str) -> dict[str, Any] | None:
    if controller_mode == "static":
        return None
    bars = load_recent_bars(symbol, timeframe, 600)
    if not bars:
        return None
    ema_rows = compute_ema_ladders(bars, [3, 12, 24, 64, 128, 500])
    latest = ema_rows[-1] if ema_rows else {}
    close_px = float(bars[-1]["close"])
    return {
        "ema_fast_3": float(latest.get(3, close_px) or close_px),
        "ema_light_12": float(latest.get(12, close_px) or close_px),
        "ema_mid_64": float(latest.get(64, close_px) or close_px),
        "ema_mid_128": float(latest.get(128, close_px) or close_px),
        "ema_slow_500": float(latest.get(500, close_px) or close_px),
    }


def append_live_ticket(
    *,
    state: SnakeShadowState,
    direction: str,
    entry_price: float,
    tick: dict[str, Any],
    event_path: Path,
    direct_exec: dict[str, Any] | None,
    level: int,
    ticket_kind: str = "core",
    open_action: str = "open_ticket",
) -> None:
    live_result: dict[str, Any] | None = None
    if direct_exec is not None:
        action_label = open_action
        if action_label == "open_ticket":
            action_label = "open_buy" if direction == "BUY" else "open_sell"
        elif action_label == "open_hedge_ticket":
            action_label = "open_hedge_buy" if direction == "BUY" else "open_hedge_sell"
        comment = live_mirror.short_live_comment(action_label, comment_prefix=str(direct_exec["live_comment_prefix"]))
        live_result = live_mirror.send_market_order(
            state.symbol,
            direction,
            float(direct_exec["live_volume"]),
            comment,
            live_magic=int(direct_exec["live_magic"]),
        )
        if not bool(live_result.get("ok")):
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "open_ticket_live_failed",
                    "symbol": state.symbol,
                    "direction": direction,
                    "ticket_kind": ticket_kind,
                    "level": int(level),
                    "entry_price": round(float(entry_price), 8),
                    "live_result": live_result,
                },
            )
            return False
    ticket = SnakeTicket(
        direction=direction,
        entry_price=float((live_result or {}).get("broker_position_price_open", entry_price) or entry_price),
        opened_time=int(tick.get("time", 0) or 0),
        ticket_kind=ticket_kind,
        live_ticket=int((live_result or {}).get("ticket", 0) or 0),
        position_comment=str((live_result or {}).get("position_comment", "") or ""),
    )
    state.open_tickets.append(ticket)
    state.opens += 1
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": open_action,
            "symbol": state.symbol,
            "direction": direction,
            "ticket_kind": ticket_kind,
            "level": int(level),
            "entry_price": round(float(entry_price), 8),
            "open_count": len(state.open_tickets),
            "time_msc": int(tick.get("time_msc", 0) or 0),
            "bid": float(tick.get("bid", 0.0) or 0.0),
            "ask": float(tick.get("ask", 0.0) or 0.0),
        },
    )
    return True


def maybe_add_hedge_ticket(
    *,
    state: SnakeShadowState,
    contract: SnakeContract,
    level_direction: str,
    entry_price: float,
    tick: dict[str, Any],
    event_path: Path,
    level: int,
    direct_exec: dict[str, Any] | None,
) -> None:
    if contract.hedge_mode not in {"same_level", "depth_threshold"}:
        return
    opposite_direction = "BUY" if level_direction == "SELL" else "SELL"
    opposite_count = sum(1 for ticket in state.open_tickets if ticket.direction == opposite_direction)
    if opposite_count >= int(contract.max_open_per_side):
        return
    if contract.hedge_mode == "depth_threshold":
        if same_direction_core_depth(state.open_tickets, level_direction) < int(contract.hedge_trigger_depth):
            return
    append_live_ticket(
        state=state,
        direction=opposite_direction,
        entry_price=entry_price,
        tick=tick,
        event_path=event_path,
        direct_exec=direct_exec,
        level=level,
        ticket_kind="hedge",
        open_action="open_hedge_ticket",
    )


def maybe_open_ticket(
    *,
    state: SnakeShadowState,
    contract: SnakeContract,
    direction: str,
    level: int,
    entry_price: float,
    tick: dict[str, Any],
    event_path: Path,
    spread_px: float,
    step_px: float,
    divisor: int,
    max_entry_spread_ratio: float,
    spread_ratio_history: list[float],
    liquidity_gap_spread_multiplier: float,
    liquidity_gap_spread_lookback: int,
    liquidity_gap_spread_floor_ratio: float,
    direct_exec: dict[str, Any] | None,
) -> None:
    side_count = sum(1 for ticket in state.open_tickets if ticket.direction == direction)
    if side_count >= int(contract.max_open_per_side):
        return
    if divisor > 1 and level % divisor != 0:
        return
    allows, current_spread_px, spread_ratio, spread_block_mode, liquidity_gap_baseline_ratio, applied_threshold_ratio = entry_spread_allows(
        tick=tick,
        step_px=float(step_px),
        max_entry_spread_ratio=float(max_entry_spread_ratio or 0.0),
        spread_ratio_history=list(spread_ratio_history),
        liquidity_gap_spread_multiplier=float(liquidity_gap_spread_multiplier or 0.0),
        liquidity_gap_spread_lookback=int(liquidity_gap_spread_lookback or 0),
        liquidity_gap_spread_floor_ratio=float(liquidity_gap_spread_floor_ratio or 0.0),
    )
    spread_ratio_history.append(float(spread_ratio))
    if not allows:
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "open_blocked_wide_spread",
                "symbol": state.symbol,
                "direction": direction,
                "level": int(level),
                "spread_px": round(float(current_spread_px), 8),
                "step_px": round(step_px, 8),
                "max_entry_spread_ratio": float(max_entry_spread_ratio),
                "spread_to_step_ratio": round(float(spread_ratio), 6),
                "spread_block_mode": str(spread_block_mode or ""),
                "liquidity_gap_baseline_ratio": None
                if liquidity_gap_baseline_ratio is None
                else round(float(liquidity_gap_baseline_ratio), 6),
                "liquidity_gap_threshold_ratio": None
                if applied_threshold_ratio is None
                else round(float(applied_threshold_ratio), 6),
                "liquidity_gap_spread_multiplier": round(float(liquidity_gap_spread_multiplier or 0.0), 6),
                "time_msc": int(tick.get("time_msc", 0) or 0),
                "bid": float(tick.get("bid", 0.0) or 0.0),
                "ask": float(tick.get("ask", 0.0) or 0.0),
            },
        )
        return
    opened = append_live_ticket(
        state=state,
        direction=direction,
        entry_price=entry_price,
        tick=tick,
        event_path=event_path,
        direct_exec=direct_exec,
        level=level,
        ticket_kind="core",
        open_action="open_ticket",
    )
    if not opened:
        return
    maybe_add_hedge_ticket(
        state=state,
        contract=contract,
        level_direction=direction,
        entry_price=entry_price,
        tick=tick,
        event_path=event_path,
        level=level,
        direct_exec=direct_exec,
    )


def close_ticket(
    *,
    state: SnakeShadowState,
    ticket: SnakeTicket,
    pnl: float,
    tick: dict[str, Any],
    event_path: Path,
    action: str,
    direct_exec: dict[str, Any] | None,
) -> None:
    if ticket not in state.open_tickets:
        return
    broker_result: dict[str, Any] | None = None
    realized_pnl = float(pnl)
    if direct_exec is not None and int(ticket.live_ticket or 0) > 0:
        broker_result = live_mirror.close_live_position(
            int(ticket.live_ticket),
            live_magic=int(direct_exec["live_magic"]),
            comment_prefix=str(direct_exec["live_comment_prefix"]),
        )
        if not bool((broker_result or {}).get("ok")):
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "close_ticket_live_failed",
                    "symbol": state.symbol,
                    **ticket_event_payload(tick=tick, ticket=ticket, pnl=pnl),
                    "broker_result": broker_result,
                },
            )
            return
        realized_pnl = deal_net_usd((broker_result or {}).get("broker_fill"))
    state.open_tickets.remove(ticket)
    state.realized_net_usd += float(realized_pnl)
    state.gross_positive_booked_usd += float(max(0.0, realized_pnl))
    state.realized_closes += 1
    if realized_pnl > 0.0:
        state.wins += 1
    if action == "close_at_float_zero":
        state.float_zero_closes += 1
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": action,
            "symbol": state.symbol,
            **ticket_event_payload(tick=tick, ticket=ticket, pnl=realized_pnl),
            "open_count": len(state.open_tickets),
            "broker_result": broker_result,
        },
    )


def apply_closes(
    *,
    state: SnakeShadowState,
    contract: SnakeContract,
    symbol_info: Any,
    price: float,
    spread_px: float,
    tick: dict[str, Any],
    event_path: Path,
    direct_exec: dict[str, Any] | None,
) -> None:
    for direction in ("SELL", "BUY"):
        ordered = ordered_tickets(state.open_tickets, direction)
        profitable: list[tuple[SnakeTicket, float]] = []
        for ticket in ordered:
            pnl = projected_close_pnl_usd(
                state=state,
                ticket=ticket,
                symbol_info=symbol_info,
                mid_price=float(price),
                spread_px=float(spread_px),
                tick=tick,
                direct_exec=direct_exec,
            )
            if pnl <= 0.0:
                continue
            if direct_exec is not None and float(pnl) < float(contract.min_harvest_profit_usd):
                continue
            if direction == "SELL":
                close_threshold = float(ticket.entry_price) - (float(contract.step_px) * float(contract.retrace_steps))
                if float(price) > close_threshold:
                    continue
            else:
                close_threshold = float(ticket.entry_price) + (float(contract.step_px) * float(contract.retrace_steps))
                if float(price) < close_threshold:
                    continue
            profitable.append((ticket, pnl))
        if not profitable:
            continue
        to_close = profitable[int(contract.hold_frontier) :] if int(contract.hold_frontier) > 0 else profitable
        for ticket, pnl in to_close:
            close_ticket(
                state=state,
                ticket=ticket,
                pnl=pnl,
                tick=tick,
                event_path=event_path,
                action="close_ticket",
                direct_exec=direct_exec,
            )

    if contract.portfolio_close_mode == "funded_rescue" and state.open_tickets:
        rows: list[tuple[SnakeTicket, float]] = []
        for ticket in list(state.open_tickets):
            pnl = projected_close_pnl_usd(
                state=state,
                ticket=ticket,
                symbol_info=symbol_info,
                mid_price=float(price),
                spread_px=float(spread_px),
                tick=tick,
                direct_exec=direct_exec,
            )
            rows.append((ticket, pnl))
        rows.sort(key=lambda item: item[1], reverse=True)
        while rows and len(rows) >= 2:
            best_ticket, best_pnl = rows[0]
            worst_ticket, worst_pnl = rows[-1]
            if best_pnl > 0.0 and worst_pnl < 0.0 and (best_pnl + worst_pnl) >= 0.0:
                close_ticket(
                    state=state,
                    ticket=best_ticket,
                    pnl=best_pnl,
                    tick=tick,
                    event_path=event_path,
                    action="close_ticket",
                    direct_exec=direct_exec,
                )
                close_ticket(
                    state=state,
                    ticket=worst_ticket,
                    pnl=worst_pnl,
                    tick=tick,
                    event_path=event_path,
                    action="close_ticket_funded_rescue",
                    direct_exec=direct_exec,
                )
                rows.pop(0)
                rows.pop(-1)
            else:
                break
        return

    if contract.portfolio_close_mode != "float_zero" or not state.open_tickets:
        return
    floating_rows: list[tuple[SnakeTicket, float]] = []
    total_floating = 0.0
    for ticket in list(state.open_tickets):
        pnl = projected_close_pnl_usd(
            state=state,
            ticket=ticket,
            symbol_info=symbol_info,
            mid_price=float(price),
            spread_px=float(spread_px),
            tick=tick,
            direct_exec=direct_exec,
        )
        floating_rows.append((ticket, pnl))
        total_floating += pnl
    if total_floating < 0.0:
        return
    for ticket, pnl in floating_rows:
        if pnl <= 0.0:
            continue
        if direct_exec is not None and float(pnl) < float(contract.min_harvest_profit_usd):
            continue
        close_ticket(
            state=state,
            ticket=ticket,
            pnl=pnl,
            tick=tick,
            event_path=event_path,
            action="close_at_float_zero",
            direct_exec=direct_exec,
        )


def update_open_burden(state: SnakeShadowState) -> None:
    open_sell = sum(1 for ticket in state.open_tickets if ticket.direction == "SELL")
    open_buy = sum(1 for ticket in state.open_tickets if ticket.direction == "BUY")
    open_total = open_sell + open_buy
    state.max_open_total = max(int(state.max_open_total), int(open_total))
    state.max_open_sell = max(int(state.max_open_sell), int(open_sell))
    state.max_open_buy = max(int(state.max_open_buy), int(open_buy))


def process_tick(
    *,
    state: SnakeShadowState,
    contract: SnakeContract,
    symbol_info: Any,
    tick: dict[str, Any],
    dynamic_context: dict[str, Any] | None,
    pip_px: float,
    event_path: Path,
    max_entry_spread_ratio: float,
    spread_ratio_history: list[float],
    liquidity_gap_spread_multiplier: float,
    liquidity_gap_spread_lookback: int,
    liquidity_gap_spread_floor_ratio: float,
    direct_exec: dict[str, Any] | None,
) -> None:
    bid = float(tick.get("bid", 0.0) or 0.0)
    ask = float(tick.get("ask", 0.0) or 0.0)
    spread_px = max(0.0, ask - bid) if bid > 0.0 and ask > 0.0 else 0.0
    mid = current_mid(tick, fallback=state.last_price or state.anchor)
    if mid <= 0.0:
        return
    if state.anchor <= 0.0:
        state.anchor = float(mid)
        state.last_price = float(mid)
        state.last_tick_msc = int(tick.get("time_msc", 0) or 0)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "anchor_initialized",
                "symbol": state.symbol,
                "anchor": round(state.anchor, 8),
                "time_msc": state.last_tick_msc,
            },
        )
        return
    prev_price = float(state.last_price or state.anchor)
    active_step, sell_divisor, buy_divisor, rebase_allowed = _resolve_controller_state(
        contract,
        dynamic_context,
        pip_px=float(pip_px),
    )
    for level in _cross_up_levels(state.anchor, prev_price, mid, active_step, state.high_level):
        maybe_open_ticket(
            state=state,
            contract=contract,
            direction="SELL",
            level=level,
            entry_price=state.anchor + (level * active_step),
            tick=tick,
            event_path=event_path,
            spread_px=spread_px,
            step_px=active_step,
            divisor=sell_divisor,
            max_entry_spread_ratio=max_entry_spread_ratio,
            spread_ratio_history=spread_ratio_history,
            liquidity_gap_spread_multiplier=liquidity_gap_spread_multiplier,
            liquidity_gap_spread_lookback=liquidity_gap_spread_lookback,
            liquidity_gap_spread_floor_ratio=liquidity_gap_spread_floor_ratio,
            direct_exec=direct_exec,
        )
        state.high_level = max(int(state.high_level), int(level))
    for level in _cross_down_levels(state.anchor, prev_price, mid, active_step, state.low_level):
        maybe_open_ticket(
            state=state,
            contract=contract,
            direction="BUY",
            level=level,
            entry_price=state.anchor - (level * active_step),
            tick=tick,
            event_path=event_path,
            spread_px=spread_px,
            step_px=active_step,
            divisor=buy_divisor,
            max_entry_spread_ratio=max_entry_spread_ratio,
            spread_ratio_history=spread_ratio_history,
            liquidity_gap_spread_multiplier=liquidity_gap_spread_multiplier,
            liquidity_gap_spread_lookback=liquidity_gap_spread_lookback,
            liquidity_gap_spread_floor_ratio=liquidity_gap_spread_floor_ratio,
            direct_exec=direct_exec,
        )
        state.low_level = max(int(state.low_level), int(level))
    apply_closes(
        state=state,
        contract=contract,
        symbol_info=symbol_info,
        price=float(mid),
        spread_px=spread_px,
        tick=tick,
        event_path=event_path,
        direct_exec=direct_exec,
    )
    if rebase_allowed and not state.open_tickets:
        old_anchor = state.anchor
        state.anchor = float(mid)
        state.high_level = 0
        state.low_level = 0
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "anchor_rebase",
                "symbol": state.symbol,
                "old_anchor": round(old_anchor, 8),
                "new_anchor": round(state.anchor, 8),
                "time_msc": int(tick.get("time_msc", 0) or 0),
            },
        )
    state.last_price = float(mid)
    state.last_tick_msc = int(tick.get("time_msc", 0) or 0)
    update_open_burden(state)


def run_once(
    *,
    state: SnakeShadowState,
    contract: SnakeContract,
    symbol_info: Any,
    state_path: Path,
    event_path: Path,
    metadata: dict[str, Any],
    runner: dict[str, Any],
    shared_price_max_age_ms: int,
    session_gate: bool,
    max_entry_spread_ratio: float,
    liquidity_gap_spread_multiplier: float,
    liquidity_gap_spread_lookback: int,
    liquidity_gap_spread_floor_ratio: float,
    require_live_admissibility: bool,
    direct_exec: dict[str, Any] | None,
) -> None:
    if direct_exec is not None:
        prestart_carry = detect_prestart_open_carry(state=state, direct_exec=direct_exec)
        if prestart_carry:
            runner["status"] = "pre_start_open_carry_blocked"
            runner["heartbeat_at"] = utc_now_iso()
            runner["last_successful_run_at"] = runner["heartbeat_at"]
            runner["pre_start_open_carry_count"] = int(len(prestart_carry))
            if int(runner.get("pre_start_open_carry_logged_count") or 0) != int(len(prestart_carry)):
                runner["pre_start_open_carry_logged_count"] = int(len(prestart_carry))
                append_jsonl(
                    event_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "pre_start_open_carry_blocked",
                        "symbol": state.symbol,
                        "carry_open_count": int(len(prestart_carry)),
                        "live_magic": int(direct_exec["live_magic"]),
                        "tickets": [int(row.get("ticket", 0) or 0) for row in prestart_carry],
                    },
                )
            save_state(state_path, state, metadata=metadata, runner=runner)
            return
    if direct_exec is not None:
        reconcile_state_with_broker(
            state=state,
            event_path=event_path,
            direct_exec=direct_exec,
        )
    if session_gate and not is_good_session():
        runner["heartbeat_at"] = utc_now_iso()
        runner["last_successful_run_at"] = runner["heartbeat_at"]
        runner["session_gated"] = True
        save_state(state_path, state, metadata=metadata, runner=runner)
        return
    runner["session_gated"] = False
    if direct_exec is not None:
        sync_state_to_broker_positions(
            state=state,
            event_path=event_path,
            live_magic=int(direct_exec["live_magic"]),
            direct_exec={
                "live_magic": int(direct_exec["live_magic"]),
                "log_path": direct_exec.get("log_path"),
                "live_comment_prefix": str(direct_exec.get("live_comment_prefix", "")),
                "max_open_per_side": int(contract.max_open_per_side),
                "started_at": runner.get("started_at"),
            },
        )
    ticks, ticks_source = load_ticks_since_with_source(
        state.symbol,
        int(state.last_tick_msc or 0),
        lookback_seconds=max(120, timeframe_seconds(contract.timeframe) * 3),
        shared_price_max_age_ms=shared_price_max_age_ms,
    )
    live_tick, live_tick_source = load_latest_tick(state.symbol, shared_price_max_age_ms=shared_price_max_age_ms)
    if live_tick is not None:
        latest_loaded_msc = int(ticks[-1]["time_msc"]) if ticks else int(state.last_tick_msc or 0)
        live_tick_msc = int(live_tick.get("time_msc", 0) or 0)
        if live_tick_msc > latest_loaded_msc:
            ticks.append(live_tick)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "tick_history_fallback",
                    "symbol": state.symbol,
                    "tick_history_source": ticks_source,
                    "latest_tick_source": live_tick_source,
                    "live_tick_msc": live_tick_msc,
                    "latest_loaded_msc": latest_loaded_msc,
                },
            )
    spread_ratio_history: list[float] = []
    baseline_ticks = list(ticks[:-1]) if ticks else []
    if float(contract.step_px) > 0.0:
        for historical_tick in baseline_ticks:
            if not isinstance(historical_tick, dict):
                continue
            historical_ratio = compute_spread_ratio(tick=historical_tick, step_px=float(contract.step_px))
            if historical_ratio is None:
                continue
            spread_ratio_history.append(float(historical_ratio))
    if direct_exec is not None and require_live_admissibility and (
        max_entry_spread_ratio > 0.0 or (liquidity_gap_spread_multiplier > 0.0 and liquidity_gap_spread_lookback >= 4)
    ):
        admissibility_tick = live_tick or (ticks[-1] if ticks else None)
        if isinstance(admissibility_tick, dict):
            allows, _spread_px, spread_ratio, spread_block_mode, liquidity_gap_baseline_ratio, applied_threshold_ratio = entry_spread_allows(
                tick=admissibility_tick,
                step_px=float(contract.step_px),
                max_entry_spread_ratio=float(max_entry_spread_ratio or 0.0),
                spread_ratio_history=spread_ratio_history,
                liquidity_gap_spread_multiplier=float(liquidity_gap_spread_multiplier or 0.0),
                liquidity_gap_spread_lookback=int(liquidity_gap_spread_lookback or 0),
                liquidity_gap_spread_floor_ratio=float(liquidity_gap_spread_floor_ratio or 0.0),
            )
            if not allows:
                reason = "live_contract_friction_invalid"
                tick_msc = int(admissibility_tick.get("time_msc", 0) or 0)
                runner["status"] = reason
                runner["live_admissibility_reason"] = reason
                runner["live_admissibility_step_px"] = round(float(contract.step_px), 8)
                runner["live_admissibility_spread_to_step_ratio"] = round(float(spread_ratio), 6)
                runner["live_admissibility_max_entry_spread_ratio"] = float(max_entry_spread_ratio)
                runner["live_admissibility_spread_block_mode"] = str(spread_block_mode or "")
                runner["live_admissibility_liquidity_gap_baseline_ratio"] = (
                    None if liquidity_gap_baseline_ratio is None else round(float(liquidity_gap_baseline_ratio), 6)
                )
                runner["live_admissibility_liquidity_gap_threshold_ratio"] = (
                    None if applied_threshold_ratio is None else round(float(applied_threshold_ratio), 6)
                )
                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                if int(runner.get("live_admissibility_block_tick_msc") or 0) != tick_msc:
                    runner["live_admissibility_block_tick_msc"] = tick_msc
                    append_jsonl(
                        event_path,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": reason,
                            "symbol": state.symbol,
                            "time_msc": tick_msc,
                            "bid": float(admissibility_tick.get("bid", 0.0) or 0.0),
                            "ask": float(admissibility_tick.get("ask", 0.0) or 0.0),
                            "step_px": round(float(contract.step_px), 8),
                            "spread_to_step_ratio": round(float(spread_ratio), 6),
                            "max_entry_spread_ratio": float(max_entry_spread_ratio),
                            "spread_block_mode": str(spread_block_mode or ""),
                            "liquidity_gap_baseline_ratio": None
                            if liquidity_gap_baseline_ratio is None
                            else round(float(liquidity_gap_baseline_ratio), 6),
                            "liquidity_gap_threshold_ratio": None
                            if applied_threshold_ratio is None
                            else round(float(applied_threshold_ratio), 6),
                            "liquidity_gap_spread_multiplier": round(float(liquidity_gap_spread_multiplier or 0.0), 6),
                        },
                    )
                save_state(state_path, state, metadata=metadata, runner=runner)
                return
    if direct_exec is not None and ticks:
        # Live seats must not replay stale historical ticks into fresh market orders.
        # On cold start or after a lag, collapse the backlog to the latest tick snapshot.
        ticks = [ticks[-1]]
    dynamic_context = compute_dynamic_context(state.symbol, contract.timeframe, contract.controller_mode)
    pip_px = float(pip_size_for(symbol_info) or 0.0)
    for index, tick in enumerate(ticks, start=1):
        if not isinstance(tick, dict):
            continue
        process_tick(
            state=state,
            contract=contract,
            symbol_info=symbol_info,
            tick=tick,
            dynamic_context=dynamic_context,
            pip_px=pip_px,
            event_path=event_path,
            max_entry_spread_ratio=max_entry_spread_ratio,
            spread_ratio_history=spread_ratio_history,
            liquidity_gap_spread_multiplier=liquidity_gap_spread_multiplier,
            liquidity_gap_spread_lookback=liquidity_gap_spread_lookback,
            liquidity_gap_spread_floor_ratio=liquidity_gap_spread_floor_ratio,
            direct_exec=direct_exec,
        )
        if index % CHECKPOINT_TICK_INTERVAL == 0:
            runner["heartbeat_at"] = utc_now_iso()
            runner["last_successful_run_at"] = runner["heartbeat_at"]
            runner["tick_history_source_last"] = str(ticks_source or "")
            runner["latest_tick_source_last"] = str(live_tick_source or "")
            save_state(state_path, state, metadata=metadata, runner=runner)
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    runner["tick_history_source_last"] = str(ticks_source or "")
    runner["latest_tick_source_last"] = str(live_tick_source or "")
    save_state(state_path, state, metadata=metadata, runner=runner)


def build_contract(args: argparse.Namespace, *, pip_px: float) -> SnakeContract:
    step_px = float(args.step_pips) * float(pip_px)
    label = str(args.variant_label or "").strip()
    if not label:
        label = (
            f"snake_step{args.step_pips:g}pip_retrace{int(args.retrace_steps)}"
            f"_hold{int(args.hold_frontier)}_{str(args.controller_mode)}"
            f"_{str(args.portfolio_close_mode)}"
            f"_hedge{str(args.hedge_mode)}"
            f"_cap{int(args.max_open_per_side)}_{'rebase' if bool(args.rebase_on_flat) else 'fixed'}"
        )
    return SnakeContract(
        symbol=str(args.symbol).upper(),
        timeframe=str(args.timeframe).upper(),
        step_px=step_px,
        retrace_steps=int(args.retrace_steps),
        hold_frontier=int(args.hold_frontier),
        rebase_on_flat=bool(args.rebase_on_flat),
        max_open_per_side=int(args.max_open_per_side),
        controller_mode=str(args.controller_mode),
        portfolio_close_mode=str(args.portfolio_close_mode),
        hedge_mode=str(args.hedge_mode),
        hedge_trigger_depth=int(args.hedge_trigger_depth),
        hedge_profit_threshold_steps=0,
        variant_label=label,
        min_harvest_profit_usd=float(args.min_harvest_profit_usd),
        positive_only_closes=bool(getattr(args, "positive_only_closes", False)),
    )


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
        symbol = str(args.symbol).upper()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Missing symbol info for {symbol}")
        pip_px = float(pip_size_for(info) or 0.0)
        if pip_px <= 0.0:
            raise RuntimeError(f"Invalid pip size for {symbol}")
        contract = build_contract(args, pip_px=pip_px)
        state_path = Path(args.state_path)
        event_path = Path(args.event_path)
        if bool(args.fresh_start):
            state = SnakeShadowState(symbol=symbol)
        else:
            state = load_state(state_path, symbol=symbol) or SnakeShadowState(symbol=symbol)
        metadata = {
            "engine_family": "snake_counter_web_shadow",
            "symbol": symbol,
            "timeframe": contract.timeframe,
            "variant_label": contract.variant_label,
            "step_pips": float(args.step_pips),
            "step_px": round(float(contract.step_px), 8),
            "retrace_steps": int(contract.retrace_steps),
            "hold_frontier": int(contract.hold_frontier),
            "rebase_on_flat": bool(contract.rebase_on_flat),
            "controller_mode": str(contract.controller_mode),
            "portfolio_close_mode": str(contract.portfolio_close_mode),
            "hedge_mode": str(contract.hedge_mode),
            "hedge_trigger_depth": int(contract.hedge_trigger_depth),
            "min_harvest_profit_usd": float(contract.min_harvest_profit_usd),
            "positive_only_closes": bool(contract.positive_only_closes),
            "max_open_per_side": int(contract.max_open_per_side),
            "max_entry_spread_ratio": float(args.max_entry_spread_ratio or 0.0),
            "liquidity_gap_spread_multiplier": float(args.liquidity_gap_spread_multiplier or 0.0),
            "liquidity_gap_spread_lookback": int(args.liquidity_gap_spread_lookback or 0),
            "liquidity_gap_spread_floor_ratio": float(args.liquidity_gap_spread_floor_ratio or 0.0),
            "require_live_admissibility": bool(args.require_live_admissibility),
            "shared_price_max_age_ms": int(args.shared_price_max_age_ms or 0),
            "session_gate": bool(args.session_gate),
            "direct_live": bool(args.direct_live),
            "live_magic": int(args.live_magic),
            "live_comment_prefix": str(args.live_comment_prefix),
            "live_volume": float(args.live_volume),
            "block_on_prestart_open_carry": bool(args.block_on_prestart_open_carry),
            "mt5_connection": mt5_connection,
        }
        runner = {
            "pid": int(os.getpid()),
            "started_at": utc_now_iso(),
            "heartbeat_at": utc_now_iso(),
            "status": "ok",
            "session_gated": False,
            "engine_family": "snake_counter_web_shadow",
            "mt5_identity_ok": bool(mt5_connection.get("identity_ok")),
            "mt5_terminal_path": str(mt5_connection.get("terminal_path") or ""),
            "mt5_login": int(mt5_connection.get("login") or 0),
            "mt5_server": str(mt5_connection.get("server") or ""),
        }
        direct_exec = None
        if bool(args.direct_live):
            direct_exec = {
                "state_path": Path(args.direct_exec_state_path),
                "log_path": Path(args.direct_exec_log_path),
                "live_magic": int(args.live_magic),
                "live_comment_prefix": str(args.live_comment_prefix),
                "live_volume": float(args.live_volume),
                "block_on_prestart_open_carry": bool(args.block_on_prestart_open_carry),
            }
        while True:
            try:
                run_once(
                    state=state,
                    contract=contract,
                    symbol_info=info,
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    runner=runner,
                    shared_price_max_age_ms=int(args.shared_price_max_age_ms or 0),
                    session_gate=bool(args.session_gate),
                    max_entry_spread_ratio=max(0.0, float(args.max_entry_spread_ratio or 0.0)),
                    liquidity_gap_spread_multiplier=max(0.0, float(args.liquidity_gap_spread_multiplier or 0.0)),
                    liquidity_gap_spread_lookback=max(0, int(args.liquidity_gap_spread_lookback or 0)),
                    liquidity_gap_spread_floor_ratio=max(0.0, float(args.liquidity_gap_spread_floor_ratio or 0.0)),
                    require_live_admissibility=bool(args.require_live_admissibility),
                    direct_exec=direct_exec,
                )
                runner["status"] = "ok"
            except Exception as exc:
                runner["status"] = "error"
                runner["last_error"] = str(exc)
                runner["heartbeat_at"] = utc_now_iso()
                save_state(state_path, state, metadata=metadata, runner=runner)
                log_runner_exception(event_path, exc, phase="run_once")
                if args.once:
                    raise
            if args.once:
                break
            time.sleep(max(0.25, float(args.poll_seconds)))
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
