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
from kraken_config import DEFAULT_MAKER_FEE_BPS, DEFAULT_TAKER_FEE_BPS  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair  # noqa: E402
from run_kraken_vulture_trigger_tape import Book, Fill, Level, parse_book, sell_fill_for_qty, spread_bps  # noqa: E402


DEFAULT_PRODUCT = "BLEND-USD"
DEFAULT_EVENT_PATH = REPORTS / "kraken_grid_shadow_tape_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_grid_shadow_tape_summary.json"


@dataclass
class GridPosition:
    level: int
    buy_price: float
    target_price: float
    qty: float
    cost_usd: float
    buy_fee_usd: float
    opened_at: str
    opened_ts: float


@dataclass
class TradePrint:
    price: float
    size: float
    ts: float
    side: str
    trade_id: int | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def parse_csv(raw: str) -> list[str]:
    return [part.strip().upper() for part in str(raw or "").split(",") if part.strip()]


def normalize_product(product: str) -> str:
    return str(product or "").upper().replace("/", "-")


def load_pairs(client: KrakenSpotClient, products: list[str]) -> dict[str, KrakenPair]:
    wanted = {normalize_product(product) for product in products}
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


def eligible_buy_fill(asks: list[Level], limit_price: float, quote_usd: float) -> Fill:
    eligible = [level for level in asks if level.price <= limit_price]
    if not eligible:
        return Fill(False, 0.0, 0.0, 0.0, 0.0, "limit_not_crossed")
    remaining = max(0.0, float(quote_usd))
    qty = 0.0
    spent = 0.0
    depth_notional = sum(level.price * level.size for level in eligible)
    for level in eligible:
        if remaining <= 0.0:
            break
        take_qty = min(level.size, remaining / level.price)
        qty += take_qty
        spent += take_qty * level.price
        remaining -= take_qty * level.price
    if remaining > max(0.000001, quote_usd * 0.000001):
        return Fill(False, qty, spent, spent / qty if qty > 0.0 else 0.0, depth_notional, "insufficient_crossed_ask_depth")
    return Fill(True, qty, spent, spent / qty if qty > 0.0 else 0.0, depth_notional, "maker_buy_crossed")


def eligible_sell_fill(bids: list[Level], limit_price: float, qty: float) -> Fill:
    eligible = [level for level in bids if level.price >= limit_price]
    if not eligible:
        return Fill(False, 0.0, 0.0, 0.0, 0.0, "limit_not_crossed")
    return sell_fill_for_qty(eligible, qty)


def parse_trades(trades_payload: dict[str, Any], *, rest_pair: str) -> list[TradePrint]:
    if not isinstance(trades_payload, dict):
        return []
    rows = trades_payload.get(rest_pair)
    if rows is None:
        rows = next((value for key, value in trades_payload.items() if key != "last" and isinstance(value, list)), [])
    trades: list[TradePrint] = []
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 4:
            continue
        try:
            price = float(row[0])
            size = float(row[1])
            ts = float(row[2])
        except (TypeError, ValueError):
            continue
        side = str(row[3]).lower()
        trade_id = None
        if len(row) >= 7:
            try:
                trade_id = int(row[6])
            except (TypeError, ValueError):
                trade_id = None
        if price > 0.0 and size > 0.0 and ts > 0.0:
            trades.append(TradePrint(price=price, size=size, ts=ts, side=side, trade_id=trade_id))
    trades.sort(key=lambda trade: (trade.ts, trade.trade_id or 0))
    return trades


def _consume_trade_tape_qty(
    trades: list[TradePrint],
    *,
    limit_price: float,
    qty: float,
    side: str,
    price_at_or_better: str,
    participation: float,
) -> Fill:
    effective_participation = min(1.0, max(0.0, float(participation)))
    if effective_participation <= 0.0:
        return Fill(False, 0.0, 0.0, 0.0, 0.0, "zero_trade_tape_participation")
    required_qty = max(0.0, float(qty))
    if required_qty <= 0.0:
        return Fill(False, 0.0, 0.0, 0.0, 0.0, "zero_qty")

    def price_ok(price: float) -> bool:
        if price_at_or_better == "below":
            return price <= limit_price
        return price >= limit_price

    eligible = [trade for trade in trades if trade.side == side and price_ok(trade.price)]
    depth_notional = sum(trade.price * trade.size for trade in eligible)
    if not eligible:
        return Fill(False, 0.0, 0.0, 0.0, depth_notional, "trade_tape_not_touched")
    fillable_qty = sum(trade.size * effective_participation for trade in eligible)
    if fillable_qty + 1e-12 < required_qty:
        return Fill(False, fillable_qty, fillable_qty * limit_price, limit_price, depth_notional, "insufficient_trade_tape_qty")

    remaining = required_qty
    for trade in eligible:
        if remaining <= 0.0:
            break
        effective_available = trade.size * effective_participation
        take_effective = min(effective_available, remaining)
        trade.size = max(0.0, trade.size - take_effective / effective_participation)
        remaining -= take_effective
    gross_quote = required_qty * limit_price
    return Fill(True, required_qty, gross_quote, limit_price, depth_notional, "trade_tape_filled")


