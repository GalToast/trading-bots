#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_piranha_candidates.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_piranha_candidates.md"


@dataclass(frozen=True)
class ProductConfig:
    product_id: str
    buy_step: float
    profit_target: float


@dataclass
class Lot:
    entry_price: float
    quantity: float
    cost_usd: float
    opened_at: int


PRODUCT_CONFIGS: tuple[ProductConfig, ...] = (
    ProductConfig("XRP-USD", buy_step=0.015, profit_target=0.025),
    ProductConfig("DOGE-USD", buy_step=0.0013, profit_target=0.0018),
    ProductConfig("ADA-USD", buy_step=0.0030, profit_target=0.0045),
    ProductConfig("SUI-USD", buy_step=0.0120, profit_target=0.0180),
    ProductConfig("LINK-USD", buy_step=0.11, profit_target=0.16),
    ProductConfig("AVAX-USD", buy_step=0.11, profit_target=0.16),
    ProductConfig("SOL-USD", buy_step=0.50, profit_target=0.80),
    ProductConfig("ETH-USD", buy_step=26.0, profit_target=34.0),
    ProductConfig("BTC-USD", buy_step=850.0, profit_target=1100.0),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Coinbase spot piranha candidates using official Coinbase candles.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--quote-per-buy", type=float, default=6.0)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-lots", type=int, default=6)
    parser.add_argument("--taker-fee-bps", type=float, default=60.0)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--products", nargs="*", help="Optional subset of product ids to benchmark.")
    return parser.parse_args()


