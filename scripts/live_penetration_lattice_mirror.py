#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVENT_PATH = ROOT / "reports" / "penetration_lattice_live_source_events.jsonl"
DEFAULT_SOURCE_STATE_PATH = ROOT / "reports" / "penetration_lattice_live_source_state.json"
DEFAULT_STATE_PATH = ROOT / "reports" / "penetration_lattice_live_mirror_state.json"
DEFAULT_LOG_PATH = ROOT / "reports" / "penetration_lattice_live_mirror_events.jsonl"
DEFAULT_LIVE_MAGIC = 941777
DEFAULT_LIVE_COMMENT_PREFIX = "PLIVE-LATTICE"
DEFAULT_LIVE_VOLUME = 0.01
RECONCILE_RETRY_SECONDS = 5.0
STALE_REARM_REOPEN_SECONDS = 120.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_tick_stale(tick, max_age_seconds: float = 120.0) -> tuple[bool, float]:
    tick_time = float(getattr(tick, "time", 0) or 0)
    if tick_time <= 0:
        return True, float("inf")
    age = time.time() - tick_time
    return age > max_age_seconds, age


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def short_live_comment(kind: str, comment_prefix: str = DEFAULT_LIVE_COMMENT_PREFIX) -> str:
    suffix = {"open_buy": "B", "open_sell": "S", "close": "X"}.get(kind, "E")
    return f"{comment_prefix}-{suffix}"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"offset": 0, "positions": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"offset": 0, "positions": []}


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = utc_now_iso()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def tracked_position_key(symbol: str, direction: str, entry_level: float) -> str:
    # Align keying with the historical tolerance-based matching so harmless
    # float serialization differences do not create fake reconcile gaps.
    return f"{str(symbol or '').upper()}|{str(direction or '').upper()}|{float(entry_level):.5f}"


def normalize_live_magics(
    live_magic: int = DEFAULT_LIVE_MAGIC,
    attached_live_magics: list[int] | tuple[int, ...] | set[int] | None = None,
) -> tuple[int, ...]:
    ordered: list[int] = []
    for candidate in [live_magic, *(list(attached_live_magics or []))]:
        try:
            magic = int(candidate or 0)
        except Exception:
            magic = 0
        if magic <= 0 or magic in ordered:
            continue
        ordered.append(magic)
    return tuple(ordered)


def broker_position_exists(
    ticket: int,
    *,
    live_magic: int = DEFAULT_LIVE_MAGIC,
    attached_live_magics: list[int] | tuple[int, ...] | set[int] | None = None,
) -> bool:
    allowed_magics = set(normalize_live_magics(live_magic, attached_live_magics))
    if not allowed_magics:
        return False
    positions = mt5.positions_get(ticket=int(ticket)) or []
    return any(int(getattr(pos, "magic", 0) or 0) in allowed_magics for pos in positions)


