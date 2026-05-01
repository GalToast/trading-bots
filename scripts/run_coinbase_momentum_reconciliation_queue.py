#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient
import strategy_library as strategy_lib


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

QUEUE_PATH = REPORTS / "coinbase_momentum_reconciliation_queue.json"
SNAPSHOT_PATH = REPORTS / "reconciliation_candles.json"
CACHE_DIR = REPORTS / "candle_cache"
JSON_PATH = REPORTS / "coinbase_momentum_reconciliation_results.json"
MD_PATH = REPORTS / "coinbase_momentum_reconciliation_results.md"

STRATEGY_PARAMS = {
    "mom_10": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 10.0, "max_hold": 48},
    "mom_25": {"lookback": 25, "tp_pct": 12.0, "sl_pct": 7.0, "max_hold": 48},
    "mom_50": {"lookback": 50, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
}

FEE_RATE = 0.004
STARTING_CASH = 48.0


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_snapshot_candles(raw_candles: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "start": str(candle["start"]),
            "open": str(candle["open"]),
            "high": str(candle["high"]),
            "low": str(candle["low"]),
            "close": str(candle["close"]),
            "volume": str(candle.get("volume", "0")),
        }
        for candle in raw_candles
    ]


def normalize_cache_candles(raw_candles: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "start": str(candle["time"]),
            "open": str(candle["open"]),
            "high": str(candle["high"]),
            "low": str(candle["low"]),
            "close": str(candle["close"]),
            "volume": str(candle.get("volume", "0")),
        }
        for candle in raw_candles
    ]


def load_snapshot_map() -> dict[str, list[dict[str, str]]]:
    payload = load_json(SNAPSHOT_PATH)
    return {
        str(coin): normalize_snapshot_candles(list(data.get("candles") or []))
        for coin, data in (payload.get("coins") or {}).items()
    }


def cache_path_for_coin(coin: str) -> Path:
    return CACHE_DIR / f"{coin.replace('-', '_')}_FIVE_MINUTE_30d.json"


def load_cache_candles(coin: str) -> list[dict[str, str]]:
    payload = load_json(cache_path_for_coin(coin))
    return normalize_cache_candles(list(payload.get("candles") or []))