def _load_candles(
    client: CoinbaseAdvancedClient,
    product_id: str,
    *,
    start_ts: int,
    end_ts: int,
    granularity: str,
    chunk_seconds: int = 300 * 60,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = start_ts
    while cursor < end_ts:
        chunk_end = min(end_ts, cursor + chunk_seconds)
        payload = client.market_candles(product_id, start=cursor, end=chunk_end, granularity=granularity, limit=350)
        rows.extend(payload.get("candles") or [])
        cursor = chunk_end
    dedup: dict[int, dict[str, Any]] = {}
    for row in rows:
        start = int(row["start"])
        dedup[start] = {
            "start": start,
            "low": float(row["low"]),
            "high": float(row["high"]),
            "open": float(row["open"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0) or 0.0),
        }
    return [dedup[key] for key in sorted(dedup.keys())]


def _simulate_candle_proxy(
    candles: list[dict[str, Any]],
    *,
    buy_step: float,
    profit_target: float,
    quote_per_buy: float,
    starting_cash: float,
    max_lots: int,
    taker_fee_bps: float,
) -> dict[str, Any]:
    if not candles:
        return {
            "realized_net_usd": 0.0,
            "realized_closes": 0,
            "open_lots": 0,
            "cash_usd": starting_cash,
            "median_hold_minutes": 0.0,
            "mean_hold_minutes": 0.0,
            "same_candle_exits_blocked": True,
        }

    fee_rate = taker_fee_bps / 10000.0
    cash_usd = float(starting_cash)
    anchor = float(candles[0]["close"])
    next_buy = anchor - buy_step
    open_lots: list[Lot] = []
    realized_net = 0.0
    closes = 0
    hold_minutes: list[float] = []

    for candle in candles:
        low = float(candle["low"])
        high = float(candle["high"])
        close = float(candle["close"])
        bar_start = int(candle["start"])

        # Conservative ordering: older lots may close, but newly opened lots cannot
        # round-trip inside the same candle.
        surviving: list[Lot] = []
        for lot in open_lots:
            target = lot.entry_price + profit_target
            if high >= target:
                proceeds = lot.quantity * target
                exit_fee = proceeds * fee_rate
                realized_net += proceeds - exit_fee - lot.cost_usd
                cash_usd += proceeds - exit_fee
                closes += 1
                hold_minutes.append(max(1.0, (bar_start - lot.opened_at) / 60.0))
            else:
                surviving.append(lot)
        open_lots = surviving

        while low <= next_buy and len(open_lots) < max_lots:
            required_cash = quote_per_buy * (1.0 + fee_rate)
            if cash_usd + 1e-9 < required_cash:
                break
            entry_price = next_buy
            quantity = quote_per_buy / entry_price
            open_lots.append(
                Lot(
                    entry_price=entry_price,
                    quantity=quantity,
                    cost_usd=required_cash,
                    opened_at=bar_start,
                )
            )
            cash_usd -= required_cash
            next_buy -= buy_step

        if not open_lots and abs(close - anchor) >= buy_step:
            anchor = close
            next_buy = anchor - buy_step

    median_hold = statistics.median(hold_minutes) if hold_minutes else 0.0
    mean_hold = statistics.fmean(hold_minutes) if hold_minutes else 0.0
    return {
        "realized_net_usd": realized_net,
        "realized_closes": closes,
        "open_lots": len(open_lots),
        "cash_usd": cash_usd,
        "median_hold_minutes": median_hold,
        "mean_hold_minutes": mean_hold,
        "same_candle_exits_blocked": True,
    }


def _fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "product_id",
        "bid",
        "ask",
        "spread",
        "breakeven_move_pct",
        "buy_step",
        "profit_target",
        "median_range_pct",
        "p90_range_pct",
        "candles_over_fee_floor_pct",
        "sim_realized_usd",
        "sim_closes",
        "sim_median_hold_minutes",
        "sim_open_lots",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def _write_md(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    hours: int,
    granularity: str,
    quote_per_buy: float,
    taker_fee_bps: float,
    starting_cash: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Piranha Candidates",
        "",
        f"- Window: last `{hours}h`",
        f"- Candles: `{granularity}` via Coinbase brokerage market candles endpoint",
        f"- Quote per buy: `${quote_per_buy:.2f}`",
        f"- Starting cash: `${starting_cash:.2f}`",
        f"- Fee model: `{taker_fee_bps:.1f}` bps per side",
        "- Important: simulation is a conservative candle proxy, not historical tick replay.",
        "- Candle simulation blocks same-candle round-trip exits.",
        "",
        "| Product | Spread | Fee Floor % | Buy Step | Target | Median 1m Range % | P90 1m Range % | Candles > Fee Floor | Sim PnL | Closes | Median Hold (m) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {product_id} | {spread:.6f} | {breakeven_move_pct:.3f}% | {buy_step:.6f} | {profit_target:.6f} | {median_range_pct:.3f}% | {p90_range_pct:.3f}% | {candles_over_fee_floor_pct:.1f}% | {sim_realized_usd:+.2f} | {sim_closes} | {sim_median_hold_minutes:.1f} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_ts = now - int(args.hours) * 3600
    selected = {value.upper() for value in (args.products or [])}
    configs = [cfg for cfg in PRODUCT_CONFIGS if not selected or cfg.product_id in selected]
    if not configs:
        raise SystemExit("No product configs selected.")

    quotes = client.best_bid_ask([cfg.product_id for cfg in configs])
    pricebooks = {row["product_id"]: row for row in (quotes.get("pricebooks") or [])}
    fee_rate = args.taker_fee_bps / 10000.0
    results: list[dict[str, Any]] = []

    for cfg in configs:
        candles = _load_candles(
            client,
            cfg.product_id,
            start_ts=start_ts,
            end_ts=now,
            granularity=args.granularity,
        )
        if not candles:
            continue
        pricebook = pricebooks.get(cfg.product_id)
        if not pricebook:
            continue
        bid = float(pricebook["bids"][0]["price"])
        ask = float(pricebook["asks"][0]["price"])
        qty = float(args.quote_per_buy) / ask
        gross_cost = float(args.quote_per_buy) * (1.0 + fee_rate)
        break_even_bid = gross_cost / (qty * (1.0 - fee_rate))
        break_even_move_pct = ((break_even_bid - ask) / ask) * 100.0

        range_pcts = [
            ((float(row["high"]) - float(row["low"])) / max(float(row["open"]), 1e-12)) * 100.0
            for row in candles
            if float(row["open"]) > 0.0
        ]
        median_range_pct = statistics.median(range_pcts) if range_pcts else 0.0
        p90_range_pct = statistics.quantiles(range_pcts, n=10)[8] if len(range_pcts) >= 10 else (max(range_pcts) if range_pcts else 0.0)
        candles_over_fee_floor_pct = (
            (sum(1 for value in range_pcts if value >= break_even_move_pct) / len(range_pcts)) * 100.0 if range_pcts else 0.0
        )

        sim = _simulate_candle_proxy(
            candles,
            buy_step=cfg.buy_step,
            profit_target=cfg.profit_target,
            quote_per_buy=float(args.quote_per_buy),
            starting_cash=float(args.starting_cash),
            max_lots=int(args.max_lots),
            taker_fee_bps=float(args.taker_fee_bps),
        )
        results.append(
            {
                "product_id": cfg.product_id,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "breakeven_move_pct": break_even_move_pct,
                "buy_step": cfg.buy_step,
                "profit_target": cfg.profit_target,
                "median_range_pct": median_range_pct,
                "p90_range_pct": p90_range_pct,
                "candles_over_fee_floor_pct": candles_over_fee_floor_pct,
                "sim_realized_usd": sim["realized_net_usd"],
                "sim_closes": sim["realized_closes"],
                "sim_median_hold_minutes": sim["median_hold_minutes"],
                "sim_open_lots": sim["open_lots"],
            }
        )

    results.sort(key=lambda row: (row["sim_realized_usd"], row["candles_over_fee_floor_pct"]), reverse=True)

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, results)
    _write_md(
        md_path,
        results,
        hours=int(args.hours),
        granularity=str(args.granularity),
        quote_per_buy=float(args.quote_per_buy),
        taker_fee_bps=float(args.taker_fee_bps),
        starting_cash=float(args.starting_cash),
    )
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
