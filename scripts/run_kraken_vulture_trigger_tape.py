#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kraken_config as cfg  # noqa: E402
from build_kraken_vulture_reversal_replay import normalize_product  # noqa: E402
from build_spot_numeraire_accumulation_board import product_id_for_pair  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, to_float  # noqa: E402


DEFAULT_PRODUCTS = "LDO-USD,XION-USD,CQT-USD,AIN-USD"
DEFAULT_EVENT_PATH = REPORTS / "kraken_vulture_trigger_tape_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_vulture_trigger_tape_summary.json"


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class Book:
    bid: float
    ask: float
    bids: list[Level]
    asks: list[Level]


@dataclass(frozen=True)
class Fill:
    ok: bool
    qty: float
    gross_quote: float
    avg_price: float
    depth_notional: float
    reason: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def parse_csv(raw: str | Iterable[str]) -> list[str]:
    parts: list[str] = []
    if isinstance(raw, str):
        parts.extend(raw.split(","))
    else:
        for item in raw:
            parts.extend(str(item).split(","))
    return [part.strip() for part in parts if part.strip()]


def spread_bps(bid: float, ask: float) -> float:
    mid = (float(bid) + float(ask)) / 2.0
    return ((float(ask) - float(bid)) / mid) * 10000.0 if mid > 0.0 else 0.0


def load_pairs(client: KrakenSpotClient, products: list[str]) -> dict[str, KrakenPair]:
    wanted = {normalize_product(product): normalize_product(product) for product in products}
    pairs: dict[str, KrakenPair] = {}
    for rest_pair, payload in client.asset_pairs().items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None or pair.status.lower() not in {"online", "post_only", ""}:
            continue
        product_id = product_id_for_pair(pair)
        if product_id in wanted:
            pairs[product_id] = pair
    return pairs


def parse_book(depth_payload: dict[str, Any]) -> Book | None:
    if not isinstance(depth_payload, dict) or not depth_payload:
        return None
    raw_book = next(iter(depth_payload.values()))
    if not isinstance(raw_book, dict):
        return None
    bids = [
        Level(price=to_float(row[0]), size=to_float(row[1]))
        for row in raw_book.get("bids") or []
        if isinstance(row, list) and len(row) >= 2 and to_float(row[0]) > 0.0 and to_float(row[1]) > 0.0
    ]
    asks = [
        Level(price=to_float(row[0]), size=to_float(row[1]))
        for row in raw_book.get("asks") or []
        if isinstance(row, list) and len(row) >= 2 and to_float(row[0]) > 0.0 and to_float(row[1]) > 0.0
    ]
    if not bids or not asks:
        return None
    bids.sort(key=lambda level: level.price, reverse=True)
    asks.sort(key=lambda level: level.price)
    return Book(bid=bids[0].price, ask=asks[0].price, bids=bids, asks=asks)


def buy_fill_for_quote(asks: list[Level], quote_usd: float) -> Fill:
    remaining = max(0.0, float(quote_usd))
    qty = 0.0
    spent = 0.0
    depth_notional = sum(level.price * level.size for level in asks)
    for level in asks:
        if remaining <= 0.0:
            break
        take_qty = min(level.size, remaining / level.price)
        qty += take_qty
        spent += take_qty * level.price
        remaining -= take_qty * level.price
    if remaining > max(0.000001, quote_usd * 0.000001):
        return Fill(False, qty, spent, spent / qty if qty > 0.0 else 0.0, depth_notional, "insufficient_ask_depth")
    return Fill(True, qty, spent, spent / qty if qty > 0.0 else 0.0, depth_notional, "filled")


def sell_fill_for_qty(bids: list[Level], qty: float) -> Fill:
    remaining = max(0.0, float(qty))
    sold = 0.0
    proceeds = 0.0
    depth_notional = sum(level.price * level.size for level in bids)
    for level in bids:
        if remaining <= 0.0:
            break
        take_qty = min(level.size, remaining)
        sold += take_qty
        proceeds += take_qty * level.price
        remaining -= take_qty
    if remaining > max(0.00000001, qty * 0.000001):
        return Fill(False, sold, proceeds, proceeds / sold if sold > 0.0 else 0.0, depth_notional, "insufficient_bid_depth")
    return Fill(True, sold, proceeds, proceeds / sold if sold > 0.0 else 0.0, depth_notional, "filled")


