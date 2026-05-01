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

from benchmark_coinbase_spot_piranha_candidates import _load_candles, _simulate_candle_proxy
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_tactics_72h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_tactics_72h.md"


@dataclass(frozen=True)
class Candle:
    start: int
    low: float
    high: float
    open: float
    close: float
    volume: float


@dataclass
class Position:
    product_id: str
    entry_price: float
    quantity: float
    notional_usd: float
    entry_time: int
    hold_bars: int = 0


PRODUCTS: tuple[str, ...] = (
    "XRP-USD",
    "DOGE-USD",
    "ADA-USD",
    "SUI-USD",
    "LINK-USD",
    "AVAX-USD",
    "SOL-USD",
    "ETH-USD",
    "BTC-USD",
)

SCAVENGER_CONFIGS: dict[str, tuple[float, float]] = {
    "XRP-USD": (0.0150, 0.0250),
    "DOGE-USD": (0.0013, 0.0018),
    "ADA-USD": (0.0030, 0.0045),
    "SUI-USD": (0.0120, 0.0180),
    "LINK-USD": (0.11, 0.16),
    "AVAX-USD": (0.11, 0.16),
    "SOL-USD": (0.50, 0.80),
    "ETH-USD": (26.0, 34.0),
    "BTC-USD": (850.0, 1100.0),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark multiple Coinbase spot tactic designs.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--quote-per-buy", type=float, default=6.0)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--products", nargs="*", default=list(PRODUCTS))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def _to_candles(rows: list[dict[str, Any]]) -> list[Candle]:
    return [
        Candle(
            start=int(row["start"]),
            low=float(row["low"]),
            high=float(row["high"]),
            open=float(row["open"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0) or 0.0),
        )
        for row in rows
    ]


def _load_market(
    client: CoinbaseAdvancedClient,
    products: list[str],
    *,
    start_ts: int,
    end_ts: int,
    granularity: str,
) -> tuple[dict[str, list[Candle]], dict[str, dict[int, Candle]], list[int]]:
    by_product: dict[str, list[Candle]] = {}
    by_time: dict[str, dict[int, Candle]] = {}
    common_times: set[int] | None = None
    for product_id in products:
        candles = _to_candles(
            _load_candles(client, product_id, start_ts=start_ts, end_ts=end_ts, granularity=granularity)
        )
        by_product[product_id] = candles
        product_by_time = {candle.start: candle for candle in candles}
        by_time[product_id] = product_by_time
        if common_times is None:
            common_times = set(product_by_time.keys())
        else:
            common_times &= set(product_by_time.keys())
    ordered_times = sorted(common_times or [])
    return by_product, by_time, ordered_times


def _exit_position(
    *,
    position: Position,
    exit_price: float,
    fee_rate: float,
) -> tuple[float, float]:
    proceeds = position.quantity * exit_price
    exit_fee = proceeds * fee_rate
    cash_delta = proceeds - exit_fee
    realized = cash_delta - position.notional_usd
    return cash_delta, realized


def simulate_breakout_continuation(
    candles: list[Candle],
    *,
    starting_cash: float,
    fee_bps: float,
    breakout_pct: float = 0.0045,
    close_strength_pct: float = 0.0025,
    take_profit_pct: float = 0.012,
    stop_loss_pct: float = 0.007,
    max_hold_bars: int = 90,
) -> dict[str, Any]:
    if len(candles) < 3:
        return {"realized_net_usd": 0.0, "trades": 0, "median_hold_minutes": 0.0, "cash_usd": starting_cash}

    fee_rate = fee_bps / 10000.0
    cash_usd = starting_cash
    position: Position | None = None
    realized = 0.0
    trades = 0
    hold_minutes: list[float] = []

    for idx in range(1, len(candles)):
        candle = candles[idx]
        prev = candles[idx - 1]

        if position is not None:
            position.hold_bars += 1
            stop_price = position.entry_price * (1.0 - stop_loss_pct)
            target_price = position.entry_price * (1.0 + take_profit_pct)
            exit_price: float | None = None
            if candle.low <= stop_price:
                exit_price = stop_price
            elif candle.high >= target_price:
                exit_price = target_price
            elif position.hold_bars >= max_hold_bars:
                exit_price = candle.close
            if exit_price is not None:
                cash_delta, pnl = _exit_position(position=position, exit_price=exit_price, fee_rate=fee_rate)
                cash_usd += cash_delta
                realized += pnl
                trades += 1
                hold_minutes.append(max(1.0, (candle.start - position.entry_time) / 60.0))
                position = None

        if position is None and idx >= 1:
            breakout = (candle.close / prev.close) - 1.0
            close_strength = (candle.close / candle.open) - 1.0
            if breakout >= breakout_pct and close_strength >= close_strength_pct:
                entry_price = candle.close
                entry_fee = cash_usd * fee_rate
                notional = cash_usd
                spendable = cash_usd - entry_fee
                if spendable > 0.0:
                    quantity = spendable / entry_price
                    cash_usd = 0.0
                    position = Position(
                        product_id="",
                        entry_price=entry_price,
                        quantity=quantity,
                        notional_usd=notional,
                        entry_time=candle.start,
                    )

    if position is not None:
        last = candles[-1]
        cash_delta, pnl = _exit_position(position=position, exit_price=last.close, fee_rate=fee_rate)
        cash_usd += cash_delta
        realized += pnl
        trades += 1
        hold_minutes.append(max(1.0, (last.start - position.entry_time) / 60.0))

    return {
        "realized_net_usd": realized,
        "trades": trades,
        "median_hold_minutes": statistics.median(hold_minutes) if hold_minutes else 0.0,
        "cash_usd": cash_usd,
    }