def broker_live_positions(
    *,
    symbol: str | None = None,
    live_magic: int = DEFAULT_LIVE_MAGIC,
    attached_live_magics: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[dict[str, Any]]:
    allowed_magics = set(normalize_live_magics(live_magic, attached_live_magics))
    if not allowed_magics:
        return []
    requested_symbol = str(symbol or "").upper().strip()
    raw_positions = mt5.positions_get(symbol=requested_symbol) if requested_symbol else mt5.positions_get()
    if not raw_positions:
        return []
    sell_type = int(getattr(mt5, "POSITION_TYPE_SELL", 1) or 1)
    rows: list[dict[str, Any]] = []
    for pos in raw_positions:
        broker_magic = int(getattr(pos, "magic", 0) or 0)
        if broker_magic not in allowed_magics:
            continue
        pos_symbol = str(getattr(pos, "symbol", "") or "").upper()
        if requested_symbol and pos_symbol != requested_symbol:
            continue
        rows.append(
            {
                "symbol": pos_symbol,
                "direction": "SELL" if int(getattr(pos, "type", 0) or 0) == sell_type else "BUY",
                "ticket": int(getattr(pos, "ticket", 0) or 0),
                "magic": broker_magic,
                "price_open": float(getattr(pos, "price_open", 0.0) or 0.0),
                "comment": str(getattr(pos, "comment", "") or ""),
                "time": int(getattr(pos, "time", 0) or 0),
            }
        )
    rows.sort(key=lambda row: (row["symbol"], row["direction"], row["time"], row["ticket"]))
    return rows


def broker_deal_snapshot(deal_ticket: int) -> dict[str, Any] | None:
    if int(deal_ticket or 0) <= 0:
        return None
    deals = mt5.history_deals_get(ticket=int(deal_ticket)) or []
    if not deals:
        return None
    deal = deals[-1]
    return {
        "ticket": int(getattr(deal, "ticket", 0) or 0),
        "order": int(getattr(deal, "order", 0) or 0),
        "position_id": int(getattr(deal, "position_id", 0) or 0),
        "entry": int(getattr(deal, "entry", 0) or 0),
        "type": int(getattr(deal, "type", 0) or 0),
        "price": float(getattr(deal, "price", 0.0) or 0.0),
        "profit": float(getattr(deal, "profit", 0.0) or 0.0),
        "commission": float(getattr(deal, "commission", 0.0) or 0.0),
        "swap": float(getattr(deal, "swap", 0.0) or 0.0),
        "fee": float(getattr(deal, "fee", 0.0) or 0.0),
        "comment": str(getattr(deal, "comment", "") or ""),
        "time_raw": int(getattr(deal, "time", 0) or 0),
        "time_msc": int(getattr(deal, "time_msc", 0) or 0),
    }


def current_market_price(symbol: str, direction: str) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "symbol": symbol, "direction": direction}
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        out["reason"] = "no_tick"
        out["last_error"] = mt5.last_error()
        return out
    stale, age = is_tick_stale(tick)
    if stale:
        out["reason"] = "stale_tick"
        out["tick_age_seconds"] = age
        out["last_error"] = mt5.last_error()
        return out
    price = float(tick.ask if str(direction or "").upper() == "BUY" else tick.bid)
    out["ok"] = True
    out["price"] = price
    out["tick_time_raw"] = int(getattr(tick, "time", 0) or 0)
    return out


def send_market_order(
    symbol: str,
    direction: str,
    volume: float,
    comment: str,
    *,
    live_magic: int = DEFAULT_LIVE_MAGIC,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "symbol": symbol,
        "direction": direction,
        "volume": volume,
        "comment": comment,
        "attempts": [],
    }
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        out["reason"] = "no_tick"
        out["last_error"] = mt5.last_error()
        return out
    stale, age = is_tick_stale(tick)
    if stale:
        out["reason"] = "stale_tick"
        out["tick_age_seconds"] = age
        out["last_error"] = mt5.last_error()
        return out
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "BUY" else tick.bid
    before_tickets = {
        int(getattr(p, "ticket", 0) or 0)
        for p in (mt5.positions_get(symbol=symbol) or [])
        if int(getattr(p, "magic", 0) or 0) == int(live_magic)
    }
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": 50,
        "magic": int(live_magic),
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
    }
    out["requested_price"] = float(price or 0.0)
    out["tick_time_raw"] = int(getattr(tick, "time", 0) or 0)
    for filling_mode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
        request["type_filling"] = filling_mode
        result = mt5.order_send(request)
        attempt = {
            "filling_mode": int(filling_mode),
            "retcode": int(getattr(result, "retcode", 0) or 0),
            "comment": str(getattr(result, "comment", "") or ""),
            "order": int(getattr(result, "order", 0) or 0),
            "deal": int(getattr(result, "deal", 0) or 0),
            "last_error": mt5.last_error(),
        }
        out["attempts"].append(attempt)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            time.sleep(0.2)
            positions = [
                p for p in (mt5.positions_get(symbol=symbol) or [])
                if int(getattr(p, "magic", 0) or 0) == int(live_magic)
            ]
            new_positions = [p for p in positions if int(getattr(p, "ticket", 0) or 0) not in before_tickets]
            chosen = new_positions[-1] if new_positions else (positions[-1] if positions else None)
            out["ok"] = True
            out["ticket"] = int(getattr(chosen, "ticket", 0) or 0) if chosen else attempt["order"] or attempt["deal"] or 0
            out["position_comment"] = str(getattr(chosen, "comment", "") or "") if chosen else ""
            out["broker_position_price_open"] = float(getattr(chosen, "price_open", 0.0) or 0.0) if chosen else 0.0
            out["broker_fill"] = broker_deal_snapshot(attempt["deal"])
            out["reason"] = "opened"
            return out
    out["reason"] = "order_send_failed"
    return out


