#!/usr/bin/env python3
"""Backtest ignition detection strategy against 1m candle cache.

Strategy:
1. Scan all products with 1m cache
2. Compute 5-minute momentum (close now vs close 5 min ago)
3. Compare to each product's 30-day median 5-min momentum
4. Enter when 5-min momentum > 3x median (ignition signal)
5. Trail at 85% retention, exit on momentum reversal
6. Account for 120bps/side fees + spread

Products tested: RAVE, SOL, BTC, ETH, IOTX, ALEPH, BAL, BLUR (all with 1m cache)
"""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"

FEE_BPS = 120  # per side
SPREAD_BPS = {
    "RAVE-USD": 10,
    "SOL-USD": 1,
    "BTC-USD": 1,
    "ETH-USD": 1,
    "IOTX-USD": 10,
    "ALEPH-USD": 10,
    "BAL-USD": 5,
    "BLUR-USD": 5,
}

PRODUCTS = {
    "RAVE_USD_ONE_MINUTE_30d.json": "RAVE-USD",
    "SOL_USD_ONE_MINUTE_30d.json": "SOL-USD",
    "BTC_USD_ONE_MINUTE_7d.json": "BTC-USD",
    "ETH_USD_ONE_MINUTE_7d.json": "ETH-USD",
    "IOTX_USD_ONE_MINUTE_7d.json": "IOTX-USD",
    "ALEPH_USD_ONE_MINUTE_7d.json": "ALEPH-USD",
    "BAL_USD_ONE_MINUTE_7d.json": "BAL-USD",
    "BLUR_USD_ONE_MINUTE_7d.json": "BLUR-USD",
}

IGNITION_MULTIPLIER = 3.0  # 3x median 5-min momentum
TRAIL_RETENTION = 0.85  # Give back only 15% of peak


def load_candles(filename):
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("candles") or data.get("data") or []


def backtest_product(product_name, candles, spread_bps):
    if len(candles) < 30:  # Need enough for median calculation
        return None

    # Compute 5-minute momentum for each candle (close now vs close 5 min ago)
    five_min_moves = []
    for i in range(5, len(candles)):
        open_price = candles[i-5]["open"]
        close_price = candles[i]["close"]
        if open_price == 0:
            continue
        move_pct = (close_price - open_price) / open_price * 100
        five_min_moves.append(abs(move_pct))

    if len(five_min_moves) < 100:
        return None

    median_5min = statistics.median(five_min_moves)
    if median_5min == 0:
        return None

    # Ignition threshold
    ignition_threshold = median_5min * IGNITION_MULTIPLIER

    # Spread cost in percentage
    spread_pct = spread_bps / 100.0
    fee_pct = (FEE_BPS * 2) / 100.0  # Round-trip fee
    total_cost_pct = spread_pct + fee_pct

    # Simulate trades
    trades = []
    in_position = False
    entry_price = 0
    peak_price = 0
    entry_idx = 0

    for i in range(5, len(candles)):
        open_price = candles[i-5]["open"]
        close_price = candles[i]["close"]
        high_price = candles[i]["high"]
        low_price = candles[i]["low"]
        if open_price == 0:
            continue

        move_5min = (close_price - open_price) / open_price * 100
        abs_move = abs(move_5min)

        if not in_position:
            # Check for ignition signal
            if abs_move >= ignition_threshold:
                # Enter in direction of move
                direction = 1 if move_5min > 0 else -1
                entry_price = close_price
                peak_price = close_price
                entry_idx = i
                in_position = True
        else:
            # Update peak
            if move_5min > 0:
                peak_price = max(peak_price, high_price)
            else:
                peak_price = max(peak_price, close_price)

            # Check trail stop
            trail_stop = entry_price + (peak_price - entry_price) * TRAIL_RETENTION
            if direction > 0:
                if low_price < trail_stop:
                    # Exit
                    exit_price = trail_stop
                    gross_pnl = (exit_price - entry_price) / entry_price * 100
                    net_pnl = gross_pnl - total_cost_pct
                    trades.append({
                        "entry": entry_price,
                        "exit": exit_price,
                        "peak": peak_price,
                        "gross_pct": round(gross_pnl, 4),
                        "net_pct": round(net_pnl, 4),
                        "cost_pct": round(total_cost_pct, 4),
                        "win": net_pnl > 0,
                    })
                    in_position = False
            else:
                if high_price > trail_stop:
                    exit_price = trail_stop
                    gross_pnl = (entry_price - exit_price) / entry_price * 100
                    net_pnl = gross_pnl - total_cost_pct
                    trades.append({
                        "entry": entry_price,
                        "exit": exit_price,
                        "peak": peak_price,
                        "gross_pct": round(gross_pnl, 4),
                        "net_pct": round(net_pnl, 4),
                        "cost_pct": round(total_cost_pct, 4),
                        "win": net_pnl > 0,
                    })
                    in_position = False

    # Close any remaining position at end
    if in_position and entry_idx < len(candles) - 1:
        exit_price = candles[-1]["close"]
        gross_pnl = (exit_price - entry_price) / entry_price * 100
        net_pnl = gross_pnl - total_cost_pct
        trades.append({
            "entry": entry_price,
            "exit": exit_price,
            "peak": peak_price,
            "gross_pct": round(gross_pnl, 4),
            "net_pct": round(net_pnl, 4),
            "cost_pct": round(total_cost_pct, 4),
            "win": net_pnl > 0,
        })

    if not trades:
        return {
            "product": product_name,
            "candles": len(candles),
            "median_5min": round(median_5min, 4),
            "ignition_threshold": round(ignition_threshold, 4),
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_net_pct": 0,
            "avg_net_pct": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "win_rate": 0,
        }

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    net_pcts = [t["net_pct"] for t in trades]

    return {
        "product": product_name,
        "candles": len(candles),
        "median_5min": round(median_5min, 4),
        "ignition_threshold": round(ignition_threshold, 4),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "total_net_pct": round(sum(net_pcts), 4),
        "avg_net_pct": round(statistics.mean(net_pcts), 4),
        "best_trade": round(max(net_pcts), 4),
        "worst_trade": round(min(net_pcts), 4),
        "win_rate": round(len(wins) / len(trades) * 100, 2),
    }