def net_bps_for_exit(cost_usd: float, exit_quote_after_fee: float) -> float:
    return ((float(exit_quote_after_fee) / float(cost_usd)) - 1.0) * 10000.0 if cost_usd > 0.0 else 0.0


def min_size_blockers(pair: KrakenPair, deploy_usd: float, qty: float) -> list[str]:
    blockers: list[str] = []
    if float(deploy_usd) < pair.cost_min:
        blockers.append("below_cost_min")
    if float(qty) < pair.order_min:
        blockers.append("below_order_min")
    return blockers


def new_dump_trigger(
    *,
    product_id: str,
    book: Book,
    samples: deque[dict[str, float]],
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if len(samples) < int(args.lookback_samples) + 1:
        return None
    prior = list(samples)[:-1]
    if not prior:
        return None
    prior_high = max(row["bid"] for row in prior)
    if prior_high <= 0.0:
        return None
    signal_bid = samples[-1]["bid"]
    dump_bps = ((signal_bid / prior_high) - 1.0) * 10000.0
    if dump_bps > -abs(float(args.min_dump_bps)):
        return None
    return {
        "product_id": product_id,
        "armed_at_epoch": time.time(),
        "armed_at": utc_now_iso(),
        "prior_high_bid": prior_high,
        "signal_bid": signal_bid,
        "signal_ask": book.ask,
        "dump_bps": dump_bps,
        "low_bid": signal_bid,
        "low_at_epoch": time.time(),
        "reclaim_bps": 0.0,
    }


def update_reclaim_trigger(trigger: dict[str, Any], book: Book) -> dict[str, Any]:
    low_bid = to_float(trigger.get("low_bid"))
    if low_bid <= 0.0 or book.bid < low_bid:
        trigger["low_bid"] = book.bid
        trigger["low_at_epoch"] = time.time()
        low_bid = book.bid
    trigger["reclaim_bps"] = ((book.bid / low_bid) - 1.0) * 10000.0 if low_bid > 0.0 else 0.0
    return trigger


def trigger_expired(trigger: dict[str, Any], args: argparse.Namespace) -> bool:
    return time.time() - to_float(trigger.get("armed_at_epoch")) >= float(args.reclaim_timeout_seconds)


def open_position_from_trigger(
    *,
    product_id: str,
    pair: KrakenPair,
    book: Book,
    trigger: dict[str, Any],
    args: argparse.Namespace,
    event_path: Path,
) -> dict[str, Any] | None:
    now_epoch = time.time()
    current_spread_bps = spread_bps(book.bid, book.ask)
    dump_bps = to_float(trigger.get("dump_bps"))
    reclaim_bps = to_float(trigger.get("reclaim_bps"))
    reclaim_after_spread_bps = reclaim_bps - current_spread_bps
    reclaim_after_cost_bps = reclaim_after_spread_bps - (2.0 * float(args.taker_fee_bps))
    low_age_seconds = now_epoch - to_float(trigger.get("low_at_epoch"))
    reclaim_velocity_bps_per_second = reclaim_bps / low_age_seconds if low_age_seconds > 0.0 else 0.0
    if low_age_seconds < float(args.min_low_age_seconds):
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_low_not_stable",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(reclaim_bps, 6),
                "low_age_seconds": round(low_age_seconds, 3),
                "min_low_age_seconds": float(args.min_low_age_seconds),
            },
        )
        return None
    if float(args.max_low_age_seconds) > 0.0 and low_age_seconds > float(args.max_low_age_seconds):
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_low_stale",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(reclaim_bps, 6),
                "low_age_seconds": round(low_age_seconds, 3),
                "max_low_age_seconds": float(args.max_low_age_seconds),
            },
        )
        return None
    if reclaim_velocity_bps_per_second < float(args.min_reclaim_velocity_bps_per_second):
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_slow_reclaim",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(reclaim_bps, 6),
                "low_age_seconds": round(low_age_seconds, 3),
                "reclaim_velocity_bps_per_second": round(reclaim_velocity_bps_per_second, 6),
                "min_reclaim_velocity_bps_per_second": float(args.min_reclaim_velocity_bps_per_second),
            },
        )
        return None
    if current_spread_bps > float(args.max_spread_bps):
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_spread",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(to_float(trigger.get("reclaim_bps")), 6),
                "low_age_seconds": round(low_age_seconds, 3),
                "reclaim_velocity_bps_per_second": round(reclaim_velocity_bps_per_second, 6),
                "spread_bps": round(current_spread_bps, 6),
            },
        )
        return None
    if reclaim_after_spread_bps < float(args.min_reclaim_after_spread_bps):
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_reclaim_edge",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(reclaim_bps, 6),
                "spread_bps": round(current_spread_bps, 6),
                "reclaim_after_spread_bps": round(reclaim_after_spread_bps, 6),
                "low_age_seconds": round(low_age_seconds, 3),
                "reclaim_velocity_bps_per_second": round(reclaim_velocity_bps_per_second, 6),
                "min_reclaim_after_spread_bps": float(args.min_reclaim_after_spread_bps),
            },
        )
        return None
    if reclaim_after_cost_bps < float(args.min_reclaim_after_cost_bps):
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_reclaim_cost_edge",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(reclaim_bps, 6),
                "spread_bps": round(current_spread_bps, 6),
                "reclaim_after_spread_bps": round(reclaim_after_spread_bps, 6),
                "reclaim_after_cost_bps": round(reclaim_after_cost_bps, 6),
                "min_reclaim_after_cost_bps": float(args.min_reclaim_after_cost_bps),
                "low_age_seconds": round(low_age_seconds, 3),
                "reclaim_velocity_bps_per_second": round(reclaim_velocity_bps_per_second, 6),
            },
        )
        return None
    entry_fee = float(args.deploy_usd) * float(args.taker_fee_bps) / 10000.0
    quote_to_trade = float(args.deploy_usd) - entry_fee
    entry_fill = buy_fill_for_quote(book.asks, quote_to_trade)
    blockers = min_size_blockers(pair, float(args.deploy_usd), entry_fill.qty)
    if not entry_fill.ok:
        blockers.append(entry_fill.reason)
    if blockers:
        append_jsonl(
            event_path,
            {
                "event": "entry_blocked_depth_or_min_size",
                "ts_utc": utc_now_iso(),
                "product_id": product_id,
                "dump_bps": round(dump_bps, 6),
                "reclaim_bps": round(to_float(trigger.get("reclaim_bps")), 6),
                "spread_bps": round(current_spread_bps, 6),
                "low_age_seconds": round(low_age_seconds, 3),
                "reclaim_velocity_bps_per_second": round(reclaim_velocity_bps_per_second, 6),
                "ask_depth_notional": round(entry_fill.depth_notional, 6),
                "blockers": blockers,
            },
        )
        return None
    position = {
        "product_id": product_id,
        "opened_at_epoch": time.time(),
        "opened_at": utc_now_iso(),
        "entry_trigger_mode": args.entry_trigger_mode,
        "prior_high_bid": trigger.get("prior_high_bid"),
        "signal_bid": trigger.get("signal_bid"),
        "low_bid": trigger.get("low_bid"),
        "dump_bps": dump_bps,
        "reclaim_bps": reclaim_bps,
        "reclaim_after_spread_bps": reclaim_after_spread_bps,
        "reclaim_after_cost_bps": reclaim_after_cost_bps,
        "low_age_seconds": low_age_seconds,
        "reclaim_velocity_bps_per_second": reclaim_velocity_bps_per_second,
        "entry_avg_price": entry_fill.avg_price,
        "entry_top_ask": book.ask,
        "entry_spread_bps": current_spread_bps,
        "entry_fee_usd": entry_fee,
        "qty": entry_fill.qty,
        "cost_usd": float(args.deploy_usd),
        "max_net_bps": -float(args.taker_fee_bps),
        "min_net_bps": -float(args.taker_fee_bps),
        "max_bid": book.bid,
    }
    append_jsonl(
        event_path,
        {
            "event": "shadow_taker_entry",
            "ts_utc": utc_now_iso(),
            "product_id": product_id,
            "entry_trigger_mode": args.entry_trigger_mode,
            "dump_bps": round(dump_bps, 6),
            "reclaim_bps": round(reclaim_bps, 6),
            "spread_bps": round(current_spread_bps, 6),
            "reclaim_after_spread_bps": round(reclaim_after_spread_bps, 6),
            "reclaim_after_cost_bps": round(reclaim_after_cost_bps, 6),
            "low_age_seconds": round(low_age_seconds, 3),
            "reclaim_velocity_bps_per_second": round(reclaim_velocity_bps_per_second, 6),
            "low_bid": round(to_float(trigger.get("low_bid")), 12),
            "entry_avg_price": round(entry_fill.avg_price, 12),
            "qty": round(entry_fill.qty, 12),
            "cost_usd": round(float(args.deploy_usd), 6),
            "entry_fee_usd": round(entry_fee, 8),
            "ask_depth_notional": round(entry_fill.depth_notional, 6),
        },
    )
    return position


