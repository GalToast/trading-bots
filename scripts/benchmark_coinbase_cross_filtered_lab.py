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

from benchmark_coinbase_spot_burst_lab import _load_candles, evaluate_forward_path, is_compression_breakout
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_cross_filtered_lab_72h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_cross_filtered_lab_72h.md"


@dataclass(frozen=True)
class Candle:
    start: int
    low: float
    high: float
    open: float
    close: float
    volume: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase cross-aware filtered spot lab.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--min-direct-volume", type=float, default=250000.0)
    parser.add_argument("--max-bases", type=int, default=24)
    parser.add_argument("--fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def _to_candles(rows: list[Any]) -> list[Candle]:
    out: list[Candle] = []
    for candle in rows:
        if isinstance(candle, dict):
            out.append(
                Candle(
                    start=int(candle["start"]),
                    low=float(candle["low"]),
                    high=float(candle["high"]),
                    open=float(candle["open"]),
                    close=float(candle["close"]),
                    volume=float(candle.get("volume", 0.0) or 0.0),
                )
            )
        else:
            out.append(
                Candle(
                    start=int(candle.start),
                    low=float(candle.low),
                    high=float(candle.high),
                    open=float(candle.open),
                    close=float(candle.close),
                    volume=float(candle.volume),
                )
            )
    return out


def _product_map(client: CoinbaseAdvancedClient) -> dict[str, dict[str, dict[str, Any]]]:
    payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    by_base: dict[str, dict[str, dict[str, Any]]] = {}
    for product in (payload.get("products") or []):
        product_id = str(product.get("product_id") or "")
        base = str(product.get("base_currency_id") or "")
        quote = str(product.get("quote_currency_id") or "")
        if not product_id or not base or not quote:
            continue
        by_base.setdefault(base, {})[quote] = product
    return by_base


def _fetch_books(client: CoinbaseAdvancedClient, product_ids: list[str]) -> dict[str, dict[str, float]]:
    payload = client.best_bid_ask(product_ids)
    books = payload.get("pricebooks") or payload.get("pricebook") or []
    if isinstance(books, dict):
        books = [books]
    out: dict[str, dict[str, float]] = {}
    for book in books:
        product_id = str(book.get("product_id") or "")
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not product_id or not bids or not asks:
            continue
        bid = float(bids[0].get("price") or 0.0)
        ask = float(asks[0].get("price") or 0.0)
        if bid > 0.0 and ask > 0.0:
            out[product_id] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}
    return out


def _select_candidates(client: CoinbaseAdvancedClient, *, min_direct_volume: float, max_bases: int) -> list[dict[str, Any]]:
    by_base = _product_map(client)
    needed = {"BTC-USD", "ETH-USD"}
    base_rows: list[dict[str, Any]] = []
    for base, quotes in by_base.items():
        direct = quotes.get("USD")
        if not direct:
            continue
        direct_volume = float(direct.get("volume_24h") or 0.0)
        if direct_volume < min_direct_volume:
            continue
        for quote in ("BTC", "ETH"):
            if quote in quotes:
                needed.add(f"{base}-USD")
                needed.add(f"{base}-{quote}")
    books = _fetch_books(client, sorted(needed))
    btc_usd = books.get("BTC-USD")
    eth_usd = books.get("ETH-USD")

    for base, quotes in by_base.items():
        direct = quotes.get("USD")
        direct_book = books.get(f"{base}-USD")
        if not direct or not direct_book:
            continue
        direct_volume = float(direct.get("volume_24h") or 0.0)
        if direct_volume < min_direct_volume:
            continue
        direct_spread_pct = ((direct_book["ask"] - direct_book["bid"]) / direct_book["mid"]) * 100.0
        best_route: dict[str, Any] | None = None
        for quote, bridge in (("BTC", btc_usd), ("ETH", eth_usd)):
            cross_book = books.get(f"{base}-{quote}")
            if not cross_book or not bridge:
                continue
            implied_bid = cross_book["bid"] * bridge["bid"]
            implied_ask = cross_book["ask"] * bridge["ask"]
            implied_mid = (implied_bid + implied_ask) / 2.0
            route_spread_pct = ((implied_ask - implied_bid) / implied_mid) * 100.0 if implied_mid > 0.0 else 999.0
            gap_pct = ((direct_book["mid"] / implied_mid) - 1.0) * 100.0 if implied_mid > 0.0 else 0.0
            row = {
                "base": base,
                "direct_product": f"{base}-USD",
                "cross_product": f"{base}-{quote}",
                "bridge_product": f"{quote}-USD",
                "quote": quote,
                "direct_volume_24h": direct_volume,
                "direct_spread_pct": direct_spread_pct,
                "route_spread_pct": route_spread_pct,
                "gap_pct": gap_pct,
            }
            if best_route is None or row["route_spread_pct"] < best_route["route_spread_pct"]:
                best_route = row
        if not best_route:
            continue
        if best_route["direct_spread_pct"] > 0.25 or best_route["route_spread_pct"] > 0.9:
            continue
        base_rows.append(best_route)

    base_rows.sort(key=lambda row: (row["route_spread_pct"], row["direct_spread_pct"], -row["direct_volume_24h"]))
    return base_rows[:max_bases]


