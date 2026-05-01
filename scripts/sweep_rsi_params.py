#!/usr/bin/env python3
"""
RSI mean reversion parameter sweep.

Tests multiple RSI configs across top products to find any profitable combination.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from benchmark_coinbase_spot_rsi import fetch_candles_72h, rsi, RSITrade, run_rsi_system

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "coinbase_spot_rsi_param_sweep.json"

PRODUCTS = ["BAL-USD", "CHECK-USD", "ALEPH-USD", "BLUR-USD", "COMP-USD", "ARB-USD"]

RSI_PERIODS = [7, 14, 21]
OVERSOLD_LEVELS = [20, 25, 30, 35]
OVERBOUGHT_LEVELS = [65, 70, 75]
PROFIT_TARGETS = [0.005, 0.01, 0.015, 0.02]
VOLUME_FILTERS = [1.0, 1.5, 2.0]


def main() -> None:
    client = CoinbaseAdvancedClient()
    all_results = []
    config_count = 0

    # Pre-fetch candles
    print("Fetching candles...")
    candles_cache = {}
    for pid in PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    print(f"\nRunning {len(PRODUCTS) * len(RSI_PERIODS) * len(OVERSOLD_LEVELS) * len(OVERBOUGHT_LEVELS) * len(PROFIT_TARGETS) * len(VOLUME_FILTERS)} configs...")

    for pid in PRODUCTS:
        if pid not in candles_cache:
            continue
        candles = candles_cache[pid]

        for rsi_p in RSI_PERIODS:
            for os in OVERSOLD_LEVELS:
                for ob in OVERBOUGHT_LEVELS:
                    for pt in PROFIT_TARGETS:
                        for vf in VOLUME_FILTERS:
                            config_count += 1
                            try:
                                result = run_rsi_system(
                                    candles,
                                    starting_cash=48.0,
                                    maker_fee_bps=5.0,
                                    rsi_period=rsi_p,
                                    oversold_threshold=float(os),
                                    overbought_threshold=float(ob),
                                    profit_target_pct=pt,
                                    stop_loss_pct=0.003,
                                    max_hold_bars=48,
                                    volume_filter_mult=vf,
                                    deploy_pct=0.9,
                                    product_id=pid,
                                )
                                result["config"] = f"rsi{rsi_p}_os{os}_ob{ob}_pt{pt*100:.1f}pct_vf{vf}x"
                                all_results.append(result)
                            except Exception:
                                pass

    # Sort by realized net
    all_results.sort(key=lambda x: x.get("realized_net_usd", -999), reverse=True)

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_configs": config_count,
        "top_20": all_results[:20],
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Summary
    print(f"\n{'='*100}")
    print(f"{'Rank':>4} {'Config':<30} {'Product':<14} {'Trades':>6} {'Win%':>6} {'Net $':>10} {'Avg/Tr':>9}")
    print(f"{'='*100}")
    for i, r in enumerate(all_results[:20]):
        print(f"{i+1:>4} {r['config']:<30} {r['product_id']:<14} {r.get('total_trades',0):>6} {r.get('win_rate',0):>5.1%} ${r.get('realized_net_usd',0):>8.4f} ${r.get('avg_net_per_trade',0):>7.4f}")

    # How many configs were net positive?
    positive = [r for r in all_results if r.get("realized_net_usd", 0) > 0 and r.get("total_trades", 0) >= 3]
    print(f"\nConfigs with net positive AND >= 3 trades: {len(positive)}")
    for r in positive[:10]:
        print(f"  {r['config']:30s} {r['product_id']:<14} ${r['realized_net_usd']:+.4f} ({r['total_trades']} trades, {r['win_rate']:.1%} win)")

    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
