#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_kraken_vulture_reversal_replay import normalize_product  # noqa: E402
from build_spot_numeraire_accumulation_board import product_id_for_pair  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, to_float  # noqa: E402


DEFAULT_PRODUCTS = "HONEY-USD,BILLY-USD,LDO-USD,XION-USD,RAVE-USD,IOTX-USD"
DEFAULT_EVENT_PATH = ROOT / "reports" / "cache" / "kraken_l2_wall_tape.jsonl"
DEFAULT_SUMMARY_PATH = ROOT / "reports" / "kraken_l2_wall_tape_summary.json"


@dataclass(frozen=True)
class Level:
    price: float
    size: float

    @property
    def notional(self) -> float:
        return self.price * self.size


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


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


def parse_levels(raw_levels: Any, *, reverse: bool) -> list[Level]:
    levels: list[Level] = []
    for row in raw_levels or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        price = to_float(row[0])
        size = to_float(row[1])
        if price > 0.0 and size > 0.0:
            levels.append(Level(price=price, size=size))
    levels.sort(key=lambda level: level.price, reverse=reverse)
    return levels


def level_sum(levels: list[Level], count: int) -> float:
    return sum(level.notional for level in levels[: max(0, int(count))])


def imbalance_ratio(bid_usd: float, ask_usd: float) -> float:
    if ask_usd <= 0.0:
        return 999999.0 if bid_usd > 0.0 else 0.0
    return bid_usd / ask_usd


def obi(bid_usd: float, ask_usd: float) -> float:
    total = bid_usd + ask_usd
    return bid_usd / total if total > 0.0 else 0.5


def spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 10000.0 if mid > 0.0 else 0.0


def bps_change(new: float, old: float) -> float:
    return ((new / old) - 1.0) * 10000.0 if new > 0.0 and old > 0.0 else 0.0