def close_live_position(
    ticket: int,
    *,
    live_magic: int = DEFAULT_LIVE_MAGIC,
    comment_prefix: str = DEFAULT_LIVE_COMMENT_PREFIX,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "ticket": int(ticket), "attempts": []}
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        out["reason"] = "position_not_found"
        return out
    pos = positions[0]
    mt5.symbol_select(pos.symbol, True)
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        out["reason"] = "no_tick"
        out["last_error"] = mt5.last_error()
        return out
    stale, age = is_tick_stale(tick)
    if stale:
        out["reason"] = "stale_tick"
        out["tick_age_seconds"] = age
        out["last_error"] = mt5.last_error()
        return out
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": order_type,
        "price": price,
        "position": ticket,
        "deviation": 50,
        "magic": int(live_magic),
        "comment": f"{comment_prefix}-exit",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    out["requested_price"] = float(price or 0.0)
    out["tracked_position_price_open"] = float(getattr(pos, "price_open", 0.0) or 0.0)
    for filling_mode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
        request["type_filling"] = filling_mode
        result = mt5.order_send(request)
        attempt = {
            "filling_mode": int(filling_mode),
            "retcode": int(getattr(result, "retcode", 0) or 0),
            "comment": str(getattr(result, "comment", "") or ""),
            "order": int(getattr(result, "order", 0) or 0),
            "deal": int(getattr(result, "deal", 0) or 0),
            "last_error": mt5.last_error(),
        }
        out["attempts"].append(attempt)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            out["ok"] = True
            out["broker_fill"] = broker_deal_snapshot(attempt["deal"])
            out["reason"] = "closed"
            return out
    out["reason"] = "order_send_failed"
    return out


def find_outermost_position(positions: list[dict[str, Any]], symbol: str, direction: str) -> dict[str, Any] | None:
    side = [p for p in positions if p["symbol"] == symbol and p["direction"] == direction]
    if not side:
        return None
    if direction == "SELL":
        return sorted(side, key=lambda p: float(p["entry_level"]), reverse=True)[0]
    return sorted(side, key=lambda p: float(p["entry_level"]))[0]


def find_position_by_entry_level(
    positions: list[dict[str, Any]],
    symbol: str,
    direction: str,
    entry_level: float,
    tolerance: float = 1e-5,
) -> dict[str, Any] | None:
    matches = [
        p
        for p in positions
        if p["symbol"] == symbol
        and p["direction"] == direction
        and abs(float(p["entry_level"]) - entry_level) < tolerance
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda p: str(p.get("opened_at", "")))[0]


