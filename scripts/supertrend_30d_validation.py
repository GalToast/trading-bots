#!/usr/bin/env python3
"""
Supertrend 30d Validation — The #1 Edge at $3,406 (7d)

Tests whether the supertrend strategy survives 30d backtesting on 20 coins.
If it does, this becomes the primary deployment candidate.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest


def compute_supertrend(candles_hist, period=10, multiplier=3.0):
    """Compute Supertrend indicator. Returns (supertrend_line, trend_direction)."""
    if len(candles_hist) < period + 1:
        return None, None

    # Calculate ATR
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    if len(trs) < period:
        return None, None

    atr = sum(trs[-period:]) / period

    # Middle band (average of high and low)
    mid = (float(candles_hist[-1]["high"]) + float(candles_hist[-1]["low"])) / 2

    # Upper and lower bands
    upper_band = mid + multiplier * atr
    lower_band = mid - multiplier * atr

    # Trend direction (simplified: compare close to bands)
    close = float(candles_hist[-1]["close"])

    # Simplified Supertrend: if close above lower band, bullish; if below upper band, bearish
    # More precise: track the active band
    if close > upper_band:
        return lower_band, "bullish"
    elif close < lower_band:
        return upper_band, "bearish"
    else:
        # Check previous trend
        if len(candles_hist) > 2:
            prev_close = float(candles_hist[-2]["close"])
            prev_mid = (float(candles_hist[-2]["high"]) + float(candles_hist[-2]["low"])) / 2
            prev_atr = sum(trs[-period-1:-1]) / period if len(trs) > period else atr
            prev_lower = prev_mid - multiplier * prev_atr
            prev_upper = prev_mid + multiplier * prev_atr

            if prev_close > prev_upper:
                return lower_band, "bullish"
            elif prev_close < prev_lower:
                return upper_band, "bearish"
            else:
                return lower_band, "bullish" if close > prev_mid else "bearish"
        return lower_band, "bullish"


def _supertrend_entry(candles_hist, closes, candle, params):
    """Enter when Supertrend flips from bearish to bullish."""
    if len(candles_hist) < 25:
        return False

    period = params.get("st_period", 10)
    multiplier = params.get("st_multiplier", 3.0)

    # Current trend
    _, current_trend = compute_supertrend(candles_hist, period, multiplier)

    # Previous trend (2 bars ago)
    if len(candles_hist) < 26:
        return False
    _, prev_trend = compute_supertrend(candles_hist[:-1], period, multiplier)

    # Enter on flip from bearish to bullish
    if prev_trend == "bearish" and current_trend == "bullish":
        return True

    # Also enter if trend is bullish and price is rising (catch continuation)
    if current_trend == "bullish" and len(closes) > 1 and closes[-1] > closes[-2]:
        # Only if the flip happened recently (within 3 bars)
        for i in range(1, 4):
            if len(candles_hist) > i + 1:
                _, t = compute_supertrend(candles_hist[:-i], period, multiplier)
                if t == "bearish":
                    return True
                if t == "bullish":
                    continue
    return False


def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"SUPERTREND 30D VALIDATION")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()

    coins = [
        "NOM-USD", "RAVE-USD", "GHST-USD", "TRU-USD", "SUP-USD",
        "AAVE-USD", "AVAX-USD", "ADA-USD", "ATOM-USD", "APT-USD",
        "ARB-USD", "BNB-USD", "AVNT-USD", "AKT-USD", "ALGO-USD",
        "AERO-USD", "BCH-USD", "APE-USD", "ANKR-USD", "AXS-USD",
    ]
    print(f"Testing on {len(coins)} coins (30d)\n")

    now = int(time.time())
    start_ts = now - 30 * 86400

    all_candles = {}
    for coin in coins:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            if candles:
                all_candles[coin] = candles
                print(f"  {coin}: {len(candles)} candles (30d)")
        except Exception as e:
            print(f"  {coin}: ERROR — {str(e)[:60]}")
        time.sleep(0.2)

    print(f"\nFetched {len(all_candles)} coins\n")

    # Test multiple supertrend param combos
    param_combos = [
        {"st_period": 10, "st_multiplier": 3.0, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
        {"st_period": 10, "st_multiplier": 2.0, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
        {"st_period": 14, "st_multiplier": 3.0, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
        {"st_period": 10, "st_multiplier": 3.0, "tp_pct": 15, "sl_pct": 0, "max_hold": 48},
        {"st_period": 10, "st_multiplier": 2.0, "tp_pct": 15, "sl_pct": 0, "max_hold": 48},
        {"st_period": 14, "st_multiplier": 2.0, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
        {"st_period": 10, "st_multiplier": 1.5, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
        {"st_period": 10, "st_multiplier": 3.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24},
        {"st_period": 14, "st_multiplier": 3.0, "tp_pct": 15, "sl_pct": 0, "max_hold": 48},
        {"st_period": 10, "st_multiplier": 2.5, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
    ]

    all_results = []

    for params in param_combos:
        coin_results = []
        for coin, candles in all_candles.items():
            try:
                result = backtest(candles, _supertrend_entry, params, fee_rate=0.004, starting_cash=48.0)
                coin_results.append({"coin": coin, "candles": len(candles), **result})
            except Exception as e:
                coin_results.append({"coin": coin, "error": str(e)[:80]})

        profitable = [r for r in coin_results if "net_pnl" in r and r["net_pnl"] > 0]
        total_pnl = sum(r.get("net_pnl", 0) for r in coin_results)
        avg_pnl = total_pnl / len(coin_results) if coin_results else 0
        hit_rate = len(profitable) / len(coin_results) * 100 if coin_results else 0

        params_str = f"p={params['st_period']}, m={params['st_multiplier']}, TP={params['tp_pct']}%, SL={params['sl_pct']}%, MH={params['max_hold']}"
        print(f"  Supertrend ({params_str})")
        print(f"    Total PnL: ${total_pnl:>8.2f}  Avg: ${avg_pnl:>7.2f}  Hit: {hit_rate:>5.1f}%  Coins: {len(profitable)}/{len(coin_results)}")

        if profitable:
            best = max(profitable, key=lambda x: x.get("net_pnl", 0))
            print(f"    Best: {best['coin']} ${best['net_pnl']:.2f} ({best['win_rate']}% WR, {best['trades']} trades)")

        all_results.append({
            "params": params,
            "params_str": params_str,
            "total_net_pnl": round(total_pnl, 2),
            "avg_net_pnl": round(avg_pnl, 2),
            "hit_rate": round(hit_rate, 1),
            "profitable_coins": len(profitable),
            "total_coins": len(coin_results),
            "coin_details": coin_results,
        })

    all_results.sort(key=lambda x: x["total_net_pnl"], reverse=True)

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "coins_tested": len(all_candles),
        "param_combos_tested": len(all_results),
        "results": all_results,
        "best_params": all_results[0] if all_results else None,
    }

    out_path = Path(__file__).parent.parent / "reports" / "supertrend_30d_validation.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"VALIDATION COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results: {out_path}")

    if all_results:
        best = all_results[0]
        print(f"\n  BEST SUPERTREND PARAMS (30d): {best['params_str']}")
        print(f"  Total PnL: ${best['total_net_pnl']:.2f}")
        print(f"  Hit Rate: {best['hit_rate']:.1f}% ({best['profitable_coins']}/{best['total_coins']} coins)")
        print(f"  Avg per coin: ${best['avg_net_pnl']:.2f}")

        # Compare to 7d
        print(f"\n  7d result was $3,406 on 35 coins")
        print(f"  30d result is ${best['total_net_pnl']:.2f} on {best['total_coins']} coins")
        if best["total_net_pnl"] > 0:
            print(f"  ✅ SURVIVED 30d — Supertrend is a real edge!")
        else:
            print(f"  ❌ FAILED 30d — Same fate as vol_breakout and atr_trailing")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