def simulate_rotation(
    by_time: dict[str, dict[int, Candle]],
    ordered_times: list[int],
    *,
    starting_cash: float,
    fee_bps: float,
    lookback_bars: int = 30,
    min_strength_pct: float = 0.006,
    leader_gap_pct: float = 0.002,
    stop_loss_pct: float = 0.009,
    take_profit_pct: float = 0.018,
    max_hold_bars: int = 180,
) -> dict[str, Any]:
    if len(ordered_times) <= lookback_bars:
        return {"realized_net_usd": 0.0, "trades": 0, "median_hold_minutes": 0.0, "cash_usd": starting_cash}

    fee_rate = fee_bps / 10000.0
    cash_usd = starting_cash
    position: Position | None = None
    realized = 0.0
    trades = 0
    hold_minutes: list[float] = []

    for idx in range(lookback_bars, len(ordered_times)):
        current_time = ordered_times[idx]
        previous_time = ordered_times[idx - lookback_bars]

        scores: list[tuple[float, str]] = []
        for product_id, product_candles in by_time.items():
            current = product_candles[current_time]
            prior = product_candles[previous_time]
            strength = (current.close / prior.close) - 1.0
            scores.append((strength, product_id))
        scores.sort(reverse=True)
        best_strength, best_product = scores[0]
        second_strength = scores[1][0] if len(scores) > 1 else -999.0
        target_ok = best_strength >= min_strength_pct and (best_strength - second_strength) >= leader_gap_pct
        current_candle = by_time[best_product][current_time]

        if position is not None:
            held_candle = by_time[position.product_id][current_time]
            position.hold_bars += 1
            stop_price = position.entry_price * (1.0 - stop_loss_pct)
            target_price = position.entry_price * (1.0 + take_profit_pct)
            exit_price: float | None = None
            if held_candle.low <= stop_price:
                exit_price = stop_price
            elif held_candle.high >= target_price:
                exit_price = target_price
            elif position.hold_bars >= max_hold_bars:
                exit_price = held_candle.close
            elif position.product_id != best_product and target_ok:
                exit_price = held_candle.close
            if exit_price is not None:
                cash_delta, pnl = _exit_position(position=position, exit_price=exit_price, fee_rate=fee_rate)
                cash_usd += cash_delta
                realized += pnl
                trades += 1
                hold_minutes.append(max(1.0, (current_time - position.entry_time) / 60.0))
                position = None

        if position is None and target_ok:
            entry_price = current_candle.close
            entry_fee = cash_usd * fee_rate
            notional = cash_usd
            spendable = cash_usd - entry_fee
            if spendable > 0.0:
                quantity = spendable / entry_price
                cash_usd = 0.0
                position = Position(
                    product_id=best_product,
                    entry_price=entry_price,
                    quantity=quantity,
                    notional_usd=notional,
                    entry_time=current_time,
                )

    if position is not None:
        last_time = ordered_times[-1]
        last_candle = by_time[position.product_id][last_time]
        cash_delta, pnl = _exit_position(position=position, exit_price=last_candle.close, fee_rate=fee_rate)
        cash_usd += cash_delta
        realized += pnl
        trades += 1
        hold_minutes.append(max(1.0, (last_time - position.entry_time) / 60.0))

    return {
        "realized_net_usd": realized,
        "trades": trades,
        "median_hold_minutes": statistics.median(hold_minutes) if hold_minutes else 0.0,
        "cash_usd": cash_usd,
    }