def try_reconcile_open(
    positions: list[dict[str, Any]],
    symbol: str,
    direction: str,
    entry_level: float,
    log_path: Path,
    *,
    from_rearm: bool = False,
    opened_time: int | None = None,
    max_drift_px: float | None = None,
    live_magic: int = DEFAULT_LIVE_MAGIC,
    comment_prefix: str = DEFAULT_LIVE_COMMENT_PREFIX,
    live_volume: float = DEFAULT_LIVE_VOLUME,
) -> bool:
    if bool(from_rearm):
        opened_time = int(opened_time or 0)
        age_seconds = None if opened_time <= 0 else max(0.0, time.time() - float(opened_time))
        if age_seconds is None or age_seconds > STALE_REARM_REOPEN_SECONDS:
            append_jsonl(
                log_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "reconcile_open_deferred",
                    "reason": "stale_missing_rearm_ticket",
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "from_rearm": True,
                    "opened_time": opened_time,
                    "age_seconds": age_seconds,
                    "max_reopen_age_seconds": STALE_REARM_REOPEN_SECONDS,
                },
            )
            return False
    max_drift_px = None if max_drift_px is None else max(0.0, float(max_drift_px))
    if max_drift_px is not None and max_drift_px > 0.0:
        market = current_market_price(symbol, direction)
        if not market.get("ok"):
            append_jsonl(
                log_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "reconcile_open_deferred",
                    "reason": market.get("reason", "market_price_unavailable"),
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "max_drift_px": max_drift_px,
                    "market": market,
                },
            )
            return False
        market_price = float(market["price"])
        entry_drift_px = abs(market_price - float(entry_level))
        if entry_drift_px > max_drift_px:
            append_jsonl(
                log_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "reconcile_open_deferred",
                    "reason": "entry_drift_exceeds_guard",
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "market_price": market_price,
                    "entry_drift_px": entry_drift_px,
                    "max_drift_px": max_drift_px,
                    "tick_time_raw": market.get("tick_time_raw"),
                },
            )
            return False
    comment = short_live_comment("open_buy" if direction == "BUY" else "open_sell", comment_prefix=comment_prefix)
    result = send_market_order(symbol, direction, live_volume, comment, live_magic=live_magic)
    append_jsonl(
        log_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "reconcile_open_attempt",
            "event": {
                "action": "open_ticket",
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_level,
                "mode": "reconcile_retry",
            },
            "result": result,
        },
    )
    live_ticket = int(result.get("ticket", 0) or 0)
    if not (result.get("ok") and live_ticket > 0):
        return False
    positions.append(
        {
            "symbol": symbol,
            "direction": direction,
            "entry_level": entry_level,
            "live_ticket": live_ticket,
            "comment": comment,
            "position_comment": str(result.get("position_comment", "") or ""),
            "opened_at": utc_now_iso(),
        }
    )
    return True


