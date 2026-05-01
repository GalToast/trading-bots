#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
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

import kraken_config as cfg  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, parse_ticker, to_float  # noqa: E402


REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_live_radar.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_live_radar.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    parser = argparse.ArgumentParser(description="Build a read-only Kraken spot live radar using public books/tickers.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH), help="Rolling bid/ask sample cache.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--quotes", default="USD,USDT,USDC", help="Comma-separated quote currencies to include.")
    parser.add_argument("--all-quotes", action="store_true", help="Include all online tradable spot quotes (ignores --quotes).")
    parser.add_argument("--max-products", type=int, default=300)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--keep-seconds", type=float, default=3900.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-loops", type=int, default=0, help="Maximum loop iterations when --loop is set; 0 means unlimited.")
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--hot-bps", type=float, default=25.0)
    parser.add_argument("--building-bps", type=float, default=10.0)
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--maker-fee-bps", type=float, default=cfg.DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--use-websocket", action="store_true", help="Try public WebSocket ticker snapshots before REST fallback.")
    parser.add_argument("--websocket-timeout-seconds", type=float, default=6.0)
    return parser.parse_args()


def quote_set(value: str) -> set[str]:
    return {item.strip().upper() for item in str(value or "").split(",") if item.strip()}


def list_pairs(client: KrakenSpotClient, quotes: set[str], max_products: int, *, include_all_quotes: bool = False) -> list[KrakenPair]:
    payload = client.asset_pairs()
    pairs: list[KrakenPair] = []
    for rest_pair, row in payload.items():
        pair = parse_pair(rest_pair, row)
        if not pair:
            continue
        if pair.status not in {"online", ""}:
            continue
        if not include_all_quotes and pair.quote not in quotes:
            continue
        pairs.append(pair)
    pairs.sort(key=lambda pair: (pair.quote != "USD", pair.quote, pair.wsname))
    if int(max_products) <= 0:
        return pairs
    return pairs[: max(1, int(max_products))]


async def websocket_tickers(pairs: list[KrakenPair], timeout_seconds: float, *, chunk_size: int = 50) -> dict[str, dict[str, float]]:
    import websockets

    symbols = [pair.wsname for pair in pairs if pair.wsname]
    if not symbols:
        return {}
    url = cfg.WS_PUBLIC_URL
    books: dict[str, dict[str, float]] = {}
    rest_by_ws = {pair.wsname: pair.rest_pair for pair in pairs}
    async with websockets.connect(url, ping_interval=None, close_timeout=1) as ws:
        size = max(1, int(chunk_size))
        for idx in range(0, len(symbols), size):
            chunk = symbols[idx : idx + size]
            await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": chunk, "snapshot": True}}))
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline and len(books) < len(symbols):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("channel") != "ticker":
                continue
            for item in message.get("data") or []:
                symbol = str(item.get("symbol") or "")
                bid = to_float(item.get("bid") or item.get("best_bid"))
                bid_size = to_float(item.get("bid_qty") or item.get("best_bid_qty"))
                ask = to_float(item.get("ask") or item.get("best_ask"))
                ask_size = to_float(item.get("ask_qty") or item.get("best_ask_qty"))
                last = to_float(item.get("last") or item.get("last_price"))
                volume = to_float(item.get("volume") or item.get("volume_24h"))
                if symbol and bid > 0.0 and ask > 0.0:
                    books[rest_by_ws.get(symbol, symbol)] = {
                        "bid": bid,
                        "bid_size": bid_size,
                        "ask": ask,
                        "ask_size": ask_size,
                        "last": last,
                        "volume_24h": volume,
                        "source": "websocket_ticker",
                    }
        return books


