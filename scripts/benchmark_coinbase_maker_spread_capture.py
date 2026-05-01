#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_piranha_candidates import _load_candles
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_maker_spread_capture_72h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_maker_spread_capture_72h.md"
PRODUCTS: tuple[str, ...] = (
    "BTC-USD",
    "ETH-USD",
    "DOGE-USD",
    "XRP-USD",
    "ADA-USD",
    "SUI-USD",
    "AVAX-USD",
    "LINK-USD",
    "SOL-USD",
)


@dataclass
class MakerLot:
    entry_bid: float
    quantity: float
    notional_usd: float
    opened_at: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark maker spread-capture feasibility on Coinbase spot.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--products", nargs="*", default=list(PRODUCTS))
    parser.add_argument("--quote-per-side", type=float, default=24.0)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=40.0)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def current_round_trip_economics(*, bid: float, ask: float, quote_per_side: float, maker_fee_bps: float) -> dict[str, float]:
    fee_per_side = quote_per_side * (maker_fee_bps / 10000.0)
    quantity = quote_per_side / bid if bid > 0.0 else 0.0
    gross_spread_capture = quantity * max(0.0, ask - bid)
    round_trip_fee = 2.0 * fee_per_side
    return {
        "gross_spread_capture_usd": gross_spread_capture,
        "round_trip_fee_usd": round_trip_fee,
        "net_spread_capture_usd": gross_spread_capture - round_trip_fee,
    }


def simulate_maker_ping_pong(
    candles: list[dict[str, Any]],
    *,
    bid: float,
    ask: float,
    starting_cash: float,
    quote_per_side: float,
    maker_fee_bps: float,
) -> dict[str, Any]:
    if not candles or bid <= 0.0 or ask <= bid:
        return {
            "proxy_realized_usd": 0.0,
            "proxy_round_trips": 0,
            "proxy_open_inventory": 0,
            "proxy_median_hold_minutes": 0.0,
            "proxy_cash_usd": starting_cash,
        }

    fee_rate = maker_fee_bps / 10000.0
    spread_abs = ask - bid
    half_spread = spread_abs / 2.0
    open_lots: list[MakerLot] = []
    realized = 0.0
    round_trips = 0
    hold_minutes: list[float] = []
    cash_usd = float(starting_cash)

    for candle in candles:
        bar_close = float(candle["close"])
        bar_low = float(candle["low"])
        bar_high = float(candle["high"])
        bar_start = int(candle["start"])

        quote_bid = max(1e-12, bar_close - half_spread)

        surviving: list[MakerLot] = []
        for lot in open_lots:
            exit_ask = lot.entry_bid + spread_abs
            if bar_high >= exit_ask:
                gross_proceeds = lot.quantity * exit_ask
                exit_fee = gross_proceeds * fee_rate
                entry_fee = lot.notional_usd * fee_rate
                cash_delta = gross_proceeds - exit_fee
                realized += cash_delta - lot.notional_usd - entry_fee
                cash_usd += cash_delta
                round_trips += 1
                hold_minutes.append(max(1.0, (bar_start - lot.opened_at) / 60.0))
            else:
                surviving.append(lot)
        open_lots = surviving

        # Same-candle ping-pong is blocked to stay conservative.
        required_cash = quote_per_side * (1.0 + fee_rate)
        if bar_low <= quote_bid and cash_usd + 1e-9 >= required_cash:
            quantity = quote_per_side / quote_bid
            cash_usd -= required_cash
            open_lots.append(
                MakerLot(
                    entry_bid=quote_bid,
                    quantity=quantity,
                    notional_usd=quote_per_side,
                    opened_at=bar_start,
                )
            )

    return {
        "proxy_realized_usd": realized,
        "proxy_round_trips": round_trips,
        "proxy_open_inventory": len(open_lots),
        "proxy_median_hold_minutes": statistics.median(hold_minutes) if hold_minutes else 0.0,
        "proxy_cash_usd": cash_usd,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "product_id",
        "bid",
        "ask",
        "spread_abs",
        "gross_spread_capture_usd",
        "round_trip_fee_usd",
        "net_spread_capture_usd",
        "proxy_realized_usd",
        "proxy_round_trips",
        "proxy_median_hold_minutes",
        "proxy_open_inventory",
        "proxy_cash_usd",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def _write_md(
    path: Path,
    *,
    hours: int,
    quote_per_side: float,
    maker_fee_bps: float,
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Maker Spread Capture Benchmark",
        "",
        f"- Window: last `{hours}h`",
        f"- Quote size per side: `${quote_per_side:.2f}`",
        f"- Maker fee assumption: `{maker_fee_bps:.1f}` bps per side",
        "- Current round-trip economics are exact from live Coinbase best bid/ask.",
        "- Historical ping-pong benchmark is a conservative candle proxy: same-candle round trips blocked, no queue priority edge assumed, no maker rebates assumed.",
        "",
        "| Product | Current Spread | Gross Spread $ | Round-Trip Fee $ | Net/RT $ | Proxy PnL | Proxy RTs | Proxy Median Hold (m) | Open Inv |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {product_id} | {spread_abs:.6f} | {gross_spread_capture_usd:.6f} | {round_trip_fee_usd:.6f} | {net_spread_capture_usd:+.6f} | {proxy_realized_usd:+.4f} | {proxy_round_trips} | {proxy_median_hold_minutes:.1f} | {proxy_open_inventory} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    products = [value.upper() for value in args.products]
    now = int(time.time())
    start_ts = now - int(args.hours) * 3600

    quotes = client.best_bid_ask(products)
    pricebooks = {row["product_id"]: row for row in (quotes.get("pricebooks") or [])}
    rows: list[dict[str, Any]] = []

    for product_id in products:
        book = pricebooks.get(product_id)
        if not book:
            continue
        bid = float(book["bids"][0]["price"])
        ask = float(book["asks"][0]["price"])
        economics = current_round_trip_economics(
            bid=bid,
            ask=ask,
            quote_per_side=float(args.quote_per_side),
            maker_fee_bps=float(args.maker_fee_bps),
        )
        candles = _load_candles(
            client,
            product_id,
            start_ts=start_ts,
            end_ts=now,
            granularity=str(args.granularity),
        )
        proxy = simulate_maker_ping_pong(
            candles,
            bid=bid,
            ask=ask,
            starting_cash=float(args.starting_cash),
            quote_per_side=float(args.quote_per_side),
            maker_fee_bps=float(args.maker_fee_bps),
        )
        rows.append(
            {
                "product_id": product_id,
                "bid": bid,
                "ask": ask,
                "spread_abs": ask - bid,
                **economics,
                **proxy,
            }
        )

    rows.sort(key=lambda row: row["net_spread_capture_usd"], reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, rows)
    _write_md(
        md_path,
        hours=int(args.hours),
        quote_per_side=float(args.quote_per_side),
        maker_fee_bps=float(args.maker_fee_bps),
        rows=rows,
    )
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
