#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_penetration_lattice_shadow import append_jsonl, utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = ROOT / "reports" / "spot_microstructure_sync.jsonl"
DEFAULT_STATE_PATH = ROOT / "reports" / "spot_microstructure_sync_state.json"
DEFAULT_COINBASE_PRODUCTS = ["BTC-USD", "RAVE-USD", "BAL-USD", "IOTX-USD"]
DEFAULT_KRAKEN_PAIRS = {"XXBTZUSD": "BTC-USD"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Kraken/Coinbase sync snapshots for spot microstructure research")
    parser.add_argument("--output-path", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--samples", type=int, default=0)
    parser.add_argument("--coinbase-products", nargs="*", default=DEFAULT_COINBASE_PRODUCTS)
    parser.add_argument("--kraken-pairs", nargs="*", default=list(DEFAULT_KRAKEN_PAIRS.keys()))
    return parser.parse_args()


def fetch_kraken_payload(pairs: list[str]) -> dict[str, Any]:
    pair_query = ",".join(pairs)
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair_query}"
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode())


def normalize_kraken_ticker_payload(payload: dict[str, Any], alias_map: dict[str, str]) -> dict[str, dict[str, float]]:
    result = payload.get("result") or {}
    out: dict[str, dict[str, float]] = {}
    for raw_pair, row in result.items():
        if not isinstance(row, dict):
            continue
        symbol = alias_map.get(raw_pair, raw_pair)
        try:
            last = float((row.get("c") or [0])[0] or 0.0)
            bid = float((row.get("b") or [0])[0] or 0.0)
            ask = float((row.get("a") or [0])[0] or 0.0)
        except Exception:
            continue
        out[symbol] = {
            "last": last,
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2.0 if bid > 0 and ask > 0 else last,
        }
    return out


def normalize_coinbase_pricebooks(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for book in payload.get("pricebooks") or []:
        try:
            product_id = str(book.get("product_id") or "").upper()
            if not product_id:
                continue
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids or not asks:
                continue
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            bid_size = float(bids[0]["size"])
            ask_size = float(asks[0]["size"])
        except Exception:
            continue
        out[product_id] = {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2.0,
            "bid_size": bid_size,
            "ask_size": ask_size,
        }
    return out


def main() -> int:
    args = parse_args()
    output_path = Path(args.output_path)
    state_path = Path(args.state_path)
    interval_seconds = max(0.2, float(args.interval_seconds))
    max_samples = max(0, int(args.samples))
    duration_seconds = max(0.0, float(args.duration_seconds))
    coinbase_products = [str(product).upper() for product in args.coinbase_products]
    kraken_pairs = [str(pair).upper() for pair in args.kraken_pairs]
    alias_map = {pair: DEFAULT_KRAKEN_PAIRS.get(pair, pair) for pair in kraken_pairs}

    client = CoinbaseAdvancedClient()
    started_at = time.time()
    sample_count = 0
    runner = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "interval_seconds": interval_seconds,
        "heartbeat_at": None,
        "last_successful_sample_at": None,
        "consecutive_errors": 0,
        "last_error_at": None,
        "last_error_message": "",
    }

    def save_state(last_record: dict[str, Any] | None = None) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": utc_now_iso(),
            "runner": runner,
            "capture": {
                "output_path": str(output_path),
                "sample_count": sample_count,
                "coinbase_products": coinbase_products,
                "kraken_pairs": kraken_pairs,
                "last_record": last_record or {},
            },
        }
        state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"[{utc_now_iso()}] Capturing spot sync to {output_path} "
        f"(interval={interval_seconds:.2f}s, duration={duration_seconds:.1f}s, samples={max_samples or '-'}).",
        flush=True,
    )

    while True:
        now_ts = time.time()
        if max_samples and sample_count >= max_samples:
            break
        if duration_seconds and (now_ts - started_at) >= duration_seconds and sample_count > 0:
            break

        record: dict[str, Any] = {
            "ts_epoch": now_ts,
            "ts_utc": utc_now_iso(),
            "sample_idx": sample_count + 1,
            "kraken": {},
            "coinbase": {},
            "errors": [],
        }

        try:
            kraken_payload = fetch_kraken_payload(kraken_pairs)
            record["kraken"] = normalize_kraken_ticker_payload(kraken_payload, alias_map)
        except Exception as exc:
            record["errors"].append({"source": "kraken", "error": str(exc)})

        try:
            pricebook_payload = client.best_bid_ask(coinbase_products)
            record["coinbase"] = normalize_coinbase_pricebooks(pricebook_payload)
        except Exception as exc:
            record["errors"].append({"source": "coinbase", "error": str(exc)})

        append_jsonl(output_path, record)
        sample_count += 1
        if record["errors"]:
            runner["consecutive_errors"] = int(runner["consecutive_errors"] or 0) + 1
            runner["last_error_at"] = record["ts_utc"]
            runner["last_error_message"] = "; ".join(str(item.get("error") or "") for item in record["errors"])
        else:
            runner["consecutive_errors"] = 0
            runner["last_error_at"] = None
            runner["last_error_message"] = ""
        runner["heartbeat_at"] = record["ts_utc"]
        runner["last_successful_sample_at"] = record["ts_utc"]
        save_state(record)
        print(
            f"[{record['ts_utc']}] sample={sample_count} "
            f"kraken={len(record['kraken'])} coinbase={len(record['coinbase'])} errors={len(record['errors'])}",
            flush=True,
        )
        time.sleep(interval_seconds)

    print(f"[{utc_now_iso()}] Done. Wrote {sample_count} samples to {output_path}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