def _align_series(candles: list[Candle]) -> dict[int, Candle]:
    return {candle.start: candle for candle in candles}


def _summarize(events: list[dict[str, Any]]) -> dict[str, float]:
    if not events:
        return {"signals": 0, "win_rate_pct": 0.0, "avg_net_return_pct": 0.0, "positive_products": 0, "cumulative_net_pct": 0.0}
    return {
        "signals": len(events),
        "win_rate_pct": (sum(1 for event in events if event["net_return_pct"] > 0.0) / len(events)) * 100.0,
        "avg_net_return_pct": statistics.fmean(event["net_return_pct"] for event in events),
    }


def _cross_confirmed_compression(
    direct: list[Candle],
    cross: dict[int, Candle],
    bridge: dict[int, Candle],
    *,
    fee_bps_per_side: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for idx in range(len(direct)):
        candle = direct[idx]
        cross_candle = cross.get(candle.start)
        bridge_candle = bridge.get(candle.start)
        if not cross_candle or not bridge_candle:
            continue
        if not is_compression_breakout(direct, idx):
            continue
        implied_close = cross_candle.close * bridge_candle.close
        gap_pct = ((candle.close / implied_close) - 1.0) * 100.0 if implied_close > 0.0 else 0.0
        cross_strength = ((cross_candle.close / cross_candle.open) - 1.0) * 100.0 if cross_candle.open > 0.0 else 0.0
        bridge_strength = ((bridge_candle.close / bridge_candle.open) - 1.0) * 100.0 if bridge_candle.open > 0.0 else 0.0
        if gap_pct > 0.2:
            continue
        if cross_strength <= 0.0 and bridge_strength <= 0.0:
            continue
        result = evaluate_forward_path(direct, idx, target_pct=0.03, stop_pct=0.01, max_hold_bars=12, fee_bps_per_side=fee_bps_per_side)
        events.append({"net_return_pct": result.net_return_pct * 100.0, "outcome": result.outcome})
    return events


def _cross_lag_reclaim(
    direct: list[Candle],
    cross: dict[int, Candle],
    bridge: dict[int, Candle],
    *,
    fee_bps_per_side: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for idx in range(1, len(direct)):
        candle = direct[idx]
        prev = direct[idx - 1]
        cross_candle = cross.get(candle.start)
        bridge_candle = bridge.get(candle.start)
        if not cross_candle or not bridge_candle or prev.close <= 0.0 or candle.low <= 0.0:
            continue
        implied_close = cross_candle.close * bridge_candle.close
        gap_pct = ((candle.close / implied_close) - 1.0) * 100.0 if implied_close > 0.0 else 0.0
        flush = ((candle.low / prev.close) - 1.0) * 100.0
        reclaim = ((candle.close / candle.low) - 1.0) * 100.0
        close_location = 0.0 if candle.high <= candle.low else (candle.close - candle.low) / (candle.high - candle.low)
        if gap_pct > -0.12:
            continue
        if flush > -0.7 or reclaim < 0.45 or close_location < 0.6:
            continue
        result = evaluate_forward_path(direct, idx, target_pct=0.02, stop_pct=0.008, max_hold_bars=12, fee_bps_per_side=fee_bps_per_side)
        events.append({"net_return_pct": result.net_return_pct * 100.0, "outcome": result.outcome})
    return events


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_md(path: Path, rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Cross Filtered Lab",
        "",
        "- Direct USD execution only.",
        "- BTC/ETH routes used as signal confirmation or lag filters.",
        "",
        "## Candidate Bases",
        "",
        "| Base | Route | Direct Spread % | Route Spread % | Gap % | Direct Volume |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in candidates[:20]:
        lines.append(
            "| {base} | {cross_product} + {bridge_product} | {direct_spread_pct:.4f}% | {route_spread_pct:.4f}% | {gap_pct:.4f}% | {direct_volume_24h:.2f} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Tactic Results",
            "",
            "| Tactic | Signals | Win Rate | Avg Net % | Positive Products | Cumulative Net % | Top Products |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| {tactic} | {signals} | {win_rate_pct:.1f}% | {avg_net_return_pct:.3f}% | {positive_products} | {cumulative_net_pct:.3f}% | {top_products} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    candidates = _select_candidates(client, min_direct_volume=float(args.min_direct_volume), max_bases=int(args.max_bases))
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600

    tactic_rows: list[dict[str, Any]] = []
    tactic_events: dict[str, list[tuple[str, dict[str, Any]]]] = {
        "cross_confirmed_compression": [],
        "cross_lag_reclaim": [],
    }

    for candidate in candidates:
        direct = _to_candles(_load_candles(client, candidate["direct_product"], start_ts=start_ts, end_ts=end_ts, granularity=str(args.granularity)))
        cross = _align_series(_to_candles(_load_candles(client, candidate["cross_product"], start_ts=start_ts, end_ts=end_ts, granularity=str(args.granularity))))
        bridge = _align_series(_to_candles(_load_candles(client, candidate["bridge_product"], start_ts=start_ts, end_ts=end_ts, granularity=str(args.granularity))))
        for event in _cross_confirmed_compression(direct, cross, bridge, fee_bps_per_side=float(args.fee_bps_per_side)):
            tactic_events["cross_confirmed_compression"].append((candidate["base"], event))
        for event in _cross_lag_reclaim(direct, cross, bridge, fee_bps_per_side=float(args.fee_bps_per_side)):
            tactic_events["cross_lag_reclaim"].append((candidate["base"], event))

    for tactic_name, events in tactic_events.items():
        per_product: dict[str, float] = {}
        flat: list[dict[str, Any]] = []
        for base, event in events:
            flat.append(event)
            per_product[base] = per_product.get(base, 0.0) + event["net_return_pct"]
        summary = _summarize(flat)
        ranked_products = sorted(per_product.items(), key=lambda item: item[1], reverse=True)
        tactic_rows.append(
            {
                "tactic": tactic_name,
                "signals": summary["signals"],
                "win_rate_pct": summary["win_rate_pct"],
                "avg_net_return_pct": summary["avg_net_return_pct"],
                "positive_products": sum(1 for _, value in ranked_products if value > 0.0),
                "cumulative_net_pct": sum(per_product.values()),
                "top_products": ", ".join(base for base, value in ranked_products[:5] if value != 0.0),
            }
        )

    tactic_rows.sort(key=lambda row: (row["positive_products"], row["cumulative_net_pct"], row["avg_net_return_pct"]), reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, tactic_rows)
    _write_md(md_path, tactic_rows, candidates)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "candidates": candidates[:15], "rows": tactic_rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