def main():
    print("=== Ignition Detector Backtest ===")
    print(f"Ignition threshold: {IGNITION_MULTIPLIER}x median 5-min move")
    print(f"Trail retention: {TRAIL_RETENTION*100:.0f}%")
    print(f"Fee: {FEE_BPS}bps/side + spread")
    print()

    results = []
    for filename, product in PRODUCTS.items():
        candles = load_candles(filename)
        if candles is None:
            print(f"  {product}: no 1m cache")
            continue

        spread = SPREAD_BPS.get(product, 10)
        result = backtest_product(product, candles, spread)
        if result is None:
            print(f"  {product}: not enough data")
            continue

        results.append(result)
        print(f"\n{product} ({result['candles']} candles, {result['candles']/1440:.1f} days):")
        print(f"  Median 5-min move: {result['median_5min']:.2f}%")
        print(f"  Ignition threshold: {result['ignition_threshold']:.2f}%")
        print(f"  Trades: {result['trades']} ({result['wins']}W / {result['losses']}L, {result['win_rate']:.1f}% WR)")
        print(f"  Total net: {result['total_net_pct']:.2f}%")
        print(f"  Avg net/trade: {result['avg_net_pct']:.2f}%")
        print(f"  Best: {result['best_trade']:.2f}%, Worst: {result['worst_trade']:.2f}%")

    print("\n\n=== SUMMARY ===")
    print(f"{'Product':<12} {'Days':>5} {'Trades':>7} {'WR%':>6} {'Total%':>8} {'Avg%':>7} {'Best%':>7} {'Worst%':>7}")
    print("-" * 70)
    for r in sorted(results, key=lambda x: x["total_net_pct"], reverse=True):
        days = r["candles"] / 1440
        print(f"{r['product']:<12} {days:>5.1f} {r['trades']:>7} {r['win_rate']:>5.1f}% {r['total_net_pct']:>+7.2f}% {r['avg_net_pct']:>+6.2f}% {r['best_trade']:>+6.2f}% {r['worst_trade']:>+6.2f}%")


if __name__ == "__main__":
    main()
