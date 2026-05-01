#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_rsi import run_rsi_system
from coinbase_advanced_client import CoinbaseAdvancedClient


REPORT_PATH = ROOT / "reports" / "coinbase_spot_rsi_walkforward.json"

CANDIDATES = [
    {
        "product_id": "ARB-USD",
        "label": "baseline",
        "cfg": {
            "rsi_period": 7,
            "oversold_threshold": 30.0,
            "overbought_threshold": 70.0,
            "profit_target_pct": 0.02,
            "stop_loss_pct": 0.003,
            "max_hold_bars": 48,
            "volume_filter_mult": 1.0,
        },
    },
    {
        "product_id": "ARB-USD",
        "label": "oos_candidate",
        "cfg": {
            "rsi_period": 7,
            "oversold_threshold": 30.0,
            "overbought_threshold": 70.0,
            "profit_target_pct": 0.025,
            "stop_loss_pct": 0.003,
            "max_hold_bars": 72,
            "volume_filter_mult": 1.25,
        },
    },
    {
        "product_id": "LIGHTER-USD",
        "label": "baseline",
        "cfg": {
            "rsi_period": 7,
            "oversold_threshold": 30.0,
            "overbought_threshold": 70.0,
            "profit_target_pct": 0.02,
            "stop_loss_pct": 0.003,
            "max_hold_bars": 48,
            "volume_filter_mult": 1.0,
        },
    },
    {
        "product_id": "LIGHTER-USD",
        "label": "oos_candidate",
        "cfg": {
            "rsi_period": 7,
            "oversold_threshold": 25.0,
            "overbought_threshold": 75.0,
            "profit_target_pct": 0.025,
            "stop_loss_pct": 0.005,
            "max_hold_bars": 48,
            "volume_filter_mult": 1.25,
        },
    },
    {
        "product_id": "VVV-USD",
        "label": "baseline",
        "cfg": {
            "rsi_period": 7,
            "oversold_threshold": 30.0,
            "overbought_threshold": 70.0,
            "profit_target_pct": 0.02,
            "stop_loss_pct": 0.003,
            "max_hold_bars": 48,
            "volume_filter_mult": 1.0,
        },
    },
    {
        "product_id": "VVV-USD",
        "label": "oos_candidate",
        "cfg": {
            "rsi_period": 9,
            "oversold_threshold": 35.0,
            "overbought_threshold": 75.0,
            "profit_target_pct": 0.025,
            "stop_loss_pct": 0.003,
            "max_hold_bars": 48,
            "volume_filter_mult": 1.25,
        },
    },
]

BASELINE_CFG = {
    "rsi_period": 7,
    "oversold_threshold": 30.0,
    "overbought_threshold": 70.0,
    "profit_target_pct": 0.02,
    "stop_loss_pct": 0.003,
    "max_hold_bars": 48,
    "volume_filter_mult": 1.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward Coinbase RSI comparison")
    parser.add_argument("--products", nargs="*", default=None)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--report-path", default=str(REPORT_PATH))
    parser.add_argument("--train-hours", type=int, default=48)
    parser.add_argument("--test-hours", type=int, default=24)
    parser.add_argument("--step-hours", type=int, default=24)
    parser.add_argument("--total-hours", type=int, default=144)
    return parser.parse_args()


def fetch_candles_hours(client: CoinbaseAdvancedClient, product_id: str, *, hours: int, granularity: str = "FIVE_MINUTE") -> list[dict]:
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (hours * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append(
                    {
                        "time": t,
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c.get("volume", 0)),
                    }
                )
        chunk_end = chunk_start - 1
        time.sleep(0.15)
    return sorted(all_candles, key=lambda x: x["time"])


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    bars_per_hour = 12
    train_hours = int(args.train_hours)
    test_hours = int(args.test_hours)
    step_hours = int(args.step_hours)
    total_hours = int(args.total_hours)
    train_bars = train_hours * bars_per_hour
    test_bars = test_hours * bars_per_hour
    step_bars = step_hours * bars_per_hour
    results = []

    candidates = CANDIDATES
    if args.products:
        wanted = {p.upper() for p in args.products}
        candidates = [candidate for candidate in candidates if str(candidate["product_id"]).upper() in wanted]
        missing = sorted(wanted - {str(candidate["product_id"]).upper() for candidate in candidates})
        for product_id in missing:
            candidates.append(
                {
                    "product_id": product_id,
                    "label": "baseline",
                    "cfg": dict(BASELINE_CFG),
                }
            )
    if args.baseline_only:
        candidates = [candidate for candidate in candidates if candidate.get("label") == "baseline"]

    cache: dict[str, list[dict]] = {}
    for candidate in candidates:
        pid = candidate["product_id"]
        if pid not in cache:
            cache[pid] = fetch_candles_hours(client, pid, hours=total_hours)

    for candidate in candidates:
        pid = candidate["product_id"]
        candles = cache[pid]
        windows = []
        start_idx = 0
        while start_idx + train_bars + test_bars <= len(candles):
            train = candles[start_idx : start_idx + train_bars]
            test = candles[start_idx + train_bars : start_idx + train_bars + test_bars]
            train_res = run_rsi_system(train, starting_cash=48.0, maker_fee_bps=5.0, deploy_pct=0.9, product_id=pid, **candidate["cfg"])
            test_res = run_rsi_system(test, starting_cash=48.0, maker_fee_bps=5.0, deploy_pct=0.9, product_id=pid, **candidate["cfg"])
            windows.append(
                {
                    "train_net": float(train_res.get("realized_net_usd", 0.0)),
                    "train_trades": int(train_res.get("total_trades", 0)),
                    "test_net": float(test_res.get("realized_net_usd", 0.0)),
                    "test_trades": int(test_res.get("total_trades", 0)),
                    "test_avg_per_trade": float(test_res.get("avg_net_per_trade", 0.0)),
                }
            )
            start_idx += step_bars

        positive_test = sum(1 for w in windows if w["test_net"] > 0)
        results.append(
            {
                "product_id": pid,
                "label": candidate["label"],
                "cfg": candidate["cfg"],
                "windows": windows,
                "windows_count": len(windows),
                "positive_test_windows": positive_test,
                "test_net_total": round(sum(w["test_net"] for w in windows), 4),
                "test_avg_window_net": round(sum(w["test_net"] for w in windows) / len(windows), 4) if windows else 0.0,
            }
        )

    results.sort(key=lambda r: (r["positive_test_windows"], r["test_net_total"], r["test_avg_window_net"]), reverse=True)
    report_path = Path(args.report_path)
    report_path.write_text(json.dumps({"generated_at": time.time(), "results": results}, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