def reconcile_from_source_state(
    state: dict[str, Any],
    source_state_path: Path,
    allowed_symbols: set[str],
    log_path: Path,
    *,
    flatten_tracked_extras: bool = True,
    live_magic: int = DEFAULT_LIVE_MAGIC,
    attached_live_magics: list[int] | tuple[int, ...] | set[int] | None = None,
    comment_prefix: str = DEFAULT_LIVE_COMMENT_PREFIX,
    live_volume: float = DEFAULT_LIVE_VOLUME,
) -> None:
    payload = load_json(source_state_path)
    symbols = payload.get("symbols") or {}
    tracked: list[dict[str, Any]] = state.setdefault("positions", [])
    gap_keys = set(state.setdefault("reconcile_gap_keys", []))
    retry_after: dict[str, float] = state.setdefault("reconcile_retry_after", {})
    seen_now: set[str] = set()
    now = time.time()

    desired_positions: list[dict[str, Any]] = []
    for symbol, snap in symbols.items():
        symbol = str(symbol or "").upper()
        if symbol and allowed_symbols and symbol not in allowed_symbols:
            continue
        for ticket in snap.get("open_tickets") or []:
            direction = str(ticket.get("direction", "") or "").upper()
            entry_level = float(ticket.get("entry_price", 0.0) or 0.0)
            desired_positions.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "from_rearm": bool(ticket.get("from_rearm", False)),
                    "opened_time": int(ticket.get("opened_time", 0) or 0),
                    "reconcile_open_max_drift_px": float(snap.get("reconcile_open_max_drift_px", 0.0) or 0.0),
                }
            )

    # Drop tracked entries that no longer exist broker-side so missing desired
    # positions can be reopened cleanly on the next pass.
    alive_positions: list[dict[str, Any]] = []
    for tracked_pos in tracked:
        live_ticket = int(tracked_pos.get("live_ticket", 0) or 0)
        if live_ticket > 0 and broker_position_exists(
            live_ticket,
            live_magic=live_magic,
            attached_live_magics=attached_live_magics,
        ):
            alive_positions.append(tracked_pos)
            continue
        append_jsonl(
            log_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "reconcile_drop_tracked_position",
                "reason": "broker_position_missing",
                "tracked": tracked_pos,
            },
        )
    tracked[:] = alive_positions

    desired_counts: dict[str, int] = defaultdict(int)
    desired_examples: dict[str, dict[str, Any]] = {}
    for desired in desired_positions:
        key = tracked_position_key(desired["symbol"], desired["direction"], desired["entry_level"])
        desired_counts[key] += 1
        desired_examples.setdefault(key, desired)

    tracked_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tracked_pos in tracked:
        key = tracked_position_key(
            tracked_pos.get("symbol", ""),
            tracked_pos.get("direction", ""),
            float(tracked_pos.get("entry_level", 0.0) or 0.0),
        )
        tracked_buckets[key].append(tracked_pos)

    # For bar/event-mirrored lanes the source snapshot can authoritatively
    # flatten extras. Direct-live tick lanes should disable this because a
    # transient source-state drop is safer to carry forward than to market-close.
    for key, bucket in list(tracked_buckets.items()):
        desired_count = int(desired_counts.get(key, 0) or 0)
        if len(bucket) <= desired_count:
            continue
        ordered_bucket = sorted(bucket, key=lambda p: str(p.get("opened_at", "")))
        extras = ordered_bucket[desired_count:]
        if not flatten_tracked_extras:
            for target in extras:
                append_jsonl(
                    log_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "reconcile_preserve_extra_tracked_position",
                        "reason": "flatten_disabled_for_direct_live",
                        "tracked": target,
                    },
                )
            continue
        for target in extras:
            result = close_live_position(int(target["live_ticket"]), live_magic=live_magic, comment_prefix=comment_prefix)
            append_jsonl(
                log_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "reconcile_close_attempt",
                    "reason": "tracked_position_not_in_source_state",
                    "tracked": target,
                    "result": result,
                },
            )
            if result.get("ok") or result.get("reason") == "position_not_found":
                if target in tracked:
                    tracked.remove(target)

    tracked_buckets = defaultdict(list)
    for tracked_pos in tracked:
        key = tracked_position_key(
            tracked_pos.get("symbol", ""),
            tracked_pos.get("direction", ""),
            float(tracked_pos.get("entry_level", 0.0) or 0.0),
        )
        tracked_buckets[key].append(tracked_pos)

    for key, desired_count in desired_counts.items():
        desired = desired_examples[key]
        current_count = len(tracked_buckets.get(key, []))
        while current_count < desired_count:
            gap_key = f"{key}|slot={current_count}"
            if gap_key not in gap_keys:
                append_jsonl(
                    log_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "reconcile_gap_detected",
                        "symbol": desired["symbol"],
                        "direction": desired["direction"],
                        "entry_level": desired["entry_level"],
                        "slot_index": current_count,
                        "reason": "source_has_open_ticket_but_mirror_does_not",
                    },
                )
            if float(retry_after.get(gap_key, 0.0) or 0.0) <= now:
                if try_reconcile_open(
                    tracked,
                    desired["symbol"],
                    desired["direction"],
                    desired["entry_level"],
                    log_path,
                    from_rearm=bool(desired.get("from_rearm", False)),
                    opened_time=int(desired.get("opened_time", 0) or 0),
                    max_drift_px=desired.get("reconcile_open_max_drift_px"),
                    live_magic=live_magic,
                    comment_prefix=comment_prefix,
                    live_volume=live_volume,
                ):
                    retry_after.pop(gap_key, None)
                    gap_keys.discard(gap_key)
                    current_count += 1
                    continue
                retry_after[gap_key] = now + RECONCILE_RETRY_SECONDS
            seen_now.add(gap_key)
            gap_keys.add(gap_key)
            current_count += 1
    state["reconcile_gap_keys"] = sorted(seen_now)
    state["reconcile_retry_after"] = {key: retry_after[key] for key in sorted(seen_now) if key in retry_after}


