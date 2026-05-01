#!/usr/bin/env python3
"""
Long-Only Burst-to-Reclaim Backtest — Spot-native volatility capture.

Detects big DOWN candles (flushes), waits for reclaim confirmation,
then buys the rebound. This is the spot-native equivalent of burst-fade.

Strategy:
1. Detect flush: 5m candle range >= threshold AND close near low (bearish)
2. Wait for reclaim: next candle's close > flush midpoint
3. Entry: buy on reclaim confirmation candle's close
4. TP: flush high (mean reversion to pre-flush level)
5. SL: flush low - buffer (if price keeps dropping, exit)

Parameters swept:
- Flush threshold: 2%, 3%, 4%, 5%
- TP: flush high, 80% of range, 60% of range
- SL: 5% below flush low, 10% below, 20% below

Output: reports/burst_reclaim_long_results.json
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
OUTPUT_PATH = ROOT / "reports" / "burst_reclaim_long_results.json"

COINS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD"]
BTC = "BTC-USD"
WINDOW_DAYS = 30
STARTING_CASH = 48.0


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


def run_reclaim_backtest(candles, flush_thresh, tp_pct_of_range, sl_buffer_pct):
    """
    Long-only burst-to-reclaim backtest.

    1. Detect flush: range >= thresh, close in bottom 30% of range
    2. Wait for reclaim: next candle close > flush midpoint
    3. Buy on reclaim confirmation
    4. TP = entry + (flush_high - flush_low) * tp_pct
    5. SL = flush_low * (1 - sl_buffer)
    """
    cash = STARTING_CASH
    position = None
    closes = 0
    wins = 0
    losses = 0
    total_volume = 0.0
    total_fees = 0.0
    signals = 0
    flushes_detected = 0
    reclaim_missed = 0  # flush detected but no reclaim

    i = 0
    while i < len(candles):
        candle = candles[i]
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        open_price = float(candle["open"])

        # Calculate candle range %
        mid = (open_price + close) / 2 if (open_price + close) > 0 else 1
        range_pct = (high - low) / mid * 100

        # EXIT logic
        if position:
            fee_rate = get_fee_rate(total_volume)
            pos = position
            pos["hold"] += 1

            exit_price = None
            exit_reason = None

            # TP: price reaches target above entry
            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            # SL: price drops to stop below entry
            elif low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "stop"
            # Max hold timeout
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = pos["units"]
                gross = (exit_price - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes += 1
                total_volume += pos["q"] + (exit_price * units)
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                position = None

        # ENTRY logic (only if no position)
        if position is None and i >= 1 and cash >= 10.0:
            prev = candles[i - 1]
            prev_open = float(prev["open"])
            prev_close = float(prev["close"])
            prev_high = float(prev["high"])
            prev_low = float(prev["low"])
            prev_mid = (prev_open + prev_close) / 2 if (prev_open + prev_close) > 0 else 1
            prev_range = (prev_high - prev_low) / prev_mid * 100

            # Detect flush: big range, close near low (bearish candle)
            is_bearish = prev_close < prev_open
            close_position = (prev_close - prev_low) / (prev_high - prev_low) if prev_high > prev_low else 0.5

            if prev_range >= flush_thresh and is_bearish and close_position < 0.3:
                flushes_detected += 1

                # Check for reclaim: current candle close > flush midpoint
                flush_mid = (prev_high + prev_low) / 2

                if close > flush_mid:
                    # RECLAIM CONFIRMED — buy the rebound
                    signals += 1

                    fee_rate = get_fee_rate(total_volume)
                    deploy = cash * 0.95  # deploy most cash
                    entry_fee = deploy * close
                    entry_price = close
                    units = (deploy - entry_fee) / entry_price

                    # TP: reclaim to flush high (or fraction of range)
                    flush_range = prev_high - prev_low
                    tp = entry_price + flush_range * tp_pct_of_range

                    # SL: below flush low with buffer
                    sl = prev_low * (1 - sl_buffer_pct)

                    position = {
                        "ep": entry_price,
                        "q": deploy,
                        "units": units,
                        "tp": tp,
                        "sl": sl,
                        "hold": 0,
                        "max_hold": 48,  # 4-hour max hold
                        "entry_fee": entry_fee,
                        "flush_high": prev_high,
                        "flush_low": prev_low,
                        "flush_range_pct": round(prev_range, 2),
                    }
                else:
                    reclaim_missed += 1

        i += 1

    # Close remaining position
    if position:
        last_close = float(candles[-1]["close"])
        fee_rate = get_fee_rate(total_volume)
        units = position["units"]
        gross = (last_close - position["ep"]) * units
        entry_fee = position["entry_fee"]
        exit_fee = last_close * units * fee_rate
        net = gross - entry_fee - exit_fee
        cash += position["q"] + net
        closes += 1
        total_volume += position["q"] + (last_close * units)
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
        "flushes_detected": flushes_detected,
        "reclaim_missed": reclaim_missed,
        "reclaim_rate": round(signals / max(1, flushes_detected) * 100, 1),
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
    print(f"LONG-ONLY BURST-TO-RECLAIM BACKTEST — {WINDOW_DAYS}d", flush=True)
    print(f"=" * 70, flush=True)

    # Fetch candles
    coin_candles = {}
    for coin in COINS:
        print(f"Fetching {WINDOW_DAYS}d candles for {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        coin_candles[coin] = candles
        print(f"  {coin}: {len(candles)} candles", flush=True)

    # Parameter sweep
    param_sets = [
        {"name": "aggressive", "ft": 2.0, "tp": 1.0, "sl": 0.05},
        {"name": "balanced", "ft": 3.0, "tp": 0.8, "sl": 0.10},
        {"name": "conservative", "ft": 4.0, "tp": 0.6, "sl": 0.15},
        {"name": "wide-tp", "ft": 3.0, "tp": 1.0, "sl": 0.10},
        {"name": "tight-sig", "ft": 5.0, "tp": 0.8, "sl": 0.05},
    ]

    all_results = {}

    for coin in COINS:
        candles = coin_candles[coin]
        print(f"\n{'='*70}", flush=True)
        print(f"{coin} — PARAMETER SWEEP", flush=True)
        print(f"{'='*70}", flush=True)

        coin_results = []
        for ps in param_sets:
            result = run_reclaim_backtest(candles, ps["ft"], ps["tp"], ps["sl"])
            result["params"] = ps
            coin_results.append(result)
            print(f"  {ps['name']:>15s}: PnL=${result['net_pnl']:>8.2f} WR={result['win_rate']:>5.1f}% "
                  f"Trades={result['closes']:>3} Signals={result['signals']:>3} "
                  f"Flushes={result['flushes_detected']:>3} Reclaim%={result['reclaim_rate']:>5.1f}%", flush=True)

        best = max(coin_results, key=lambda r: r["net_pnl"])
        all_results[coin] = {
            "sweep": coin_results,
            "best": {
                "config": best["params"]["name"],
                "net_pnl": best["net_pnl"],
                "win_rate": best["win_rate"],
                "closes": best["closes"],
                "signals": best["signals"],
                "flushes_detected": best["flushes_detected"],
                "reclaim_rate": best["reclaim_rate"],
            },
        }

        print(f"  → BEST: {best['params']['name']} — ${best['net_pnl']:.2f}, "
              f"{best['win_rate']:.1f}% WR, {best['closes']} trades", flush=True)

    # Summary table
    print(f"\n{'='*70}", flush=True)
    print("CROSS-COIN SUMMARY", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Coin':<12} | {'Best Config':<15} | {'PnL':>8} | {'WR':>5} | "
          f"Trades | Signals | Flushes | Reclaim%", flush=True)
    print(f"{'-'*12}-+-{'-'*15}-+-{'-'*8}-+-{'-'*5}-+-"
          f"{'-'*6}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}", flush=True)

    for coin, data in all_results.items():
        b = data["best"]
        print(f"{coin:<12} | {b['config']:<15} | ${b['net_pnl']:>7.2f} | "
              f"{b['win_rate']:>4.1f}% | {b['closes']:>6} | {b['signals']:>7} | "
              f"{b['flushes_detected']:>7} | {b['reclaim_rate']:>7.1f}%", flush=True)

    # Compare with other strategies
    print(f"\n{'='*70}", flush=True)
    print("STRATEGY COMPARISON (spot-valid only)", flush=True)
    print(f"{'='*70}", flush=True)

    best_overall = max(all_results.values(), key=lambda d: d["best"]["net_pnl"])
    print(f"  Best burst-reclaim: {max(all_results, key=lambda c: all_results[c]['best']['net_pnl'])} "
          f"— ${best_overall['best']['net_pnl']:.2f}, {best_overall['best']['win_rate']:.1f}% WR", flush=True)
    print(f"  RSI MR (RAVE): ~$270-344, ~60% WR", flush=True)
    print(f"  Momentum (RAVE): ~$642, 61.1% WR", flush=True)
    print(f"  BB Reversion (IOTX): ~$44, 79.1% WR", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "coins": COINS,
        "param_sets": [{"name": p["name"], "flush_thresh": p["ft"], "tp_pct_range": p["tp"], "sl_buffer": p["sl"]} for p in param_sets],
        "results": {
            coin: {
                "sweep": [
                    {
                        "config": r["params"]["name"],
                        "net_pnl": r["net_pnl"],
                        "win_rate": r["win_rate"],
                        "closes": r["closes"],
                        "signals": r["signals"],
                        "flushes_detected": r["flushes_detected"],
                        "reclaim_rate": r["reclaim_rate"],
                        "total_fees": r["total_fees"],
                    }
                    for r in data["sweep"]
                ],
                "best": data["best"],
            }
            for coin, data in all_results.items()
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
