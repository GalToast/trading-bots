#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, to_float  # noqa: E402


DEFAULT_EVENTS_PATH = ROOT / "reports" / "kraken_live_btc_usd_conversion_events.jsonl"
DEFAULT_REPORT_PATH = ROOT / "reports" / "kraken_live_btc_usd_conversion_latest.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def load_btc_usd_pair(client: KrakenSpotClient) -> KrakenPair:
    for rest_pair, payload in client.asset_pairs().items():
        pair = parse_pair(rest_pair, payload)
        if pair and pair.base == "BTC" and pair.quote == "USD" and pair.status == "online":
            return pair
    raise RuntimeError("Could not find online BTC/USD Kraken pair")


def legal_price(price: float, tick_size: float) -> float:
    if tick_size <= 0.0:
        return price
    return round(round(price / tick_size) * tick_size, 12)


def order_status(client: KrakenSpotClient, txid: str) -> dict[str, Any]:
    result = client._request("POST", "/0/private/QueryOrders", params={"txid": txid}, private=True)
    if isinstance(result, dict):
        row = result.get(txid)
        if isinstance(row, dict):
            return row
    return {}


def cancel_order(client: KrakenSpotClient, txid: str) -> dict[str, Any]:
    return client._request("POST", "/0/private/CancelOrder", params={"txid": txid}, private=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit one tiny live BTC/USD post-only sell conversion and auto-cancel unfilled remainder."
    )
    parser.add_argument("--target-usd", type=float, default=10.0)
    parser.add_argument("--max-usd", type=float, default=10.50)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-wait-seconds", type=float, default=90.0)
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--i-understand-this-places-a-live-order", action="store_true")
    args = parser.parse_args()

    if not args.i_understand_this_places_a_live_order:
        raise SystemExit("Refusing to run without --i-understand-this-places-a-live-order")
    if args.target_usd <= 0.0 or args.target_usd > args.max_usd:
        raise SystemExit("--target-usd must be positive and <= --max-usd")

    client = KrakenSpotClient()
    events_path = Path(args.events_path)
    report_path = Path(args.report_path)

    pair = load_btc_usd_pair(client)
    balances = client.balance()
    btc_balance = to_float(balances.get("XXBT"), to_float(balances.get("XBT")))
    ticker = client.ticker([pair.rest_pair])
    row = ticker.get(pair.rest_pair) or next(iter(ticker.values()), {})
    bid = to_float((row.get("b") or [0])[0])
    ask = to_float((row.get("a") or [0])[0])
    if bid <= 0.0 or ask <= 0.0:
        raise RuntimeError(f"Bad BTC/USD ticker bid={bid} ask={ask}")

    price = legal_price(ask, pair.tick_size)
    volume = args.target_usd / price
    volume = max(volume, pair.order_min)
    notional = volume * price
    if notional > args.max_usd:
        raise RuntimeError(f"Computed notional ${notional:.4f} exceeds max ${args.max_usd:.4f}")
    if volume > btc_balance:
        raise RuntimeError(f"Insufficient BTC balance: need {volume:.8f}, have {btc_balance:.8f}")

    append_jsonl(
        events_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "live_btc_usd_conversion_submit_attempt",
            "pair": pair.rest_pair,
            "side": "sell",
            "post_only": True,
            "target_usd": round(args.target_usd, 6),
            "max_usd": round(args.max_usd, 6),
            "bid": bid,
            "ask": ask,
            "price": price,
            "volume": round(volume, 8),
            "estimated_notional": round(notional, 8),
            "btc_balance_before": round(btc_balance, 10),
        },
    )

    response = client.add_order(
        rest_pair=pair.rest_pair,
        side="sell",
        order_type="limit",
        volume=volume,
        price=price,
        post_only=True,
        validate=False,
    )
    txids = response.get("txid") if isinstance(response, dict) else None
    txid = str((txids or [""])[0])
    if not txid:
        raise RuntimeError(f"Kraken did not return txid: {response!r}")

    append_jsonl(
        events_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "live_btc_usd_conversion_submitted",
            "txid": txid,
            "response": response,
        },
    )

    deadline = time.time() + max(0.0, float(args.max_wait_seconds))
    last_status: dict[str, Any] = {}
    while True:
        last_status = order_status(client, txid)
        append_jsonl(
            events_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "live_btc_usd_conversion_status",
                "txid": txid,
                "status": last_status.get("status"),
                "vol": last_status.get("vol"),
                "vol_exec": last_status.get("vol_exec"),
                "cost": last_status.get("cost"),
                "fee": last_status.get("fee"),
                "price": last_status.get("price"),
            },
        )
        if str(last_status.get("status") or "").lower() in {"closed", "canceled", "expired"}:
            break
        if time.time() >= deadline:
            cancel_response = cancel_order(client, txid)
            append_jsonl(
                events_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "live_btc_usd_conversion_cancel_requested",
                    "txid": txid,
                    "response": cancel_response,
                },
            )
            time.sleep(1.0)
            last_status = order_status(client, txid)
            break
        time.sleep(max(1.0, float(args.poll_seconds)))

    final_balances = client.balance()
    final_trade_balance = client._request(
        "POST",
        "/0/private/TradeBalance",
        params={"asset": "ZUSD"},
        private=True,
    )
    payload = {
        "ts_utc": utc_now_iso(),
        "txid": txid,
        "pair": pair.rest_pair,
        "submitted_price": price,
        "submitted_volume": round(volume, 8),
        "submitted_estimated_notional": round(notional, 8),
        "final_status": last_status,
        "nonzero_balances": {
            k: v for k, v in final_balances.items() if to_float(v) != 0.0
        },
        "trade_balance_usd": final_trade_balance,
        "events_path": str(events_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