def fetch_rest_tickers(client: KrakenSpotClient, pairs: list[KrakenPair], chunk_size: int) -> dict[str, dict[str, float]]:
    books: dict[str, dict[str, float]] = {}
    size = max(1, int(chunk_size))
    pair_by_rest = {pair.rest_pair: pair for pair in pairs}
    alt_by_rest = {pair.altname: pair for pair in pairs}
    ws_by_rest = {pair.wsname: pair for pair in pairs}
    for idx in range(0, len(pairs), size):
        chunk = pairs[idx : idx + size]
        payload = client.ticker([pair.rest_pair for pair in chunk])
        for returned_pair, row in payload.items():
            pair = pair_by_rest.get(returned_pair) or alt_by_rest.get(returned_pair) or ws_by_rest.get(returned_pair)
            if not pair:
                continue
            parsed = parse_ticker(pair.rest_pair, pair.wsname, row)
            if not parsed:
                continue
            books[pair.rest_pair] = {
                "bid": parsed.bid,
                "bid_size": parsed.bid_size,
                "ask": parsed.ask,
                "ask_size": parsed.ask_size,
                "last": parsed.last,
                "volume_24h": parsed.volume_24h,
                "source": parsed.source,
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
    pair: KrakenPair,
    book: dict[str, float],
    samples: list[dict[str, float]],
    now_epoch: float,
    args: argparse.Namespace,
) -> dict[str, Any]:
    bid = to_float(book.get("bid"))
    bid_size = to_float(book.get("bid_size"))
    ask = to_float(book.get("ask"))
    ask_size = to_float(book.get("ask_size"))
    
    spread_bps = ((ask - bid) / bid) * 10000.0 if bid > 0 and ask >= bid else 0.0
    imbalance_pct = (bid_size / (bid_size + ask_size) * 100.0) if (bid_size + ask_size) > 0 else 50.0
    
    last = samples[-2] if len(samples) >= 2 else None
    move_last_bps = bps_change(bid, to_float(last.get("bid"))) if last else 0.0
    horizons = {"ret_30s_bps": 30.0, "ret_60s_bps": 60.0, "ret_5m_bps": 300.0, "ret_15m_bps": 900.0, "ret_60m_bps": 3600.0}
    returns: dict[str, float] = {}
    for key, seconds in horizons.items():
        prior = sample_at_or_before(samples, now_epoch - seconds)
        returns[key] = bps_change(bid, to_float(prior.get("bid"))) if prior else 0.0
    deploy_usd = max(0.0, float(args.starting_cash) * float(args.deploy_pct))
    min_notional_usd = max(pair.cost_min, pair.order_min * ask)
    can_trade_100 = deploy_usd >= min_notional_usd if min_notional_usd > 0 else True
    taker_round_trip_bps = float(args.taker_fee_bps) * 2.0
    maker_taker_round_trip_bps = float(args.maker_fee_bps) + float(args.taker_fee_bps)
    
    # ADVERSARIAL FIX: Tick-Size Aware MER
    # If the spread is 100bps but the tick size is also 100bps, we can't 'improve'
    # position without crossing the spread. We subtract 1 tick to be conservative.
    tick_size_bps = (pair.tick_size / bid * 10000.0) if bid > 0 else 0.0
    effective_spread_bps = max(0.0, spread_bps - tick_size_bps)
    mer = effective_spread_bps / maker_taker_round_trip_bps if maker_taker_round_trip_bps > 0 else 0.0
    best_short = max(move_last_bps, returns["ret_30s_bps"], returns["ret_60s_bps"])
    if spread_bps > float(args.max_spread_bps):
        signal_state = "too_wide"
    elif not can_trade_100:
        signal_state = "below_min_size"
    elif best_short >= float(args.hot_bps):
        signal_state = "live_hot"
    elif best_short >= float(args.building_bps) or returns["ret_5m_bps"] >= float(args.hot_bps):
        signal_state = "building"
    elif max(best_short, returns["ret_5m_bps"], returns["ret_15m_bps"]) <= -float(args.building_bps):
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
        "product_id": pair.wsname.replace("/", "-"),
        "wsname": pair.wsname,
        "rest_pair": pair.rest_pair,
        "base_currency": pair.base,
        "quote_currency": pair.quote,
        "signal_state": signal_state,
        "velocity_score": round(velocity_score, 6),
        "mer": round(mer, 4),
        "bid": round(bid, 12),
        "bid_size": round(bid_size, 8),
        "ask": round(ask, 12),
        "ask_size": round(ask_size, 8),
        "imbalance_pct": round(imbalance_pct, 2),
        "spread_bps": round(spread_bps, 4),
        "move_last_bps": round(move_last_bps, 6),
        **{key: round(value, 6) for key, value in returns.items()},
        "best_short_bps": round(best_short, 6),
        "taker_round_trip_bps": round(taker_round_trip_bps, 4),
        "maker_taker_round_trip_bps": round(maker_taker_round_trip_bps, 4),
        "min_notional_usd": round(min_notional_usd, 6),
        "deploy_usd": round(deploy_usd, 6),
        "can_trade_starting_cash": can_trade_100,
        "order_min_base": pair.order_min,
        "cost_min": pair.cost_min,
        "volume_24h_base": round(to_float(book.get("volume_24h")), 6),
        "samples": len(samples),
        "sample_age_seconds": round(now_epoch - to_float(samples[0].get("ts")), 1) if samples else 0.0,
        "source": book.get("source") or "unknown",
    }


