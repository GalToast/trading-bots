#!/usr/bin/env python3
"""
Mass Coin Scanner — Test RSI MR on ALL 235 coins on Coinbase.

The multi-timeframe scan proved: RAVE makes money with RANDOM strategies
because of its VOLATILITY PROFILE. Find the NEXT RAVE.

Approach:
1. Get 7-day M5 candles for each coin
2. Run RSI(3)<30, 25% TP, 48-bar hold
3. Return net PnL, WR%, trade count
4. Sort by profitability

This is a BRUTE FORCE scan across 235 coins. Takes ~15-30 minutes.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
FEE_RATE = 0.0040


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rsi = [50.0] * period
    if avg_l > 0:
        rsi.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        rsi.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l > 0:
            rsi.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            rsi.append(100.0)
    return rsi


def fast_rsi_mr_test(candles, fee_rate=FEE_RATE, starting_cash=48.0):
    """Quick RSI MR test — no bells and whistles."""
    if len(candles) < 50:
        return None

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    rsi = compute_rsi(closes, period=3)

    cash = starting_cash
    in_position = False
    position = None
    trades = []

    for i in range(10, len(candles) - 1):
        cl = closes[i]
        h = highs[i]

        # Exit
        if in_position and position:
            if h >= position["target"] or (i - position["bar"]) >= 48:
                exit_p = position["target"] if h >= position["target"] else cl
                units = position["qty"]
                gross = units * exit_p
                exit_fee = gross * fee_rate
                net = gross - exit_fee - position["cost"]
                cash += gross - exit_fee
                trades.append({"net": net, "win": net > 0})
                in_position = False
                position = None

        # Entry
        if not in_position and cash >= 10.0 and rsi[i] <= 30:
            deploy = cash * 0.95
            entry_fee = deploy * fee_rate
            qty = (deploy - entry_fee) / cl
            if qty > 0:
                cash -= deploy
                in_position = True
                position = {
                    "entry": cl, "qty": qty, "bar": i,
                    "cost": deploy, "target": cl * 1.25,
                }

    if position:
        exit_p = closes[-1]
        units = position["qty"]
        gross = units * exit_p
        exit_fee = gross * fee_rate
        net = gross - exit_fee - position["cost"]
        cash += gross - exit_fee
        trades.append({"net": net, "win": net > 0})

    net = cash - starting_cash
    wins = sum(1 for t in trades if t["win"])
    wr = wins / max(1, len(trades)) * 100

    return {
        "net": round(net, 2),
        "trades": len(trades),
        "wins": wins,
        "wr": round(wr, 1),
        "volatility": round(max(closes) / min(closes) * 100 - 100, 1) if min(closes) > 0 else 0,
    }


def main():
    # Load coin list
    coin_file = ROOT / "coinbase_usd_pairs.txt"
    if not coin_file.exists():
        print("ERROR: Run get_all_coins.py first")
        return 1

    with open(coin_file) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("Total:")]

    # Coins already tested by the team
    TESTED = {"RAVE-USD", "MOG-USD", "BAL-USD", "IOTX-USD", "BLUR-USD",
              "ALEPH-USD", "SOL-USD", "DOGE-USD", "XRP-USD", "AAVE-USD",
              "ETH-USD", "PEPE-USD", "BTC-USD"}

    coins = [c for c in lines if c not in TESTED]
    print(f"Scanning {len(coins)} NEW coins (already tested: {len(TESTED)})")
    print(f"{'=' * 80}")

    results = []
    errors = []
    scanned = 0

    start_time = time.time()

    for idx, coin in enumerate(coins):
        try:
            # Load with rate limit handling
            candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
            if not candles or len(candles) < 50:
                errors.append({"coin": coin, "error": f"only {len(candles)} bars"})
                continue

            result = fast_rsi_mr_test(candles)
            if result is None:
                errors.append({"coin": coin, "error": "test failed"})
                continue

            result["coin"] = coin
            results.append(result)
            scanned += 1

            emoji = "✅" if result["net"] > 0 else "  "
            if result["net"] > 0:
                print(f"  {emoji} {coin:<16} ${result['net']:+7.2f}  {result['trades']:>3}t  "
                      f"{result['wr']:>5.1f}%WR  vol={result['volatility']:+.1f}%")
            elif result["trades"] > 10:
                print(f"  {emoji} {coin:<16} ${result['net']:+7.2f}  {result['trades']:>3}t  "
                      f"{result['wr']:>5.1f}%WR  vol={result['volatility']:+.1f}%")

            # Progress every 10 coins
            if (idx + 1) % 10 == 0:
                elapsed = time.time() - start_time
                rate = (idx + 1) / max(0.001, elapsed) * 60
                remaining = (len(coins) - idx - 1) / max(0.001, rate)
                print(f"  ... {idx + 1}/{len(coins)} ({elapsed/60:.1f}m elapsed, ~{remaining/60:.0f}m remaining)")

            # Small delay to avoid rate limiting
            if (idx + 1) % 50 == 0:
                print(f"  Rate limit cooldown — sleeping 5s...")
                time.sleep(5)

        except Exception as e:
            errors.append({"coin": coin, "error": str(e)[:100]})
            if "rate" in str(e).lower() or "429" in str(e):
                print(f"  Rate limited on {coin}, waiting 10s...")
                time.sleep(10)
                # Retry
                try:
                    candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
                    if candles and len(candles) >= 50:
                        result = fast_rsi_mr_test(candles)
                        if result:
                            result["coin"] = coin
                            results.append(result)
                            scanned += 1
                            emoji = "✅" if result["net"] > 0 else "  "
                            print(f"  {emoji} {coin:<16} ${result['net']:+7.2f}  {result['trades']:>3}t  "
                                  f"{result['wr']:>5.1f}%WR")
                except Exception as e2:
                    errors[-1]["error"] = str(e2)[:100] if errors else str(e2)[:100]

    elapsed = time.time() - start_time

    # Sort by net PnL
    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\n{'=' * 80}")
    print(f"  MASS SCAN COMPLETE — {scanned} coins tested in {elapsed/60:.1f}m")
    print(f"  Errors: {len(errors)}")
    print(f"{'=' * 80}")

    # Top 20
    print(f"\n  TOP 20 PROFITABLE (RSI MR, 7d, 40bps):")
    print(f"  {'Coin':<16} {'Net':>8} {'Trades':>7} {'WR%':>6} {'Vol%':>7}")
    print(f"  {'─' * 16} {'─' * 8} {'─' * 7} {'─' * 6} {'─' * 7}")
    for r in results[:20]:
        print(f"  {r['coin']:<16} ${r['net']:+7.2f}  {r['trades']:>6}  {r['wr']:>5.1f}%  {r['volatility']:>6.1f}%")

    # Bottom 20
    print(f"\n  BOTTOM 20 (worst losers):")
    print(f"  {'Coin':<16} {'Net':>8} {'Trades':>7} {'WR%':>6} {'Vol%':>7}")
    print(f"  {'─' * 16} {'─' * 8} {'─' * 7} {'─' * 6} {'─' * 7}")
    for r in results[-20:]:
        print(f"  {r['coin']:<16} ${r['net']:+7.2f}  {r['trades']:>6}  {r['wr']:>5.1f}%  {r['volatility']:>6.1f}%")

    # Stats
    profitable = [r for r in results if r["net"] > 0]
    print(f"\n  {len(profitable)}/{len(results)} profitable ({len(profitable)/max(1,len(results))*100:.1f}%)")
    if profitable:
        avg_vol = sum(r["volatility"] for r in profitable) / len(profitable)
        losing_vol = sum(r["volatility"] for r in results if r["net"] <= 0) / max(1, len(results) - len(profitable))
        print(f"  Avg volatility of winners: {avg_vol:.1f}%")
        print(f"  Avg volatility of losers: {losing_vol:.1f}%")

    # Save
    output_path = REPORT_DIR / "mass_coin_scan_rsi_mr_7d.json"
    with open(output_path, "w") as f:
        json.dump({
            "scanned": scanned,
            "errors": len(errors),
            "results": results,
            "top_20": results[:20],
            "bottom_20": results[-20:],
            "profitable_count": len(profitable),
            "total_coins": len(coins),
        }, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
