#!/usr/bin/env python3
"""
Burst-on-RAVE Backtest — Can the burst fade strategy work on RAVE?

The burst strategy (from burst_fade_god_mode.py):
1. Detect "burst" candles (high volatility, range >= threshold)
2. SHORT the burst — enter ABOVE the burst high
3. TP = entry - fraction of burst range (mean reversion)
4. SL = tight stop above entry (if price keeps going up, exit fast)

This is the OPPOSITE of RSI MR:
- RSI MR: Buy dips, ride up
- Burst: Fade pumps, ride down

On RAVE, which has HUGE moves in both directions, burst could be extremely profitable
if the coin pumps then reverts. But if RAVE pumps and KEEPS going, burst gets stopped out.

Output: reports/burst_on_rave_results.json
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "burst_on_rave_results.json"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"
WINDOW_DAYS = 30
STARTING_CASH = 48.0

# Burst parameters (god mode defaults for BAL, tuned for RAVE)
BURST_THRESHOLD = 3.0  # Min candle range % to trigger (RAVE needs higher threshold)
TARGET_MULTIPLIER = 0.8  # TP = entry - (range * 0.8)
STOP_MULTIPLIER = 0.1  # SL = entry + (range * 0.1)
MAX_CONCURRENT = 1


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
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


def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


def run_burst_backtest(candles, burst_thresh, target_mult, stop_mult, max_concurrent):
    """
    Burst fade backtest.
    - Detect burst candle (range >= threshold %)
    - SHORT: entry = high + 0.5%, TP = entry - (range * mult), SL = entry + (range * stop_mult)
    """
    cash = STARTING_CASH
    positions = []
    closes = 0
    wins = 0
    losses = 0
    total_volume = 0.0
    total_fees = 0.0
    signals = 0
    skipped_signals = 0

    for candle in candles:
        ts = int(candle["start"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        open_price = float(candle["open"])

        # Calculate candle range %
        mid = (open_price + close) / 2 if (open_price + close) > 0 else 1
        range_pct = (high - low) / mid * 100

        # Process exits
        still_open = []
        for pos in positions:
            fee_rate = get_fee_rate(total_volume)
            h = high
            l = low
            ep = pos["entry"]
            tp = pos["target"]
            sp = pos["stop"]
            tq = pos["quote"]
            units = tq / ep  # approximate units

            closed = False
            exit_reason = None
            exit_price = None

            # Since this is a SHORT, we profit when price goes DOWN
            # TP hits when price drops to target (below entry)
            if l <= tp:
                exit_price = tp
                exit_reason = "tp"
            # SL hits when price rises above stop
            elif h >= sp:
                exit_price = sp
                exit_reason = "stop"

            if exit_price is not None:
                # For short: profit = (entry - exit) * units
                gross = (ep - exit_price) * units
                entry_fee = tq * fee_rate
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += tq + net
                closes += 1
                total_volume += tq + (exit_price * units)
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                closed = True

            if not closed:
                still_open.append(pos)

        positions = still_open

        # Check for burst entry
        free_slots = max_concurrent - len(positions)
        if free_slots > 0 and cash >= 10.0 and range_pct >= burst_thresh:
            signals += 1

            fee_rate = get_fee_rate(total_volume)
            burst_high = high

            # Entry slightly above burst high (fade the pump)
            ep = burst_high * 1.005
            # Target: mean reversion — drop back down by fraction of the range
            tp = ep * (1 - range_pct / 100 * target_mult)
            # Stop: tight, just above entry
            sp = ep * (1 + range_pct / 100 * stop_mult)

            tq = min(cash * 0.95, cash / max_concurrent)
            if tq < 10.0:
                skipped_signals += 1
                continue

            positions.append({
                "entry": ep,
                "target": tp,
                "stop": sp,
                "quote": tq,
                "range_pct": range_pct,
                "burst_high": burst_high,
            })
            cash -= tq

    # Close remaining positions at last candle price
    if positions:
        last_close = float(candles[-1]["close"])
        for pos in positions:
            fee_rate = get_fee_rate(total_volume)
            ep = pos["entry"]
            tq = pos["quote"]
            units = tq / ep
            gross = (ep - last_close) * units
            entry_fee = tq * fee_rate
            exit_fee = last_close * units * fee_rate
            net = gross - entry_fee - exit_fee
            cash += tq + net
            closes += 1
            total_volume += tq + (last_close * units)
            total_fees += entry_fee + exit_fee
            if net > 0:
                wins += 1
            else:
                losses += 1

    total_pnl = cash - STARTING_CASH
    return_pct = total_pnl / STARTING_CASH * 100
    wr = wins / max(1, closes) * 100

    return {
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "win_rate": round(wr, 1),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "skipped_signals": skipped_signals,
        "total_fees": round(total_fees, 2),
        "total_volume": round(total_volume, 2),
        "avg_pnl_per_trade": round(total_pnl / max(1, closes), 2),
        "final_cash": round(cash, 2),
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"BURST FADE ON RAVE — {WINDOW_DAYS}d backtest", flush=True)
    print(f"=" * 70, flush=True)

    print(f"\nFetching {WINDOW_DAYS}d M5 candles for {PRODUCT}...", flush=True)
    candles = fetch_candles(client, PRODUCT, start, now)
    print(f"  {PRODUCT}: {len(candles)} candles", flush=True)

    if len(candles) < 100:
        print("  ERROR: Insufficient data", flush=True)
        return

    # Analyze candle ranges to find optimal burst threshold
    ranges = []
    for c in candles:
        o = float(c["open"])
        c_ = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])
        mid = (o + c_) / 2 if (o + c_) > 0 else 1
        range_pct = (h - l) / mid * 100
        ranges.append(range_pct)

    print(f"\nCandle range statistics:", flush=True)
    print(f"  Mean: {statistics.mean(ranges):.2f}%", flush=True)
    print(f"  Median: {statistics.median(ranges):.2f}%", flush=True)
    print(f"  P90: {sorted(ranges)[int(len(ranges)*0.9)]:.2f}%", flush=True)
    print(f"  P95: {sorted(ranges)[int(len(ranges)*0.95)]:.2f}%", flush=True)
    print(f"  Max: {max(ranges):.2f}%", flush=True)

    # Test multiple burst parameter sets
    print(f"\n{'='*70}", flush=True)
    print("PARAMETER SWEEP", flush=True)
    print(f"{'='*70}", flush=True)

    param_sets = [
        {"name": "BAL-default", "bt": 2.0, "tm": 0.8, "sm": 0.1},
        {"name": "RAVE-tight", "bt": 3.0, "tm": 0.6, "sm": 0.1},
        {"name": "RAVE-wide", "bt": 3.0, "tm": 1.0, "sm": 0.1},
        {"name": "RAVE-aggressive", "bt": 2.0, "tm": 1.0, "sm": 0.15},
        {"name": "RAVE-conservative", "bt": 4.0, "tm": 0.5, "sm": 0.05},
        {"name": "RAVE-extreme", "bt": 5.0, "tm": 0.8, "sm": 0.1},
    ]

    results = []
    for ps in param_sets:
        result = run_burst_backtest(candles, ps["bt"], ps["tm"], ps["sm"], MAX_CONCURRENT)
        result["params"] = ps
        results.append(result)
        print(f"  {ps['name']:>20s}: PnL=${result['net_pnl']:>8.2f} WR={result['win_rate']:>5.1f}% "
              f"Trades={result['closes']:>4} Signals={result['signals']:>4}", flush=True)

    # Compare with RSI MR
    print(f"\n{'='*70}", flush=True)
    print("BURST vs RSI MR ON RAVE", flush=True)
    print(f"{'='*70}", flush=True)

    best_burst = max(results, key=lambda r: r["net_pnl"])
    print(f"\n  Best burst config: {best_burst['params']['name']}", flush=True)
    print(f"    PnL: ${best_burst['net_pnl']:.2f} | WR: {best_burst['win_rate']:.1f}% | "
          f"Trades: {best_burst['closes']} | Signals: {best_burst['signals']}", flush=True)

    # Reference: RSI MR results from previous runs
    print(f"\n  RSI MR (RSI3/25%/48): ~$270-344 | WR: ~60% | Trades: ~64-72", flush=True)
    print(f"  RSI MR (RSI4/40%/48): ~$312-344 | WR: ~58% | Trades: ~59-64", flush=True)

    if best_burst["net_pnl"] > 200:
        print(f"\n  → BURST IS COMPETITIVE with RSI MR on RAVE", flush=True)
    elif best_burst["net_pnl"] > 50:
        print(f"\n  → BURST IS PROFITABLE but below RSI MR", flush=True)
    else:
        print(f"\n  → BURST FAILS on RAVE — shorting pumps doesn't work", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "product": PRODUCT,
        "candle_count": len(candles),
        "range_stats": {
            "mean": round(statistics.mean(ranges), 2),
            "median": round(statistics.median(ranges), 2),
            "p90": round(sorted(ranges)[int(len(ranges)*0.9)], 2),
            "p95": round(sorted(ranges)[int(len(ranges)*0.95)], 2),
            "max": round(max(ranges), 2),
        },
        "parameter_sweep": results,
        "best_burst": {
            "config": best_burst["params"]["name"],
            "net_pnl": best_burst["net_pnl"],
            "win_rate": best_burst["win_rate"],
            "closes": best_burst["closes"],
            "signals": best_burst["signals"],
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