def build_once(args: argparse.Namespace) -> dict[str, Any]:
    client = KrakenSpotClient()
    pairs = list_pairs(
        client,
        quote_set(args.quotes),
        int(args.max_products),
        include_all_quotes=bool(args.all_quotes),
    )
    books: dict[str, dict[str, float]] = {}
    websocket_error = ""
    if bool(args.use_websocket):
        try:
            books = asyncio.run(websocket_tickers(pairs, float(args.websocket_timeout_seconds), chunk_size=int(args.chunk_size)))
        except Exception as exc:
            websocket_error = f"{type(exc).__name__}: {exc}"
            books = {}
    if len(books) < len(pairs):
        rest_books = fetch_rest_tickers(client, pairs, int(args.chunk_size))
        books.update({key: value for key, value in rest_books.items() if key not in books})

    state_path = Path(str(args.state_path))
    state = load_json(state_path)
    samples_by_pair = state.get("samples") if isinstance(state, dict) else {}
    if not isinstance(samples_by_pair, dict):
        samples_by_pair = {}
    now_epoch = time.time()
    prune_before = now_epoch - max(60.0, float(args.keep_seconds))
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        book = books.get(pair.rest_pair)
        if not book:
            continue
        old_samples = samples_by_pair.get(pair.rest_pair)
        samples = [sample for sample in old_samples if to_float(sample.get("ts")) >= prune_before] if isinstance(old_samples, list) else []
        samples.append({"ts": now_epoch, "bid": to_float(book.get("bid")), "ask": to_float(book.get("ask"))})
        samples.sort(key=lambda sample: to_float(sample.get("ts")))
        samples_by_pair[pair.rest_pair] = samples
        rows.append(score_row(pair=pair, book=book, samples=samples, now_epoch=now_epoch, args=args))
    rows.sort(
        key=lambda row: (
            row.get("signal_state") == "live_hot",
            row.get("signal_state") == "building",
            to_float(row.get("velocity_score")),
            to_float(row.get("best_short_bps")),
        ),
        reverse=True,
    )
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_live_radar",
        "shadow_only": True,
        "parameters": {
            "all_quotes": bool(args.all_quotes),
            "quotes": sorted(quote_set(args.quotes)),
            "max_products": int(args.max_products),
            "chunk_size": int(args.chunk_size),
            "keep_seconds": float(args.keep_seconds),
            "max_spread_bps": float(args.max_spread_bps),
            "hot_bps": float(args.hot_bps),
            "building_bps": float(args.building_bps),
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "maker_fee_bps": float(args.maker_fee_bps),
            "taker_fee_bps": float(args.taker_fee_bps),
            "use_websocket": bool(args.use_websocket),
            "websocket_error": websocket_error,
        },
        "summary": {
            "products_scanned": len(rows),
            "live_hot": sum(1 for row in rows if row.get("signal_state") == "live_hot"),
            "building": sum(1 for row in rows if row.get("signal_state") == "building"),
            "too_wide": sum(1 for row in rows if row.get("signal_state") == "too_wide"),
            "below_min_size": sum(1 for row in rows if row.get("signal_state") == "below_min_size"),
            "stale_or_flat": sum(1 for row in rows if row.get("signal_state") == "stale_or_flat"),
            "tradable_with_deploy_cash": sum(1 for row in rows if row.get("can_trade_starting_cash")),
        },
        "leadership_read": [
            "Read-only Kraken radar: public market data only, no private key and no order placement.",
            "Kraken starter taker/taker fee drag is modeled as 80bps round trip by default, versus the current Coinbase 240bps taker/taker drag.",
            "Rows are shadow candidates only; min size, spread, bid/ask path, and forward shadow fills still have to prove out.",
        ],
        "rows": rows,
    }
    write_json(state_path, {"updated_at": payload["generated_at"], "keep_seconds": float(args.keep_seconds), "samples": samples_by_pair})
    write_reports(payload, json_path=Path(str(args.json_path)), csv_path=Path(str(args.csv_path)), md_path=Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "wsname",
        "quote_currency",
        "signal_state",
        "velocity_score",
        "mer",
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
        "taker_round_trip_bps",
        "maker_taker_round_trip_bps",
        "min_notional_usd",
        "deploy_usd",
        "can_trade_starting_cash",
        "order_min_base",
        "cost_min",
        "volume_24h_base",
        "samples",
        "source",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Kraken Spot Live Radar",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Products scanned: `{payload.get('summary', {}).get('products_scanned')}`",
        f"- Tradable with deploy cash: `{payload.get('summary', {}).get('tradable_with_deploy_cash')}`",
        f"- Live hot: `{payload.get('summary', {}).get('live_hot')}`",
        f"- Building: `{payload.get('summary', {}).get('building')}`",
        f"- Taker round trip bps: `{float(payload.get('parameters', {}).get('taker_fee_bps', 0)) * 2:.2f}`",
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
            "| Rank | Product | Signal | Score | Last bps | 30s bps | 60s bps | 5m bps | Spread bps | Min USD | Source |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:50], start=1):
        lines.append(
            "| {idx} | {product_id} | {signal_state} | {velocity_score:.4f} | {move_last_bps:.4f} | {ret_30s_bps:.4f} | {ret_60s_bps:.4f} | {ret_5m_bps:.4f} | {spread_bps:.2f} | {min_notional_usd:.4f} | {source} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    loops = 0
    while True:
        payload = build_once(args)
        loops += 1
        print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "md_path": str(Path(args.md_path).resolve()), "rows": len(payload.get("rows") or [])}, indent=2))
        if not args.loop or (int(args.max_loops) > 0 and loops >= int(args.max_loops)):
            return
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