def simulate_scavenger(
    by_product: dict[str, list[Candle]],
    products: list[str],
    *,
    starting_cash: float,
    quote_per_buy: float,
    fee_bps: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    total_realized = 0.0
    total_closes = 0
    hold_proxy: list[float] = []
    open_lots = 0
    for product_id in products:
        buy_step, profit_target = SCAVENGER_CONFIGS[product_id]
        sim = _simulate_candle_proxy(
            [candle.__dict__ for candle in by_product[product_id]],
            buy_step=buy_step,
            profit_target=profit_target,
            quote_per_buy=quote_per_buy,
            starting_cash=starting_cash,
            max_lots=max(1, int(starting_cash // max(quote_per_buy, 1.0))),
            taker_fee_bps=fee_bps,
        )
        rows.append(
            {
                "product_id": product_id,
                "realized_net_usd": sim["realized_net_usd"],
                "trades": sim["realized_closes"],
                "median_hold_minutes": sim["median_hold_minutes"],
                "open_lots": sim["open_lots"],
            }
        )
        total_realized += sim["realized_net_usd"]
        total_closes += sim["realized_closes"]
        open_lots += sim["open_lots"]
        if sim["median_hold_minutes"] > 0.0:
            hold_proxy.append(sim["median_hold_minutes"])

    rows.sort(key=lambda row: row["realized_net_usd"], reverse=True)
    best = rows[0] if rows else {"product_id": "", "realized_net_usd": 0.0, "trades": 0, "median_hold_minutes": 0.0}
    summary = {
        "realized_net_usd": best["realized_net_usd"],
        "trades": best["trades"],
        "median_hold_minutes": best["median_hold_minutes"],
        "cash_usd": starting_cash + best["realized_net_usd"],
        "best_product_id": best["product_id"],
        "portfolio_total_realized_usd": total_realized,
        "portfolio_total_closes": total_closes,
        "portfolio_open_lots": open_lots,
    }
    return summary, rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "tactic",
        "fee_bps_per_side",
        "best_product_id",
        "realized_net_usd",
        "ending_cash_usd",
        "trades",
        "median_hold_minutes",
        "notes",
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
    starting_cash: float,
    results: list[dict[str, Any]],
    scavenger_rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Tactics Benchmark",
        "",
        f"- Window: last `{hours}h`",
        f"- Starting cash: `${starting_cash:.2f}`",
        "- Venue data: Coinbase brokerage market candles + current Coinbase best bid/ask",
        "- Purpose: compare spot tactic shapes that are actually compatible with a small U.S. Coinbase account",
        "- Important: candle benchmark is conservative but still not full historical tick replay",
        "",
        "| Tactic | Fee Model | Best Product | PnL | Ending Cash | Trades | Median Hold (m) | Notes |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in results:
        lines.append(
            "| {tactic} | {fee_bps_per_side:.1f}bps | {best_product_id} | {realized_net_usd:+.2f} | {ending_cash_usd:.2f} | {trades} | {median_hold_minutes:.1f} | {notes} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Maker Scavenger Product Split",
            "",
            "| Product | PnL | Closes | Median Hold (m) | Open Lots |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in scavenger_rows:
        lines.append(
            "| {product_id} | {realized_net_usd:+.2f} | {trades} | {median_hold_minutes:.1f} | {open_lots} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_ts = now - int(args.hours) * 3600
    products = [product.upper() for product in args.products]
    by_product, by_time, ordered_times = _load_market(
        client,
        products,
        start_ts=start_ts,
        end_ts=now,
        granularity=str(args.granularity),
    )

    momentum_rows: list[dict[str, Any]] = []
    for product_id in products:
        sim = simulate_breakout_continuation(
            by_product[product_id],
            starting_cash=float(args.starting_cash),
            fee_bps=60.0,
        )
        momentum_rows.append({"product_id": product_id, **sim})
    momentum_rows.sort(key=lambda row: row["realized_net_usd"], reverse=True)
    best_momentum = momentum_rows[0]

    rotation = simulate_rotation(
        by_time,
        ordered_times,
        starting_cash=float(args.starting_cash),
        fee_bps=60.0,
    )

    scavenger_summary, scavenger_rows = simulate_scavenger(
        by_product,
        products,
        starting_cash=float(args.starting_cash),
        quote_per_buy=float(args.quote_per_buy),
        fee_bps=40.0,
    )

    results = [
        {
            "tactic": "pump_rider_breakout",
            "fee_bps_per_side": 60.0,
            "best_product_id": best_momentum["product_id"],
            "realized_net_usd": best_momentum["realized_net_usd"],
            "ending_cash_usd": float(args.starting_cash) + best_momentum["realized_net_usd"],
            "trades": best_momentum["trades"],
            "median_hold_minutes": best_momentum["median_hold_minutes"],
            "notes": "taker-style breakout continuation on single product",
        },
        {
            "tactic": "relative_strength_rotator",
            "fee_bps_per_side": 60.0,
            "best_product_id": "multi-asset",
            "realized_net_usd": rotation["realized_net_usd"],
            "ending_cash_usd": rotation["cash_usd"],
            "trades": rotation["trades"],
            "median_hold_minutes": rotation["median_hold_minutes"],
            "notes": "rotate full account into strongest asset on close",
        },
        {
            "tactic": "maker_scavenger",
            "fee_bps_per_side": 40.0,
            "best_product_id": scavenger_summary["best_product_id"],
            "realized_net_usd": scavenger_summary["realized_net_usd"],
            "ending_cash_usd": scavenger_summary["cash_usd"],
            "trades": scavenger_summary["trades"],
            "median_hold_minutes": scavenger_summary["median_hold_minutes"],
            "notes": "best single-product slow scavenger from maker-style proxy",
        },
    ]

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, results)
    _write_md(
        md_path,
        hours=int(args.hours),
        starting_cash=float(args.starting_cash),
        results=results,
        scavenger_rows=scavenger_rows,
    )
    print(
        json.dumps(
            {
                "csv_path": str(csv_path),
                "md_path": str(md_path),
                "results": results,
                "scavenger_rows": scavenger_rows,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