def fetch_candles(client: CoinbaseAdvancedClient, coin: str, *, window_days: int = 30) -> list[dict[str, str]]:
    now = int(time.time())
    start = now - window_days * 86400
    chunk_sec = 300 * 5 * 60
    candles: list[dict[str, Any]] = []
    current_start = start
    while current_start < now:
        current_end = min(current_start + chunk_sec, now)
        response = client.market_candles(coin, start=current_start, end=current_end, granularity="FIVE_MINUTE")
        candles.extend(response.get("candles", []))
        current_start = current_end
        time.sleep(0.1)
    candles.sort(key=lambda candle: int(candle["start"]))
    normalized = normalize_snapshot_candles(candles)
    cache_payload = {
        "product_id": coin,
        "granularity": "FIVE_MINUTE",
        "days": 30,
        "fetched_at": time.time(),
        "count": len(normalized),
        "candles": [
            {
                "time": int(candle["start"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", "0")),
            }
            for candle in normalized
        ],
    }
    save_json(cache_path_for_coin(coin), cache_payload)
    return normalized


def load_queue_rows() -> list[dict[str, Any]]:
    payload = load_json(QUEUE_PATH)
    return list(payload.get("queue") or [])


def run_momentum_reconciliation(candles: list[dict[str, str]], strategy: str) -> dict[str, Any]:
    params = STRATEGY_PARAMS[strategy]
    result = strategy_lib.momentum(
        candles,
        fee_rate=FEE_RATE,
        starting_cash=STARTING_CASH,
        entry_slip=0.0,
        exit_slip=0.0,
        **params,
    )
    return {
        "net_pnl": round(result["net_pnl"], 4),
        "return_pct": round(result["return_pct"], 4),
        "trades": int(result["trades"]),
        "win_rate": round(result["win_rate"], 1),
        "max_drawdown": round(result["max_drawdown"], 1),
        "signals": int(result["signals"]),
        "total_fees": round(result["total_fees"], 4),
        "engine": "strategy_library_snapshot",
    }


def classify_verdict(net_pnl: float) -> str:
    if net_pnl > 0.0:
        return "confirmed_positive"
    if net_pnl < 0.0:
        return "rejected"
    return "flat"


def build_results(selected_rows: list[dict[str, Any]], *, fetch_missing: bool = False) -> dict[str, Any]:
    snapshot_map = load_snapshot_map()
    client: CoinbaseAdvancedClient | None = CoinbaseAdvancedClient() if fetch_missing else None
    results: list[dict[str, Any]] = []

    for row in selected_rows:
        coin = str(row["coin"])
        strategy = str(row["strategy"])
        candles = snapshot_map.get(coin) or load_cache_candles(coin)
        source = "snapshot" if snapshot_map.get(coin) else "cache"
        if not candles and fetch_missing and client is not None:
            candles = fetch_candles(client, coin)
            source = "fetched"
        if not candles:
            results.append(
                {
                    "coin": coin,
                    "strategy": strategy,
                    "priority": row["priority"],
                    "source": "missing",
                    "verdict": "missing_candles",
                    "library_sweep_partial_14d_net_usd": row["library_sweep_partial_14d_net_usd"],
                    "note": "no snapshot/cache candles available",
                }
            )
            continue

        recon = run_momentum_reconciliation(candles, strategy)
        results.append(
            {
                "coin": coin,
                "strategy": strategy,
                "priority": row["priority"],
                "source": source,
                "verdict": classify_verdict(recon["net_pnl"]),
                "library_sweep_partial_14d_net_usd": row["library_sweep_partial_14d_net_usd"],
                "reconciliation_30d_net_usd": recon["net_pnl"],
                "reconciliation_30d_closes": recon["trades"],
                "reconciliation_30d_win_rate": recon["win_rate"],
                "reconciliation_30d_max_dd": recon["max_drawdown"],
                "note": row["reason"],
            }
        )

    return {
        "generated_at": utc_now_iso(),
        "results": results,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)

    lines = [
        "# Coinbase Momentum Reconciliation Results",
        "",
        "| Coin | Strategy | Priority | Source | Sweep Partial 14d $ | Recon 30d $ | Recon Closes | Recon WR | Recon DD | Verdict | Note |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in payload["results"]:
        lines.append(
            "| {coin} | {strategy} | {priority} | {source} | {sweep:.4f} | {recon} | {closes} | {wr} | {dd} | {verdict} | {note} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                priority=row["priority"],
                source=row["source"],
                sweep=float(row.get("library_sweep_partial_14d_net_usd") or 0.0),
                recon="" if row.get("reconciliation_30d_net_usd") is None else f"{float(row['reconciliation_30d_net_usd']):.4f}",
                closes="" if row.get("reconciliation_30d_closes") is None else row["reconciliation_30d_closes"],
                wr="" if row.get("reconciliation_30d_win_rate") is None else f"{float(row['reconciliation_30d_win_rate']):.1f}",
                dd="" if row.get("reconciliation_30d_max_dd") is None else f"{float(row['reconciliation_30d_max_dd']):.1f}",
                verdict=row["verdict"],
                note=row["note"],
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--priority", choices=["reconcile_next", "reconcile_later", "watch_only"], default="reconcile_next")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--exclude-coins", nargs="*", default=[])
    parser.add_argument("--fetch-missing", action="store_true")
    args = parser.parse_args()

    queue = [
        row for row in load_queue_rows()
        if str(row.get("priority") or "") == args.priority and str(row.get("coin") or "") not in set(args.exclude_coins)
    ]
    selected = queue[: args.limit]
    payload = build_results(selected, fetch_missing=args.fetch_missing)
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