def eligible_buy_trade_fill(
    trades: list[TradePrint],
    *,
    limit_price: float,
    quote_usd: float,
    participation: float,
) -> Fill:
    qty = max(0.0, float(quote_usd)) / limit_price if limit_price > 0.0 else 0.0
    return _consume_trade_tape_qty(
        trades,
        limit_price=limit_price,
        qty=qty,
        side="s",
        price_at_or_better="below",
        participation=participation,
    )


def eligible_sell_trade_fill(
    trades: list[TradePrint],
    *,
    limit_price: float,
    qty: float,
    participation: float,
) -> Fill:
    return _consume_trade_tape_qty(
        trades,
        limit_price=limit_price,
        qty=qty,
        side="b",
        price_at_or_better="above",
        participation=participation,
    )


def min_size_blockers(pair: KrakenPair, quote_usd: float, qty: float) -> list[str]:
    blockers: list[str] = []
    if quote_usd < pair.cost_min:
        blockers.append("below_cost_min")
    if qty < pair.order_min:
        blockers.append("below_order_min")
    return blockers


def build_buy_price(anchor: float, spacing_bps: float, level: int, entry_offset_mult: float) -> float:
    spacing = spacing_bps / 10000.0
    return anchor * (1.0 - max(0.0, entry_offset_mult) * spacing - (level - 1) * spacing)


def mark_inventory(positions: list[GridPosition], book: Book, *, taker_fee_bps: float, haircut_bps: float) -> tuple[float, float, float]:
    if not positions:
        return 0.0, 0.0, 0.0
    total_qty = sum(pos.qty for pos in positions)
    fill = sell_fill_for_qty(book.bids, total_qty)
    if not fill.ok:
        return 0.0, -sum(pos.cost_usd + pos.buy_fee_usd for pos in positions), 0.0
    cost_bps = max(0.0, taker_fee_bps + haircut_bps) / 10000.0
    costs = fill.gross_quote * cost_bps
    net = fill.gross_quote - costs
    pnl = net - sum(pos.cost_usd + pos.buy_fee_usd for pos in positions)
    return net, pnl, costs


def liquidate_inventory(
    positions: list[GridPosition],
    book: Book,
    *,
    taker_fee_bps: float,
    haircut_bps: float,
) -> tuple[bool, float, float, float, float, float, str]:
    total_qty = sum(pos.qty for pos in positions)
    fill = sell_fill_for_qty(book.bids, total_qty)
    if not fill.ok:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, fill.reason
    cost_bps = max(0.0, taker_fee_bps + haircut_bps) / 10000.0
    costs = fill.gross_quote * cost_bps
    net = fill.gross_quote - costs
    cost_basis = sum(pos.cost_usd + pos.buy_fee_usd for pos in positions)
    pnl = net - cost_basis
    net_bps = (net / cost_basis - 1.0) * 10000.0 if cost_basis > 0.0 else 0.0
    return True, fill.gross_quote, costs, net, pnl, net_bps, "liquidated"


