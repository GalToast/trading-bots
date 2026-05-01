#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_spot_numeraire_accumulation_board import product_id_for_pair  # noqa: E402
from build_kraken_grid_exit_first_router import build_payload as build_router_payload  # noqa: E402
from build_kraken_grid_exit_first_router import load_usd_pairs  # noqa: E402
from kraken_config import DEFAULT_MAKER_FEE_BPS, DEFAULT_TAKER_FEE_BPS  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient  # noqa: E402
from run_kraken_grid_shadow_tape import (  # noqa: E402
    GridPosition,
    TradePrint,
    eligible_buy_trade_fill,
    eligible_sell_trade_fill,
    liquidate_inventory,
    mark_inventory,
    parse_trades,
)
from run_kraken_vulture_trigger_tape import Book, parse_book, spread_bps  # noqa: E402


DEFAULT_EVENT_PATH = REPORTS / "kraken_grid_adaptive_router_shadow_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_grid_adaptive_router_shadow_summary.json"


@dataclass
class StandingBid:
    product_id: str
    rest_pair: str
    buy_price: float
    target_price: float
    allocation_usd: float
    qty: float
    level: int
    opened_at: str
    expires_at: float
    router_row: dict[str, Any]
    last_trade_ts: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def best_fire_candidate(
    router_payload: dict[str, Any],
    *,
    cooldowns: dict[str, float] | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    current_time = time.time() if now is None else float(now)
    active_cooldowns = cooldowns or {}
    for row in router_payload.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        if active_cooldowns.get(product_id, 0.0) > current_time:
            continue
        if row.get("roundtrip_exit_ok") and not row.get("blockers"):
            return row
    return None


def standing_bid_from_row(row: dict[str, Any], *, standing_seconds: float) -> StandingBid:
    buy_price = float(row["buy_price"])
    allocation = float(row["allocation_usd"])
    qty = allocation / buy_price if buy_price > 0.0 else 0.0
    now = time.time()
    return StandingBid(
        product_id=str(row["product_id"]),
        rest_pair=str(row["rest_pair"]),
        buy_price=buy_price,
        target_price=float(row["target_price"]),
        allocation_usd=allocation,
        qty=qty,
        level=1,
        opened_at=utc_now_iso(),
        expires_at=now + max(0.0, float(standing_seconds)),
        router_row=row,
        last_trade_ts=max(0.0, now - 5.0),
    )


def position_from_bid(bid: StandingBid, *, maker_fee_bps: float) -> GridPosition:
    buy_fee = bid.allocation_usd * float(maker_fee_bps) / 10000.0
    return GridPosition(
        level=bid.level,
        buy_price=bid.buy_price,
        target_price=bid.target_price,
        qty=bid.qty,
        cost_usd=bid.allocation_usd,
        buy_fee_usd=buy_fee,
        opened_at=utc_now_iso(),
        opened_ts=time.time(),
    )


def summary_payload(
    *,
    started_at: str,
    cash: float,
    initial_capital: float,
    active_bid: StandingBid | None,
    active_product: str,
    position: GridPosition | None,
    book: Book | None,
    realized_net: float,
    fees: float,
    router_scans: int,
    fire_candidates: int,
    standing_bids: int,
    bid_expirations: int,
    buys: int,
    target_closes: int,
    sweep_closes: int,
    blocked_entries: int,
    blocked_exits: int,
    no_fire_scans: int,
    taker_fee_bps: float,
    liquidation_haircut_bps: float,
) -> dict[str, Any]:
    positions = [position] if position is not None else []
    inventory_value = 0.0
    inventory_pnl = 0.0
    inventory_mark_costs = 0.0
    if book is not None and positions:
        inventory_value, inventory_pnl, inventory_mark_costs = mark_inventory(
            positions,
            book,
            taker_fee_bps=taker_fee_bps,
            haircut_bps=liquidation_haircut_bps,
        )
    equity = cash + inventory_value
    return {
        "generated_at": utc_now_iso(),
        "started_at": started_at,
        "active_product": active_product,
        "active_bid": active_bid.product_id if active_bid else "",
        "cash": round(cash, 8),
        "equity": round(equity, 8),
        "return_pct": round((equity / initial_capital - 1.0) * 100.0, 6) if initial_capital > 0.0 else 0.0,
        "realized_net_usd": round(realized_net, 8),
        "fees_usd": round(fees, 8),
        "open_positions": len(positions),
        "open_inventory_value": round(inventory_value, 8),
        "open_inventory_pnl": round(inventory_pnl, 8),
        "open_inventory_mark_costs": round(inventory_mark_costs, 8),
        "router_scans": router_scans,
        "fire_candidates": fire_candidates,
        "no_fire_scans": no_fire_scans,
        "standing_bids": standing_bids,
        "bid_expirations": bid_expirations,
        "buys": buys,
        "target_closes": target_closes,
        "sweep_closes": sweep_closes,
        "blocked_entries": blocked_entries,
        "blocked_exits": blocked_exits,
        "last_bid": round(book.bid, 12) if book else 0.0,
        "last_ask": round(book.ask, 12) if book else 0.0,
        "last_spread_bps": round(spread_bps(book.bid, book.ask), 6) if book else 0.0,
    }


def router_args_from(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        top_n_volume=args.top_n_volume,
        lookback_seconds=args.lookback_seconds,
        trade_count=args.trade_count,
        spacing_bps=args.spacing_bps,
        levels=args.levels,
        entry_offset_mult=args.entry_offset_mult,
        initial_capital=args.initial_capital,
        maker_fee_bps=args.maker_fee_bps,
        min_net_edge_bps=args.min_net_edge_bps,
        max_spread_bps=args.max_spread_bps,
        min_recent_trades=args.min_recent_trades,
        max_roundtrip_seconds=args.max_roundtrip_seconds,
        max_signal_age_seconds=args.max_signal_age_seconds,
        trade_volume_participation=args.trade_volume_participation,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public-only adaptive Kraken spot grid router shadow.")
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--router-interval-seconds", type=float, default=30.0)
    parser.add_argument("--standing-bid-seconds", type=float, default=180.0)
    parser.add_argument("--max-hold-seconds", type=float, default=600.0)
    parser.add_argument("--top-n-volume", type=int, default=80)
    parser.add_argument("--lookback-seconds", type=float, default=900.0)
    parser.add_argument("--trade-count", type=int, default=1000)
    parser.add_argument("--spacing-bps", type=float, default=60.0)
    parser.add_argument("--levels", type=int, default=5)
    parser.add_argument("--entry-offset-mult", type=float, default=0.0)
    parser.add_argument("--initial-capital", type=float, default=50.0)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--liquidation-haircut-bps", type=float, default=10.0)
    parser.add_argument("--min-net-edge-bps", type=float, default=0.0)
    parser.add_argument("--max-spread-bps", type=float, default=30.0)
    parser.add_argument("--min-recent-trades", type=int, default=3)
    parser.add_argument("--max-roundtrip-seconds", type=float, default=180.0)
    parser.add_argument("--max-signal-age-seconds", type=float, default=90.0)
    parser.add_argument("--miss-cooldown-seconds", type=float, default=300.0)
    parser.add_argument("--trade-volume-participation", type=float, default=1.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--depth-count", type=int, default=20)
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--summary-path", default=str(DEFAULT_SUMMARY_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    event_path = Path(args.event_path)
    summary_path = Path(args.summary_path)
    client = KrakenSpotClient()
    pairs_by_product = {product_id_for_pair(pair): pair for pair in load_usd_pairs(client)}
    pairs_by_rest = {pair.rest_pair: pair for pair in pairs_by_product.values()}
    started_at = utc_now_iso()
    deadline = time.time() + max(0.0, float(args.duration_seconds))
    next_router_scan = 0.0
    cash = float(args.initial_capital)
    active_bid: StandingBid | None = None
    position: GridPosition | None = None
    position_last_trade_ts = 0.0
    active_product = ""
    last_book: Book | None = None
    realized_net = 0.0
    fees = 0.0
    router_scans = 0
    fire_candidates = 0
    no_fire_scans = 0
    standing_bids = 0
    bid_expirations = 0
    buys = 0
    target_closes = 0
    sweep_closes = 0
    blocked_entries = 0
    blocked_exits = 0
    product_cooldowns: dict[str, float] = {}
    append_jsonl(event_path, {"event": "adaptive_grid_start", "ts": started_at, "mode": "public_shadow_no_private_no_orders"})

    while time.time() <= deadline:
        now = time.time()
        if position is None and active_bid is None and now >= next_router_scan:
            router_scans += 1
            router_payload = build_router_payload(client, router_args_from(args))
            candidate = best_fire_candidate(router_payload, cooldowns=product_cooldowns, now=now)
            if candidate is None:
                no_fire_scans += 1
                next_router_scan = now + max(1.0, float(args.router_interval_seconds))
                append_jsonl(
                    event_path,
                    {
                        "event": "router_no_fire",
                        "ts": utc_now_iso(),
                        "router_scans": router_scans,
                        "rows_scored": router_payload.get("rows_scored"),
                        "best_product": router_payload.get("best_product"),
                    },
                )
            else:
                fire_candidates += 1
                active_bid = standing_bid_from_row(candidate, standing_seconds=float(args.standing_bid_seconds))
                active_product = active_bid.product_id
                standing_bids += 1
                next_router_scan = active_bid.expires_at
                append_jsonl(
                    event_path,
                    {
                        "event": "standing_bid_open",
                        "ts": active_bid.opened_at,
                        "product_id": active_bid.product_id,
                        "buy_price": active_bid.buy_price,
                        "target_price": active_bid.target_price,
                        "allocation_usd": active_bid.allocation_usd,
                        "router_roundtrip_seconds": candidate.get("roundtrip_seconds_to_exit"),
                        "router_spread_bps": candidate.get("spread_bps"),
                    },
                )

        if active_bid is not None and position is None:
            pair = pairs_by_rest.get(active_bid.rest_pair)
            if pair is None:
                active_bid = None
                continue
            book = parse_book(client.depth(pair.rest_pair, count=max(1, int(args.depth_count))))
            if book is not None:
                last_book = book
            trades = parse_trades(client.trades(pair.rest_pair, count=100), rest_pair=pair.rest_pair)
            fresh_trades = [trade for trade in trades if trade.ts > active_bid.last_trade_ts]
            if trades:
                active_bid.last_trade_ts = max(active_bid.last_trade_ts, max(trade.ts for trade in trades))
            fill = eligible_buy_trade_fill(
                fresh_trades,
                limit_price=active_bid.buy_price,
                quote_usd=active_bid.allocation_usd,
                participation=float(args.trade_volume_participation),
            )
            if fill.ok and cash >= active_bid.allocation_usd + active_bid.allocation_usd * float(args.maker_fee_bps) / 10000.0:
                position = position_from_bid(active_bid, maker_fee_bps=float(args.maker_fee_bps))
                position_last_trade_ts = active_bid.last_trade_ts
                cash -= position.cost_usd + position.buy_fee_usd
                fees += position.buy_fee_usd
                buys += 1
                append_jsonl(
                    event_path,
                    {
                        "event": "standing_bid_filled",
                        "ts": position.opened_at,
                        "product_id": active_bid.product_id,
                        "buy_price": position.buy_price,
                        "target_price": position.target_price,
                        "qty": position.qty,
                        "cost_usd": position.cost_usd,
                    },
                )
                active_bid = None
            elif now >= active_bid.expires_at:
                bid_expirations += 1
                product_cooldowns[active_bid.product_id] = now + max(0.0, float(args.miss_cooldown_seconds))
                append_jsonl(
                    event_path,
                    {
                        "event": "standing_bid_expired",
                        "ts": utc_now_iso(),
                        "product_id": active_bid.product_id,
                        "buy_price": active_bid.buy_price,
                        "target_price": active_bid.target_price,
                        "reason": fill.reason,
                    },
                )
                active_bid = None
                active_product = ""
                next_router_scan = now
            else:
                blocked_entries += 1

        if position is not None:
            pair = pairs_by_product.get(active_product)
            if pair is not None:
                book = parse_book(client.depth(pair.rest_pair, count=max(1, int(args.depth_count))))
                if book is not None:
                    last_book = book
                trades = parse_trades(client.trades(pair.rest_pair, count=100), rest_pair=pair.rest_pair)
                fresh_trades = [trade for trade in trades if trade.ts > position_last_trade_ts]
                if trades:
                    position_last_trade_ts = max(position_last_trade_ts, max(trade.ts for trade in trades))
                fill = eligible_sell_trade_fill(
                    fresh_trades,
                    limit_price=position.target_price,
                    qty=position.qty,
                    participation=float(args.trade_volume_participation),
                )
                if fill.ok:
                    sell_fee = fill.gross_quote * float(args.maker_fee_bps) / 10000.0
                    proceeds = fill.gross_quote - sell_fee
                    pnl = proceeds - position.cost_usd - position.buy_fee_usd
                    cash += proceeds
                    fees += sell_fee
                    realized_net += pnl
                    target_closes += 1
                    append_jsonl(
                        event_path,
                        {
                            "event": "target_close",
                            "ts": utc_now_iso(),
                            "product_id": active_product,
                            "target_price": position.target_price,
                            "qty": position.qty,
                            "net_usd": round(pnl, 8),
                            "net_bps": round((proceeds / (position.cost_usd + position.buy_fee_usd) - 1.0) * 10000.0, 6),
                        },
                    )
                    position = None
                    active_product = ""
                    next_router_scan = now
                elif now - position.opened_ts >= float(args.max_hold_seconds) and last_book is not None:
                    ok, gross, costs, proceeds, pnl, net_bps, reason = liquidate_inventory(
                        [position],
                        last_book,
                        taker_fee_bps=float(args.taker_fee_bps),
                        haircut_bps=float(args.liquidation_haircut_bps),
                    )
                    if ok and net_bps >= 0.0:
                        cash += proceeds
                        fees += costs
                        realized_net += pnl
                        sweep_closes += 1
                        append_jsonl(
                            event_path,
                            {
                                "event": "max_hold_green_sweep",
                                "ts": utc_now_iso(),
                                "product_id": active_product,
                                "gross_proceeds": round(gross, 8),
                                "liquidation_costs": round(costs, 8),
                                "net_usd": round(pnl, 8),
                                "net_bps": round(net_bps, 6),
                                "reason": reason,
                            },
                        )
                        position = None
                        active_product = ""
                        next_router_scan = now
                    else:
                        blocked_exits += 1
                else:
                    blocked_exits += 1

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                summary_payload(
                    started_at=started_at,
                    cash=cash,
                    initial_capital=float(args.initial_capital),
                    active_bid=active_bid,
                    active_product=active_product,
                    position=position,
                    book=last_book,
                    realized_net=realized_net,
                    fees=fees,
                    router_scans=router_scans,
                    fire_candidates=fire_candidates,
                    standing_bids=standing_bids,
                    bid_expirations=bid_expirations,
                    buys=buys,
                    target_closes=target_closes,
                    sweep_closes=sweep_closes,
                    blocked_entries=blocked_entries,
                    blocked_exits=blocked_exits,
                    no_fire_scans=no_fire_scans,
                    taker_fee_bps=float(args.taker_fee_bps),
                    liquidation_haircut_bps=float(args.liquidation_haircut_bps),
                ),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        time.sleep(max(0.25, float(args.poll_seconds)))

    append_jsonl(event_path, {"event": "adaptive_grid_stop", "ts": utc_now_iso()})
    print(summary_path.read_text(encoding="utf-8") if summary_path.exists() else "{}")


if __name__ == "__main__":
    main()