def process_event(
    event: dict[str, Any],
    state: dict[str, Any],
    allowed_symbols: set[str],
    log_path: Path,
    *,
    live_magic: int = DEFAULT_LIVE_MAGIC,
    comment_prefix: str = DEFAULT_LIVE_COMMENT_PREFIX,
    live_volume: float = DEFAULT_LIVE_VOLUME,
) -> None:
    action = str(event.get("action", "") or "")
    symbol = str(event.get("symbol", "") or "").upper()
    if symbol and allowed_symbols and symbol not in allowed_symbols:
        append_jsonl(log_path, {"ts_utc": utc_now_iso(), "action": "skip_event", "reason": "symbol_not_allowed", "event": event})
        return
    positions: list[dict[str, Any]] = state.setdefault("positions", [])

    if action == "open_ticket":
        direction = str(event.get("direction", "") or "").upper()
        entry_level = float(event.get("entry_price", 0.0) or 0.0)
        already = any(
            p["symbol"] == symbol
            and p["direction"] == direction
            and abs(float(p["entry_level"]) - entry_level) < 1e-5
            for p in positions
        )
        if already:
            append_jsonl(
                log_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "skip_event",
                    "reason": "tracked_position_already_exists",
                    "event": event,
                },
            )
            return
        comment = short_live_comment("open_buy" if direction == "BUY" else "open_sell", comment_prefix=comment_prefix)
        result = send_market_order(symbol, direction, live_volume, comment, live_magic=live_magic)
        append_jsonl(log_path, {"ts_utc": utc_now_iso(), "action": "open_attempt", "event": event, "result": result})
        live_ticket = int(result.get("ticket", 0) or 0)
        if result.get("ok") and live_ticket > 0:
            positions.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "live_ticket": live_ticket,
                    "comment": comment,
                    "position_comment": str(result.get("position_comment", "") or ""),
                    "opened_at": utc_now_iso(),
                }
            )
        return

    if action == "close_ticket":
        direction = str(event.get("direction", "") or "").upper()
        entry_level = float(event.get("entry_price", 0.0) or 0.0)
        target = find_position_by_entry_level(positions, symbol, direction, entry_level)
        if not target and entry_level > 0:
            append_jsonl(
                log_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "close_attempt",
                    "reason": "no_exact_tracked_position",
                    "event": event,
                },
            )
            return
        if not target:
            target = find_outermost_position(positions, symbol, direction)
        if not target:
            append_jsonl(log_path, {"ts_utc": utc_now_iso(), "action": "close_attempt", "reason": "no_tracked_position", "event": event})
            return
        result = close_live_position(int(target["live_ticket"]), live_magic=live_magic, comment_prefix=comment_prefix)
        append_jsonl(log_path, {"ts_utc": utc_now_iso(), "action": "close_attempt", "event": event, "tracked": target, "result": result})
        if result.get("ok"):
            positions.remove(target)
        return

    if action in {"forced_unwind", "breakout_kill", "timed_kill"}:
        for target in [p for p in list(positions) if p["symbol"] == symbol]:
            result = close_live_position(int(target["live_ticket"]), live_magic=live_magic, comment_prefix=comment_prefix)
            append_jsonl(log_path, {"ts_utc": utc_now_iso(), "action": "flush_attempt", "event": event, "tracked": target, "result": result})
            if result.get("ok"):
                positions.remove(target)
        return

    append_jsonl(log_path, {"ts_utc": utc_now_iso(), "action": "skip_event", "reason": "unsupported_action", "event": event})


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror fresh-start lattice shadow events into live 0.01-lot MT5 trades.")
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--source-state-path", default=str(DEFAULT_SOURCE_STATE_PATH))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "USDJPY"])
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--live-magic", type=int, default=DEFAULT_LIVE_MAGIC)
    parser.add_argument("--live-comment-prefix", default=DEFAULT_LIVE_COMMENT_PREFIX)
    parser.add_argument("--live-volume", type=float, default=DEFAULT_LIVE_VOLUME)
    args = parser.parse_args()

    event_path = Path(args.event_path)
    source_state_path = Path(args.source_state_path)
    state_path = Path(args.state_path)
    log_path = Path(args.log_path)
    allowed_symbols = {str(sym or "").upper() for sym in args.symbols}

    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(
        mt5_module=mt5,
        require_trade_allowed=True,
    )
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    try:
        state = load_state(state_path)
        if args.start_at_end and event_path.exists() and not state.get("offset"):
            state["offset"] = event_path.stat().st_size
            save_state(state_path, state)
        reconcile_from_source_state(
            state,
            source_state_path,
            allowed_symbols,
            log_path,
            live_magic=args.live_magic,
            comment_prefix=args.live_comment_prefix,
            live_volume=args.live_volume,
        )
        save_state(state_path, state)

        while True:
            reconcile_from_source_state(
                state,
                source_state_path,
                allowed_symbols,
                log_path,
                live_magic=args.live_magic,
                comment_prefix=args.live_comment_prefix,
                live_volume=args.live_volume,
            )
            if event_path.exists():
                state["offset"] = event_path.stat().st_size
            save_state(state_path, state)
            if args.once:
                return 0
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
