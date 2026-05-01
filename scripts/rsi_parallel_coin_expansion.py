#!/usr/bin/env python3
"""
RSI Parallel Coin Expansion — Test all 15 profitable coins
from @main's 39-coin scan with rsi_parallel cross-entry architecture.

Tests: RAVE, FARTCOIN, FET, TRUMP, IOTX, BAL, ALEPH, DASH, CFG, IRYS, MON, SKL, VVV, LDO, STORJ
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

ALL_COINS = [
    "RAVE-USD", "FARTCOIN-USD", "FET-USD", "TRUMP-USD", "IOTX-USD",
    "BAL-USD", "ALEPH-USD", "DASH-USD", "CFG-USD", "IRYS-USD",
    "MON-USD", "SKL-USD", "VVV-USD", "LDO-USD", "STORJ-USD",
]

BTC = "BTC-USD"

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
            if not cands: break
            time.sleep(0.2)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result

def run_rsi_single(candles, params, fee_bps=40):
    """Run rsi_parallel engine on a single coin."""
    starting_cash = 12.0  # Per-coin allocation
    fee_rate = fee_bps / 10000.0

    cash = starting_cash
    realized = 0.0
    closes = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    in_position = False
    price_hist = []
    entry_price = 0
    entry_fee = 0
    qty = 0
    tp = 0
    sl = 0
    ob = 0
    p = 0
    os = 0
    current_bar = 0
    rsi_below_os = False

    for i in range(len(candles)):
        c = candles[i]
        cl = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])

        price_hist.append(cl)
        if len(price_hist) > 100:
            price_hist = price_hist[-100:]
        current_bar += 1

        # Exit
        if in_position:
            rsi_vals = compute_rsi(price_hist, p)
            rsi_val = rsi_vals[-1] if rsi_vals else 50

            exit_price = None
            if h >= entry_price * (1 + tp):
                exit_price = entry_price * (1 + tp)
            elif l <= entry_price * (1 - sl):
                exit_price = entry_price * (1 - sl)
            elif rsi_val >= ob:
                exit_price = cl

            if exit_price is not None:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                realized += net
                closes += 1
                total_fees += entry_fee + exit_fee
                cash += exit_price * qty - exit_fee
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                in_position = False
                rsi_below_os = False

        # Entry (cross detection)
        if not in_position:
            rsi_vals = compute_rsi(price_hist, params["p"])
            rsi_val = rsi_vals[-1] if rsi_vals else 50
            was_below = rsi_below_os
            is_below = rsi_val <= params["os"]

            if is_below and not was_below:
                deploy = cash
                if deploy >= 1.0:
                    entry_fee = cl * (deploy / cl) * fee_rate
                    qty = (deploy - entry_fee) / cl
                    if qty > 0:
                        cash -= deploy
                        total_fees += entry_fee
                        in_position = True
                        entry_price = cl
                        tp = params["t"] / 100.0
                        sl = params["s"] / 100.0
                        ob = params["ob"]
                        p = params["p"]
                        os = params["os"]

            rsi_below_os = is_below

    if in_position:
        cash += entry_price * qty

    net = cash + realized - starting_cash
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "wr": round(wins / max(1, closes) * 100, 1),
        "fees": round(total_fees, 2),
        "avg_trade": round(net / max(1, closes), 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    # Load optimal params
    params_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "rsi_optimal_params.json")
    with open(params_path) as f:
        all_params = json.load(f)

    print(f"RSI PARALLEL COIN EXPANSION — {days} days, {len(ALL_COINS)} coins")
    print(f"{'=' * 90}")
    print(f"{'Coin':<18} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Fees':>8} {'Avg/Tr':>8} {'Signals/Day':>12}")
    print("-" * 90)

    results = []
    failed = []

    for coin in ALL_COINS:
        try:
            candles = fetch_candles(client, coin, start, now)
            if len(candles) < 50:
                failed.append(coin)
                continue

            params = all_params.get(coin)
            if not params:
                failed.append(coin)
                print(f"{coin:<18} NO PARAMS")
                continue

            r = run_rsi_single(candles, params)
            signals_per_day = r["closes"] / max(1, days)
            results.append({"coin": coin, **r, "signals_per_day": round(signals_per_day, 1)})
            print(f"{coin:<18} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['fees']:>7.2f} ${r['avg_trade']:>7.2f} {signals_per_day:>11.1f}")
        except Exception as e:
            failed.append(coin)
            print(f"{coin:<18} ERROR: {e}")

    # Sort by net profit
    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\n{'=' * 90}")
    print(f"TOP COINS FOR rsi_parallel EXPANSION")
    print(f"{'=' * 90}")
    print(f"{'Rank':<5} {'Coin':<18} {'Net $':>8} {'Trades':>7} {'WR%':>6} {'Signals/Day':>12}")
    print("-" * 70)
    for rank, r in enumerate(results[:15], 1):
        print(f"{rank:<5} {r['coin']:<18} ${r['net']:>7.2f} {r['closes']:>7} {r['wr']:>5.1f}% {r['signals_per_day']:>11.1f}")

    # Combined portfolio simulation
    print(f"\n{'=' * 90}")
    print(f"COMBINED PORTFOLIO SIMULATION")
    print(f"{'=' * 90}")

    # Try different coin combinations
    for n_coins in [4, 6, 8, 10, 12, 15]:
        top_n = results[:n_coins]
        total_trades = sum(r["closes"] for r in top_n)
        total_wins = sum(r["wins"] for r in top_n)
        total_fees = sum(r["fees"] for r in top_n)
        total_net = sum(r["net"] for r in top_n)
        starting = 12.0 * n_coins
        wr = total_wins / max(1, total_trades) * 100
        signals_per_day = total_trades / max(1, days)

        print(f"  Top {n_coins:2d} coins: ${total_net:>8.2f} ({total_net/starting*100:.1f}%) "
              f"{total_trades:>4} trades {wr:.1f}%WR "
              f"${total_fees:.2f} fees "
              f"{signals_per_day:.1f} signals/day")

    # Failed coins
    if failed:
        print(f"\n  Failed/No data: {', '.join(failed)}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "results": results,
        "failed": failed,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "rsi_parallel_coin_expansion.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
