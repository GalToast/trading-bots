#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_coinbase_spot_pulse_board import (  # noqa: E402
    product_base_currency,
    product_live_blockers,
    product_quote_currency,
    product_quote_volume,
)
from coinbase_advanced_client import CoinbaseAdvancedClient  # noqa: E402


REPORTS = ROOT / "reports"
DEFAULT_PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
DEFAULT_STATE_PATH = REPORTS / "cache" / "coinbase_spot_live_radar_ticks.json"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_live_radar.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_live_radar.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_live_radar.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a rolling live Coinbase spot pricebook radar without candle fetches.")
    parser.add_argument("--pulse-path", default=str(DEFAULT_PULSE_PATH), help="Existing pulse board used only as the product universe seed.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH), help="Rolling bid/ask sample cache.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--all-spot-quotes", action="store_true", help="Fetch product metadata and include every live-tradable spot quote.")
    parser.add_argument("--direct-usd-stable-only", action="store_true", help="Limit output to products directly tradable from USD/USDC.")
    parser.add_argument("--max-products", type=int, default=1000)
    parser.add_argument("--chunk-size", type=int, default=75)
    parser.add_argument("--keep-seconds", type=float, default=3900.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--hot-bps", type=float, default=25.0, help="One-sample move threshold for live_hot.")
    parser.add_argument("--building-bps", type=float, default=10.0)
    return parser.parse_args()


def products_from_pulse(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    products: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        if not product_id or not row.get("live_tradable"):
            continue
        products.append(
            {
                "product_id": product_id,
                "base_currency_id": row.get("base_currency"),
                "quote_currency_id": row.get("quote_currency"),
                "approximate_quote_24h_volume": row.get("quote_volume_native"),
                "status": "online",
                "product_type": "SPOT",
                "quote_min_size": row.get("quote_min_size", 1),
                "base_min_size": row.get("base_min_size", 1),
            }
        )
    return products


def products_from_api(client: CoinbaseAdvancedClient) -> list[dict[str, Any]]:
    payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    rows: list[dict[str, Any]] = []
    for product in payload.get("products") or []:
        if product_live_blockers(product):
            continue
        rows.append(product)
    return rows


def route_state(quote_currency: str) -> str:
    return "ready_direct_usd_or_stable" if quote_currency in {"USD", "USDC"} else "requires_quote_inventory_or_conversion_costing"


def fetch_pricebooks(client: CoinbaseAdvancedClient, product_ids: list[str], *, chunk_size: int) -> dict[str, dict[str, float]]:
    books: dict[str, dict[str, float]] = {}
    size = max(1, int(chunk_size))
    for idx in range(0, len(product_ids), size):
        chunk = product_ids[idx : idx + size]
        payload = client.best_bid_ask(chunk)
        for book in payload.get("pricebooks") or []:
            product_id = str(book.get("product_id") or "")
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not product_id or not bids or not asks:
                continue
            bid = to_float(bids[0].get("price"))
            ask = to_float(asks[0].get("price"))
            if bid <= 0.0 or ask <= 0.0:
                continue
            books[product_id] = {
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2.0,
                "spread_bps": ((ask - bid) / bid) * 10000.0 if ask >= bid else 0.0,
            }
    return books


def sample_at_or_before(samples: list[dict[str, float]], target_ts: float) -> dict[str, float] | None:
    candidate: dict[str, float] | None = None
    for sample in samples:
        if to_float(sample.get("ts")) <= target_ts:
            candidate = sample
        else:
            break
    return candidate


def bps_change(now_bid: float, old_bid: float) -> float:
    if now_bid <= 0.0 or old_bid <= 0.0:
        return 0.0
    return ((now_bid - old_bid) / old_bid) * 10000.0


def score_row(
    *,
    product: dict[str, Any],
    book: dict[str, float],
    samples: list[dict[str, float]],
    now_epoch: float,
    max_spread_bps: float,
    hot_bps: float,
    building_bps: float,
) -> dict[str, Any]:
    product_id = str(product.get("product_id") or "")
    quote = product_quote_currency(product)
    base = product_base_currency(product)
    current_bid = to_float(book.get("bid"))
    last = samples[-2] if len(samples) >= 2 else None
    move_last_bps = bps_change(current_bid, to_float(last.get("bid"))) if last else 0.0
    horizons = {
        "ret_30s_bps": 30.0,
        "ret_60s_bps": 60.0,
        "ret_5m_bps": 300.0,
        "ret_15m_bps": 900.0,
        "ret_60m_bps": 3600.0,
    }
    returns: dict[str, float] = {}
    for key, seconds in horizons.items():
        prior = sample_at_or_before(samples, now_epoch - seconds)
        returns[key] = bps_change(current_bid, to_float(prior.get("bid"))) if prior else 0.0
    spread_bps = to_float(book.get("spread_bps"))
    route = route_state(quote)
    best_short = max(move_last_bps, returns["ret_30s_bps"], returns["ret_60s_bps"])
    best_window = max(best_short, returns["ret_5m_bps"], returns["ret_15m_bps"])
    age_seconds = now_epoch - to_float(samples[0].get("ts")) if samples else 0.0
    if spread_bps > max_spread_bps:
        signal_state = "too_wide"
    elif route != "ready_direct_usd_or_stable":
        signal_state = "route_blocked"
    elif best_short >= hot_bps:
        signal_state = "live_hot"
    elif best_short >= building_bps or returns["ret_5m_bps"] >= hot_bps:
        signal_state = "building"
    elif best_window <= -building_bps:
        signal_state = "dumping"
    else:
        signal_state = "stale_or_flat"
    velocity_score = (
        max(move_last_bps, 0.0) * 1.2
        + max(returns["ret_30s_bps"], 0.0)
        + max(returns["ret_60s_bps"], 0.0) * 0.8
        + max(returns["ret_5m_bps"], 0.0) * 0.35
        - min(spread_bps, 250.0) * 0.25
    )
    return {
        "product_id": product_id,
        "base_currency": base,
        "quote_currency": quote,
        "live_route_state": route,
        "signal_state": signal_state,
        "velocity_score": round(velocity_score, 6),
        "bid": round(current_bid, 12),
        "ask": round(to_float(book.get("ask")), 12),
        "spread_bps": round(spread_bps, 4),
        "move_last_bps": round(move_last_bps, 6),
        **{key: round(value, 6) for key, value in returns.items()},
        "best_short_bps": round(best_short, 6),
        "best_window_bps": round(best_window, 6),
        "samples": len(samples),
        "sample_age_seconds": round(age_seconds, 1),
        "quote_volume_native": round(product_quote_volume(product), 4),
    }


def build_once(args: argparse.Namespace) -> dict[str, Any]:
    client = CoinbaseAdvancedClient()
    if args.all_spot_quotes:
        products = products_from_api(client)
    else:
        products = products_from_pulse(Path(str(args.pulse_path)))
        if not products:
            products = products_from_api(client)
    if bool(args.direct_usd_stable_only):
        products = [product for product in products if product_quote_currency(product) in {"USD", "USDC"}]
    products = products[: max(1, int(args.max_products))]
    product_ids = [str(product.get("product_id") or "") for product in products if product.get("product_id")]
    books = fetch_pricebooks(client, product_ids, chunk_size=int(args.chunk_size))

    state_path = Path(str(args.state_path))
    state = load_json(state_path)
    samples_by_product = state.get("samples") if isinstance(state, dict) else {}
    if not isinstance(samples_by_product, dict):
        samples_by_product = {}
    now_epoch = time.time()
    prune_before = now_epoch - max(60.0, float(args.keep_seconds))
    rows: list[dict[str, Any]] = []
    for product in products:
        product_id = str(product.get("product_id") or "")
        book = books.get(product_id)
        if not book:
            continue
        old_samples = samples_by_product.get(product_id)
        samples = [sample for sample in old_samples if to_float(sample.get("ts")) >= prune_before] if isinstance(old_samples, list) else []
        samples.append(
            {
                "ts": now_epoch,
                "bid": to_float(book.get("bid")),
                "ask": to_float(book.get("ask")),
                "spread_bps": to_float(book.get("spread_bps")),
            }
        )
        samples.sort(key=lambda sample: to_float(sample.get("ts")))
        samples_by_product[product_id] = samples
        rows.append(
            score_row(
                product=product,
                book=book,
                samples=samples,
                now_epoch=now_epoch,
                max_spread_bps=float(args.max_spread_bps),
                hot_bps=float(args.hot_bps),
                building_bps=float(args.building_bps),
            )
        )
    rows.sort(
        key=lambda row: (
            str(row.get("signal_state") or "") == "live_hot",
            str(row.get("signal_state") or "") == "building",
            to_float(row.get("velocity_score")),
            to_float(row.get("best_short_bps")),
        ),
        reverse=True,
    )
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_live_radar",
        "parameters": {
            "all_spot_quotes": bool(args.all_spot_quotes),
            "direct_usd_stable_only": bool(args.direct_usd_stable_only),
            "max_products": int(args.max_products),
            "chunk_size": int(args.chunk_size),
            "keep_seconds": float(args.keep_seconds),
            "max_spread_bps": float(args.max_spread_bps),
            "hot_bps": float(args.hot_bps),
            "building_bps": float(args.building_bps),
            "source": "best_bid_ask_pricebook_samples",
        },
        "summary": {
            "products_requested": len(product_ids),
            "products_scanned": len(rows),
            "live_hot": sum(1 for row in rows if row.get("signal_state") == "live_hot"),
            "building": sum(1 for row in rows if row.get("signal_state") == "building"),
            "stale_or_flat": sum(1 for row in rows if row.get("signal_state") == "stale_or_flat"),
            "too_wide": sum(1 for row in rows if row.get("signal_state") == "too_wide"),
            "route_blocked": sum(1 for row in rows if row.get("signal_state") == "route_blocked"),
        },
        "leadership_read": [
            "This is the live movement radar; it uses rolling best bid/ask snapshots, not stale candle cache returns.",
            "Rows become targets only when live bid movement persists after spread and route constraints; flat products decay to stale_or_flat.",
            "This radar does not place orders and does not prove profitability by itself.",
        ],
        "rows": rows,
    }
    write_json(state_path, {"updated_at": payload["generated_at"], "keep_seconds": float(args.keep_seconds), "samples": samples_by_product})
    write_reports(payload, json_path=Path(str(args.json_path)), csv_path=Path(str(args.csv_path)), md_path=Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "quote_currency",
        "live_route_state",
        "signal_state",
        "velocity_score",
        "bid",
        "ask",
        "spread_bps",
        "move_last_bps",
        "ret_30s_bps",
        "ret_60s_bps",
        "ret_5m_bps",
        "ret_15m_bps",
        "ret_60m_bps",
        "best_short_bps",
        "best_window_bps",
        "samples",
        "sample_age_seconds",
        "quote_volume_native",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Coinbase Spot Live Radar",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Source: `{payload.get('parameters', {}).get('source')}`",
        f"- Products scanned: `{payload.get('summary', {}).get('products_scanned')}`",
        f"- Live hot: `{payload.get('summary', {}).get('live_hot')}`",
        f"- Building: `{payload.get('summary', {}).get('building')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("leadership_read") or []])
    lines.extend(
        [
            "",
            "## Top Live Movers",
            "",
            "| Rank | Product | Signal | Score | Last bps | 30s bps | 60s bps | 5m bps | Spread bps | Route | Samples |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:40], start=1):
        lines.append(
            "| {idx} | {product_id} | {signal_state} | {velocity_score:.4f} | {move_last_bps:.4f} | {ret_30s_bps:.4f} | {ret_60s_bps:.4f} | {ret_5m_bps:.4f} | {spread_bps:.2f} | {live_route_state} | {samples} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    while True:
        build_once(args)
        if not args.loop:
            return
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