def state_payload(
    *,
    product_id: str,
    cash: float,
    positions: list[GridPosition],
    book: Book | None,
    initial_capital: float,
    realized_net: float,
    fees: float,
    buys: int,
    target_closes: int,
    sweep_closes: int,
    sweep_count: int,
    blocked_entries: int,
    blocked_exits: int,
    blocked_entry_reasons: dict[str, int],
    started_at: str,
    taker_fee_bps: float,
    liquidation_haircut_bps: float,
    anchor: float | None,
    spacing_bps: float,
    levels: int,
    entry_offset_mult: float,
) -> dict[str, Any]:
    inventory_value = 0.0
    inventory_pnl = 0.0
    inventory_mark_costs = 0.0
    if book is not None:
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
        "product_id": product_id,
        "cash": round(cash, 8),
        "open_positions": len(positions),
        "open_inventory_value": round(inventory_value, 8),
        "open_inventory_pnl": round(inventory_pnl, 8),
        "open_inventory_mark_costs": round(inventory_mark_costs, 8),
        "equity": round(equity, 8),
        "return_pct": round((equity / initial_capital - 1.0) * 100.0, 6) if initial_capital > 0.0 else 0.0,
        "realized_net_usd": round(realized_net, 8),
        "fees_usd": round(fees, 8),
        "buys": buys,
        "target_closes": target_closes,
        "sweep_closes": sweep_closes,
        "sweep_count": sweep_count,
        "blocked_entries": blocked_entries,
        "blocked_exits": blocked_exits,
        "blocked_entry_reasons": dict(sorted(blocked_entry_reasons.items())),
        "anchor": round(anchor, 12) if anchor is not None else 0.0,
        "next_buy_prices": [
            round(build_buy_price(float(anchor), float(spacing_bps), level, float(entry_offset_mult)), 12)
            for level in range(1, int(levels) + 1)
        ]
        if anchor is not None
        else [],
        "last_bid": round(book.bid, 12) if book else 0.0,
        "last_ask": round(book.ask, 12) if book else 0.0,
        "last_spread_bps": round(spread_bps(book.bid, book.ask), 6) if book else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public-only forward Kraken spot grid shadow tape.")
    parser.add_argument("--products", default=DEFAULT_PRODUCT)
    parser.add_argument("--spacing-bps", type=float, default=200.0)
    parser.add_argument("--levels", type=int, default=3)
    parser.add_argument("--entry-offset-mult", type=float, default=1.0)
    parser.add_argument("--initial-capital", type=float, default=100.0)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--liquidation-haircut-bps", type=float, default=10.0)
    parser.add_argument("--sweep-min-inventory-net-bps", type=float, default=0.0)
    parser.add_argument("--sweep-max-inventory-pct", type=float, default=50.0)
    parser.add_argument("--max-spread-bps", type=float, default=80.0)
    parser.add_argument("--min-depth-usd", type=float, default=5.0)
    parser.add_argument("--fill-source", choices=["book", "trade_tape", "book_or_trade"], default="book")
    parser.add_argument("--trade-volume-participation", type=float, default=1.0)
    parser.add_argument("--trade-lookback-seconds", type=float, default=10.0)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--depth-count", type=int, default=20)
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--summary-path", default=str(DEFAULT_SUMMARY_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    event_path = Path(args.event_path)
    summary_path = Path(args.summary_path)
    products = parse_csv(args.products)
    if len(products) != 1:
        raise SystemExit("This first grid shadow supports exactly one product per process.")
    product_id = products[0]
    client = KrakenSpotClient()
    pairs = load_pairs(client, [product_id])
    pair = pairs.get(product_id)
    if pair is None:
        raise SystemExit(f"Product not found or not online: {product_id}")

    started_at = utc_now_iso()
    deadline = time.time() + max(0.0, float(args.duration_seconds))
    cash = float(args.initial_capital)
    allocation = float(args.initial_capital) / max(1, int(args.levels))
    positions: list[GridPosition] = []
    anchor: float | None = None
    realized_net = 0.0
    fees = 0.0
    buys = 0
    target_closes = 0
    sweep_closes = 0
    sweep_count = 0
    blocked_entries = 0
    blocked_exits = 0
    blocked_entry_reasons: dict[str, int] = {}
    last_book: Book | None = None
    last_trade_ts = max(0.0, time.time() - max(0.0, float(args.trade_lookback_seconds)))

    append_jsonl(
        event_path,
        {
            "event": "grid_shadow_start",
            "ts": started_at,
            "product_id": product_id,
            "rest_pair": pair.rest_pair,
            "spacing_bps": args.spacing_bps,
            "levels": args.levels,
            "initial_capital": args.initial_capital,
            "fill_source": args.fill_source,
            "trade_volume_participation": args.trade_volume_participation,
            "mode": "public_shadow_no_private_no_orders",
        },
    )

    while time.time() <= deadline:
        book = parse_book(client.depth(pair.rest_pair, count=max(1, int(args.depth_count))))
        if book is None:
            time.sleep(max(0.1, float(args.poll_seconds)))
            continue
        last_book = book
        trade_prints: list[TradePrint] = []
        if args.fill_source in {"trade_tape", "book_or_trade"}:
            trade_payload = client.trades(pair.rest_pair, count=100)
            all_trades = parse_trades(trade_payload, rest_pair=pair.rest_pair)
            trade_prints = [trade for trade in all_trades if trade.ts > last_trade_ts]
            if all_trades:
                last_trade_ts = max(last_trade_ts, max(trade.ts for trade in all_trades))
        current_spread = spread_bps(book.bid, book.ask)
        mid = (book.bid + book.ask) / 2.0
        if anchor is None or not positions:
            anchor = mid

        # Target maker exits first.
        remaining: list[GridPosition] = []
        for pos in positions:
            if args.fill_source == "trade_tape":
                fill = eligible_sell_trade_fill(
                    trade_prints,
                    limit_price=pos.target_price,
                    qty=pos.qty,
                    participation=float(args.trade_volume_participation),
                )
            elif args.fill_source == "book_or_trade":
                fill = eligible_sell_fill(book.bids, pos.target_price, pos.qty)
                if not fill.ok:
                    fill = eligible_sell_trade_fill(
                        trade_prints,
                        limit_price=pos.target_price,
                        qty=pos.qty,
                        participation=float(args.trade_volume_participation),
                    )
            else:
                fill = eligible_sell_fill(book.bids, pos.target_price, pos.qty)
            if fill.ok:
                sell_fee = fill.gross_quote * float(args.maker_fee_bps) / 10000.0
                proceeds = fill.gross_quote - sell_fee
                pnl = proceeds - pos.cost_usd - pos.buy_fee_usd
                cash += proceeds
                fees += sell_fee
                realized_net += pnl
                target_closes += 1
                append_jsonl(
                    event_path,
                    {
                        "event": "grid_target_close",
                        "ts": utc_now_iso(),
                        "product_id": product_id,
                        "level": pos.level,
                        "buy_price": pos.buy_price,
                        "target_price": pos.target_price,
                        "fill_avg_price": fill.avg_price,
                        "qty": fill.qty,
                        "net_usd": round(pnl, 8),
                        "net_bps": round((proceeds / (pos.cost_usd + pos.buy_fee_usd) - 1.0) * 10000.0, 6),
                    },
                )
            else:
                blocked_exits += 1
                remaining.append(pos)
        positions = remaining

        # Green inventory retirement when exposure is high enough.
        open_cost = sum(pos.cost_usd + pos.buy_fee_usd for pos in positions)
        open_inventory_pct = open_cost / float(args.initial_capital) * 100.0 if args.initial_capital > 0.0 else 0.0
        if positions and open_inventory_pct >= float(args.sweep_max_inventory_pct):
            ok, gross_proceeds, liquidation_costs, proceeds, pnl, net_bps, reason = liquidate_inventory(
                positions,
                book,
                taker_fee_bps=float(args.taker_fee_bps),
                haircut_bps=float(args.liquidation_haircut_bps),
            )
            if ok and net_bps >= float(args.sweep_min_inventory_net_bps):
                cash += proceeds
                fees += liquidation_costs
                realized_net += pnl
                sweep_closes += len(positions)
                sweep_count += 1
                append_jsonl(
                    event_path,
                    {
                        "event": "grid_inventory_sweep",
                        "ts": utc_now_iso(),
                        "product_id": product_id,
                        "positions_closed": len(positions),
                        "gross_proceeds": round(gross_proceeds, 8),
                        "liquidation_costs": round(liquidation_costs, 8),
                        "proceeds": round(proceeds, 8),
                        "net_usd": round(pnl, 8),
                        "net_bps": round(net_bps, 6),
                        "reason": reason,
                    },
                )
                positions = []

        # New entries.
        if current_spread <= float(args.max_spread_bps):
            open_levels = {pos.level for pos in positions}
            for level in range(1, int(args.levels) + 1):
                if level in open_levels:
                    continue
                buy_price = build_buy_price(anchor, float(args.spacing_bps), level, float(args.entry_offset_mult))
                if buy_price <= 0.0:
                    continue
                buy_fee = allocation * float(args.maker_fee_bps) / 10000.0
                if cash + 1e-12 < allocation + buy_fee:
                    continue
                if args.fill_source == "trade_tape":
                    fill = eligible_buy_trade_fill(
                        trade_prints,
                        limit_price=buy_price,
                        quote_usd=allocation,
                        participation=float(args.trade_volume_participation),
                    )
                elif args.fill_source == "book_or_trade":
                    fill = eligible_buy_fill(book.asks, buy_price, allocation)
                    if not fill.ok:
                        fill = eligible_buy_trade_fill(
                            trade_prints,
                            limit_price=buy_price,
                            quote_usd=allocation,
                            participation=float(args.trade_volume_participation),
                        )
                else:
                    fill = eligible_buy_fill(book.asks, buy_price, allocation)
                blockers = min_size_blockers(pair, allocation, fill.qty if fill.ok else allocation / buy_price)
                if not fill.ok or blockers or fill.depth_notional < float(args.min_depth_usd):
                    blocked_entries += 1
                    reasons = list(blockers)
                    if not fill.ok:
                        reasons.append(fill.reason)
                    if fill.ok and fill.depth_notional < float(args.min_depth_usd):
                        reasons.append("below_min_depth_usd")
                    for reason in reasons or ["unknown"]:
                        blocked_entry_reasons[reason] = blocked_entry_reasons.get(reason, 0) + 1
                    continue
                qty = allocation / buy_price
                cash -= allocation + buy_fee
                fees += buy_fee
                buys += 1
                pos = GridPosition(
                    level=level,
                    buy_price=buy_price,
                    target_price=buy_price * (1.0 + float(args.spacing_bps) / 10000.0),
                    qty=qty,
                    cost_usd=allocation,
                    buy_fee_usd=buy_fee,
                    opened_at=utc_now_iso(),
                    opened_ts=time.time(),
                )
                positions.append(pos)
                append_jsonl(
                    event_path,
                    {
                        "event": "grid_entry_fill_proxy",
                        "ts": pos.opened_at,
                        "product_id": product_id,
                        "level": level,
                        "buy_price": round(buy_price, 12),
                        "target_price": round(pos.target_price, 12),
                        "qty": round(qty, 12),
                        "allocation": round(allocation, 8),
                        "fill_avg_price": round(fill.avg_price, 12),
                        "spread_bps": round(current_spread, 6),
                    },
                )

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                state_payload(
                    product_id=product_id,
                    cash=cash,
                    positions=positions,
                    book=book,
                    initial_capital=float(args.initial_capital),
                    realized_net=realized_net,
                    fees=fees,
                    buys=buys,
                    target_closes=target_closes,
                    sweep_closes=sweep_closes,
                    sweep_count=sweep_count,
                    blocked_entries=blocked_entries,
                    blocked_exits=blocked_exits,
                    blocked_entry_reasons=blocked_entry_reasons,
                    started_at=started_at,
                    taker_fee_bps=float(args.taker_fee_bps),
                    liquidation_haircut_bps=float(args.liquidation_haircut_bps),
                    anchor=anchor,
                    spacing_bps=float(args.spacing_bps),
                    levels=int(args.levels),
                    entry_offset_mult=float(args.entry_offset_mult),
                ),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        time.sleep(max(0.1, float(args.poll_seconds)))

    if last_book is not None:
        summary_path.write_text(
            json.dumps(
                state_payload(
                    product_id=product_id,
                    cash=cash,
                    positions=positions,
                    book=last_book,
                    initial_capital=float(args.initial_capital),
                    realized_net=realized_net,
                    fees=fees,
                    buys=buys,
                    target_closes=target_closes,
                    sweep_closes=sweep_closes,
                    sweep_count=sweep_count,
                    blocked_entries=blocked_entries,
                    blocked_exits=blocked_exits,
                    blocked_entry_reasons=blocked_entry_reasons,
                    started_at=started_at,
                    taker_fee_bps=float(args.taker_fee_bps),
                    liquidation_haircut_bps=float(args.liquidation_haircut_bps),
                    anchor=anchor,
                    spacing_bps=float(args.spacing_bps),
                    levels=int(args.levels),
                    entry_offset_mult=float(args.entry_offset_mult),
                ),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    append_jsonl(event_path, {"event": "grid_shadow_stop", "ts": utc_now_iso(), "product_id": product_id})
    print(summary_path.read_text(encoding="utf-8") if summary_path.exists() else "{}")


if __name__ == "__main__":
    main()