def maybe_open_position(
    *,
    product_id: str,
    pair: KrakenPair,
    book: Book,
    samples: deque[dict[str, float]],
    pending_triggers: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    event_path: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    pending = pending_triggers.get(product_id)
    if pending is not None:
        pending = update_reclaim_trigger(pending, book)
        if to_float(pending.get("reclaim_bps")) >= float(args.reclaim_bps):
            position = open_position_from_trigger(
                product_id=product_id,
                pair=pair,
                book=book,
                trigger=pending,
                args=args,
                event_path=event_path,
            )
            pending_triggers.pop(product_id, None)
            return position, "reclaim_entry_attempted"
        if trigger_expired(pending, args):
            append_jsonl(
                event_path,
                {
                    "event": "reclaim_trigger_expired",
                    "ts_utc": utc_now_iso(),
                    "product_id": product_id,
                    "dump_bps": round(to_float(pending.get("dump_bps")), 6),
                    "best_reclaim_bps": round(to_float(pending.get("reclaim_bps")), 6),
                    "timeout_seconds": float(args.reclaim_timeout_seconds),
                },
            )
            pending_triggers.pop(product_id, None)
            return None, "reclaim_expired"
        return None, None

    trigger = new_dump_trigger(product_id=product_id, book=book, samples=samples, args=args)
    if trigger is None:
        return None, None
    if args.entry_trigger_mode == "immediate":
        return (
            open_position_from_trigger(
                product_id=product_id,
                pair=pair,
                book=book,
                trigger=trigger,
                args=args,
                event_path=event_path,
            ),
            "immediate_entry_attempted",
        )
    pending_triggers[product_id] = trigger
    append_jsonl(
        event_path,
        {
            "event": "reclaim_trigger_armed",
            "ts_utc": utc_now_iso(),
            "product_id": product_id,
            "dump_bps": round(to_float(trigger.get("dump_bps")), 6),
            "signal_bid": round(to_float(trigger.get("signal_bid")), 12),
            "prior_high_bid": round(to_float(trigger.get("prior_high_bid")), 12),
            "required_reclaim_bps": float(args.reclaim_bps),
            "timeout_seconds": float(args.reclaim_timeout_seconds),
        },
    )
    return None, "reclaim_armed"


def mark_position(position: dict[str, Any], book: Book, args: argparse.Namespace) -> dict[str, Any]:
    exit_fill = sell_fill_for_qty(book.bids, to_float(position.get("qty")))
    exit_fee = exit_fill.gross_quote * float(args.taker_fee_bps) / 10000.0
    exit_after_fee = exit_fill.gross_quote - exit_fee
    net_bps = net_bps_for_exit(to_float(position.get("cost_usd")), exit_after_fee)
    position["last_bid"] = book.bid
    position["last_exit_avg_price"] = exit_fill.avg_price
    position["last_bid_depth_notional"] = exit_fill.depth_notional
    position["last_exit_depth_ok"] = exit_fill.ok
    position["last_net_bps"] = net_bps
    position["max_net_bps"] = max(to_float(position.get("max_net_bps")), net_bps)
    position["min_net_bps"] = min(to_float(position.get("min_net_bps")), net_bps)
    position["max_bid"] = max(to_float(position.get("max_bid")), book.bid)
    position["last_exit_after_fee_usd"] = exit_after_fee
    position["last_exit_fee_usd"] = exit_fee
    return position


def exit_reason(position: dict[str, Any], args: argparse.Namespace) -> str | None:
    age = time.time() - to_float(position.get("opened_at_epoch"))
    net_bps = to_float(position.get("last_net_bps"))
    max_net_bps = to_float(position.get("max_net_bps"))
    if not position.get("last_exit_depth_ok"):
        return None
    if net_bps >= float(args.take_profit_bps):
        return "take_profit"
    if max_net_bps >= float(args.trail_after_bps):
        giveback = max_net_bps - net_bps
        if giveback >= float(args.trail_giveback_bps) and net_bps >= float(args.min_trail_exit_bps):
            return "profit_trail"
    if net_bps <= -abs(float(args.stop_loss_bps)):
        return "stop_loss"
    if age >= float(args.max_hold_seconds):
        return "time_force_close"
    return None


def close_position(position: dict[str, Any], reason: str, event_path: Path) -> dict[str, Any]:
    event = {
        "event": "shadow_taker_exit",
        "ts_utc": utc_now_iso(),
        "reason": reason,
        "product_id": position.get("product_id"),
        "age_seconds": round(time.time() - to_float(position.get("opened_at_epoch")), 3),
        "net_bps": round(to_float(position.get("last_net_bps")), 6),
        "max_net_bps": round(to_float(position.get("max_net_bps")), 6),
        "min_net_bps": round(to_float(position.get("min_net_bps")), 6),
        "exit_avg_price": round(to_float(position.get("last_exit_avg_price")), 12),
        "exit_after_fee_usd": round(to_float(position.get("last_exit_after_fee_usd")), 8),
        "exit_fee_usd": round(to_float(position.get("last_exit_fee_usd")), 8),
    }
    append_jsonl(event_path, event)
    return event


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = KrakenSpotClient()
    products = [normalize_product(product) for product in parse_csv(args.products)]
    pairs = load_pairs(client, products)
    missing = sorted(set(products) - set(pairs))
    event_path = Path(args.event_path)
    summary_path = Path(args.summary_path)
    samples: dict[str, deque[dict[str, float]]] = {
        product_id: deque(maxlen=max(2, int(args.lookback_samples) + 1)) for product_id in pairs
    }
    positions: dict[str, dict[str, Any]] = {}
    pending_triggers: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    blocked = 0
    armed = 0
    expired = 0
    entry_attempts = 0
    started = time.time()
    cycles = 0
    while time.time() - started < float(args.duration_seconds):
        cycles += 1
        for product_id, pair in pairs.items():
            book = parse_book(client.depth(pair.rest_pair, count=int(args.depth_count)))
            if book is None:
                continue
            now = time.time()
            samples[product_id].append({"ts": now, "bid": book.bid, "ask": book.ask})
            if product_id in positions:
                position = mark_position(positions[product_id], book, args)
                reason = exit_reason(position, args)
                if reason:
                    closed.append(close_position(position, reason, event_path))
                    del positions[product_id]
                continue
            before_events = event_path.stat().st_size if event_path.exists() else 0
            position, action = maybe_open_position(
                product_id=product_id,
                pair=pair,
                book=book,
                samples=samples[product_id],
                pending_triggers=pending_triggers,
                args=args,
                event_path=event_path,
            )
            after_events = event_path.stat().st_size if event_path.exists() else 0
            if action == "reclaim_armed":
                armed += 1
            elif action == "reclaim_expired":
                expired += 1
            elif action in {"reclaim_entry_attempted", "immediate_entry_attempted"}:
                entry_attempts += 1
            if position is not None:
                positions[product_id] = position
            elif action in {"reclaim_entry_attempted", "immediate_entry_attempted"} and after_events > before_events:
                blocked += 1
        time.sleep(max(0.1, float(args.poll_seconds)))
    for product_id, position in list(positions.items()):
        pair = pairs.get(product_id)
        if pair is None:
            continue
        book = parse_book(client.depth(pair.rest_pair, count=int(args.depth_count)))
        if book is None:
            continue
        position = mark_position(position, book, args)
        if position.get("last_exit_depth_ok"):
            closed.append(close_position(position, "end_force_close", event_path))
        del positions[product_id]
    winners = [row for row in closed if to_float(row.get("net_bps")) > 0.0]
    summary = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_vulture_trigger_tape",
        "read": "Public-only forward tape. Simulates taker entry and taker exit from live order-book depth; no private endpoints and no orders.",
        "parameters": vars(args),
        "products_loaded": sorted(pairs),
        "missing_products": missing,
        "cycles": cycles,
        "closed_trades": len(closed),
        "winners": len(winners),
        "pending_triggers": len(pending_triggers),
        "armed_triggers": armed,
        "expired_triggers": expired,
        "entry_attempts": entry_attempts,
        "blocked_entry_events": blocked,
        "best_net_bps": max((to_float(row.get("net_bps")) for row in closed), default=0.0),
        "worst_net_bps": min((to_float(row.get("net_bps")) for row in closed), default=0.0),
        "closed": closed,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forward-only Kraken dump-recovery vulture trigger tape.")
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--duration-seconds", type=float, default=180.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--lookback-samples", type=int, default=20)
    parser.add_argument("--entry-trigger-mode", choices=["reclaim", "immediate"], default="reclaim")
    parser.add_argument("--min-dump-bps", type=float, default=80.0)
    parser.add_argument("--reclaim-bps", type=float, default=40.0)
    parser.add_argument("--min-reclaim-after-spread-bps", type=float, default=40.0)
    parser.add_argument("--min-reclaim-after-cost-bps", type=float, default=0.0)
    parser.add_argument("--min-low-age-seconds", type=float, default=0.0)
    parser.add_argument("--max-low-age-seconds", type=float, default=0.0)
    parser.add_argument("--min-reclaim-velocity-bps-per-second", type=float, default=0.0)
    parser.add_argument("--reclaim-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-spread-bps", type=float, default=250.0)
    parser.add_argument("--deploy-usd", type=float, default=15.0)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--depth-count", type=int, default=20)
    parser.add_argument("--take-profit-bps", type=float, default=80.0)
    parser.add_argument("--trail-after-bps", type=float, default=40.0)
    parser.add_argument("--trail-giveback-bps", type=float, default=25.0)
    parser.add_argument("--min-trail-exit-bps", type=float, default=5.0)
    parser.add_argument("--stop-loss-bps", type=float, default=180.0)
    parser.add_argument("--max-hold-seconds", type=float, default=300.0)
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser.parse_args()


def main() -> int:
    summary = run(parse_args())
    print(
        json.dumps(
            {
                "summary_path": str(summary["parameters"]["summary_path"]),
                "products_loaded": summary["products_loaded"],
                "closed_trades": summary["closed_trades"],
                "winners": summary["winners"],
                "best_net_bps": summary["best_net_bps"],
                "armed_triggers": summary["armed_triggers"],
                "entry_attempts": summary["entry_attempts"],
                "blocked_entry_events": summary["blocked_entry_events"],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