def book_metrics(
    *,
    product_id: str,
    rest_pair: str,
    depth_payload: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(depth_payload, dict) or not depth_payload:
        return None
    raw_book = next(iter(depth_payload.values()))
    if not isinstance(raw_book, dict):
        return None
    bids = parse_levels(raw_book.get("bids"), reverse=True)
    asks = parse_levels(raw_book.get("asks"), reverse=False)
    if not bids or not asks:
        return None

    bid = bids[0].price
    ask = asks[0].price
    l1_bid_usd = level_sum(bids, 1)
    l1_ask_usd = level_sum(asks, 1)
    l5_bid_usd = level_sum(bids, 5)
    l5_ask_usd = level_sum(asks, 5)
    l10_bid_usd = level_sum(bids, 10)
    l10_ask_usd = level_sum(asks, 10)
    l10_ratio = imbalance_ratio(l10_bid_usd, l10_ask_usd)
    prev_bid = to_float((previous or {}).get("bid"))
    prev_ask = to_float((previous or {}).get("ask"))
    prev_l10_ratio = to_float((previous or {}).get("l10_imbalance_ratio"))
    row = {
        "ts_epoch": time.time(),
        "ts_utc": utc_now_iso(),
        "product_id": product_id,
        "rest_pair": rest_pair,
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2.0,
        "spread_bps": round(spread_bps(bid, ask), 6),
        "l1_bid_usd": round(l1_bid_usd, 6),
        "l1_ask_usd": round(l1_ask_usd, 6),
        "l5_bid_usd": round(l5_bid_usd, 6),
        "l5_ask_usd": round(l5_ask_usd, 6),
        "l10_bid_usd": round(l10_bid_usd, 6),
        "l10_ask_usd": round(l10_ask_usd, 6),
        "l10_imbalance_ratio": round(l10_ratio, 6),
        "l10_obi": round(obi(l10_bid_usd, l10_ask_usd), 6),
        "bid_change_bps": round(bps_change(bid, prev_bid), 6),
        "ask_change_bps": round(bps_change(ask, prev_ask), 6),
        "l10_ratio_change": round(l10_ratio - prev_l10_ratio, 6) if prev_l10_ratio > 0.0 else 0.0,
        "book_changed": bool(
            previous
            and (
                bid != prev_bid
                or ask != prev_ask
                or abs(l10_ratio - prev_l10_ratio) > 0.000001
            )
        ),
    }
    return row


def summarize_rows(rows: list[dict[str, Any]], *, rolling_window: int) -> dict[str, Any]:
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_product[str(row.get("product_id"))].append(row)
    leaders: list[dict[str, Any]] = []
    for product_id, product_rows in by_product.items():
        recent = list(deque(product_rows, maxlen=max(1, int(rolling_window))))
        change_count = sum(1 for row in product_rows if row.get("book_changed"))
        leaders.append(
            {
                "product_id": product_id,
                "samples": len(product_rows),
                "book_change_rate": round(change_count / len(product_rows), 6) if product_rows else 0.0,
                "max_l10_ratio": max((to_float(row.get("l10_imbalance_ratio")) for row in product_rows), default=0.0),
                "max_l10_obi": max((to_float(row.get("l10_obi")) for row in product_rows), default=0.0),
                "avg_recent_spread_bps": round(sum(to_float(row.get("spread_bps")) for row in recent) / len(recent), 6) if recent else 0.0,
                "avg_recent_l10_ratio": round(sum(to_float(row.get("l10_imbalance_ratio")) for row in recent) / len(recent), 6) if recent else 0.0,
                "max_abs_bid_change_bps": max((abs(to_float(row.get("bid_change_bps"))) for row in product_rows), default=0.0),
                "max_abs_ask_change_bps": max((abs(to_float(row.get("ask_change_bps"))) for row in product_rows), default=0.0),
            }
        )
    leaders.sort(
        key=lambda row: (
            to_float(row.get("max_l10_ratio")),
            to_float(row.get("book_change_rate")),
            to_float(row.get("max_abs_bid_change_bps")),
        ),
        reverse=True,
    )
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_l2_wall_tape_summary",
        "products": sorted(by_product),
        "records": len(rows),
        "leaders": leaders,
        "read": "Public L2 depth tape only. L2 imbalance is scouting evidence, not proof of maker queue fill or profitable roundtrip.",
    }


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = KrakenSpotClient()
    products = [normalize_product(product) for product in parse_csv(args.products)]
    pairs = load_pairs(client, products)
    missing = sorted(set(products) - set(pairs))
    event_path = Path(args.event_path)
    summary_path = Path(args.summary_path)
    previous_by_product: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    started = time.time()
    cycles = 0
    while time.time() - started < float(args.duration_seconds):
        cycles += 1
        for product_id, pair in pairs.items():
            try:
                depth = client.depth(pair.rest_pair, count=max(10, int(args.depth_count)))
                row = book_metrics(
                    product_id=product_id,
                    rest_pair=pair.rest_pair,
                    depth_payload=depth,
                    previous=previous_by_product.get(product_id),
                )
            except Exception as exc:
                row = {
                    "ts_epoch": time.time(),
                    "ts_utc": utc_now_iso(),
                    "product_id": product_id,
                    "rest_pair": pair.rest_pair,
                    "error": str(exc),
                }
            if row is None:
                continue
            append_jsonl(event_path, row)
            if "error" not in row:
                previous_by_product[product_id] = row
                rows.append(row)
        time.sleep(max(0.1, float(args.poll_seconds)))
    summary = summarize_rows(rows, rolling_window=int(args.rolling_window))
    summary["parameters"] = vars(args)
    summary["cycles"] = cycles
    summary["products_loaded"] = sorted(pairs)
    summary["missing_products"] = missing
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture read-only Kraken L2 depth wall/imbalance tape.")
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--duration-seconds", type=float, default=300.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--depth-count", type=int, default=10)
    parser.add_argument("--rolling-window", type=int, default=10)
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser.parse_args()


def main() -> int:
    summary = run(parse_args())
    print(
        json.dumps(
            {
                "summary_path": str(summary["parameters"]["summary_path"]),
                "records": summary["records"],
                "products_loaded": summary["products_loaded"],
                "leaders": summary["leaders"][:5],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
