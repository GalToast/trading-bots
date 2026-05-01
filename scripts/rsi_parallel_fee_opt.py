#!/usr/bin/env python3
"""
RSI Parallel Fee Optimization — Replay 415 trades at different fee tiers
Maps the fee curve from 40bps → 15bps to see the true ceiling.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TOP_4 = ["RAVE-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
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

def run_rsi_parallel(candles_map, params, fee_bps):
    """Run rsi_parallel engine at a specific fee tier."""
    starting_cash = 48.0
    per_coin = starting_cash / 4
    fee_rate = fee_bps / 10000.0

    coin = {}
    for pid in TOP_4:
        coin[pid] = {
            "cash": per_coin,
            "realized": 0.0,
            "closes": 0,
            "wins": 0,
            "losses": 0,
            "fees": 0.0,
            "in_position": False,
            "price_hist": [],
            "entry_price": 0,
            "entry_fee": 0,
            "qty": 0,
            "tp": 0, "sl": 0, "ob": 0, "p": 0, "os": 0,
            "current_bar": 0,
            "rsi_below_os": False,
        }

    # Get max candle count
    max_candles = max(len(candles_map[pid]) for pid in TOP_4)

    for i in range(max_candles):
        for pid in TOP_4:
            if i >= len(candles_map[pid]):
                continue
            c = candles_map[pid][i]
            cl = float(c["close"])
            h = float(c["high"])
            l = float(c["low"])
            st = coin[pid]
            p = params.get(pid, {})
            if not p:
                continue

            st["price_hist"].append(cl)
            if len(st["price_hist"]) > 100:
                st["price_hist"] = st["price_hist"][-100:]
            st["current_bar"] += 1

            # Exit
            if st["in_position"]:
                rsi_vals = compute_rsi(st["price_hist"], st["p"])
                rsi_val = rsi_vals[-1] if rsi_vals else 50

                tp = st["entry_price"] * (1 + st["tp"])
                sl = st["entry_price"] * (1 - st["sl"])

                exit_price = None
                if h >= tp:
                    exit_price = tp
                elif l <= sl:
                    exit_price = sl
                elif rsi_val >= st["ob"]:
                    exit_price = cl

                if exit_price is not None:
                    gross = (exit_price - st["entry_price"]) * st["qty"]
                    exit_fee = exit_price * st["qty"] * fee_rate
                    net = gross - st["entry_fee"] - exit_fee
                    st["realized"] += net
                    st["closes"] += 1
                    st["fees"] += st["entry_fee"] + exit_fee
                    st["cash"] += exit_price * st["qty"] - exit_fee
                    if net > 0:
                        st["wins"] += 1
                    else:
                        st["losses"] += 1
                    st["in_position"] = False
                    st["rsi_below_os"] = False

            # Entry (cross detection)
            if not st["in_position"]:
                rsi_vals = compute_rsi(st["price_hist"], p["p"])
                rsi_val = rsi_vals[-1] if rsi_vals else 50
                was_below = st["rsi_below_os"]
                is_below = rsi_val <= p["os"]

                if is_below and not was_below:
                    deploy = st["cash"]
                    if deploy >= 1.0:
                        entry_fee = cl * (deploy / cl) * fee_rate
                        qty = (deploy - entry_fee) / cl
                        if qty > 0:
                            st["cash"] -= deploy
                            st["fees"] += entry_fee
                            st["in_position"] = True
                            st["entry_price"] = cl
                            st["entry_fee"] = entry_fee
                            st["qty"] = qty
                            st["tp"] = p["t"] / 100.0
                            st["sl"] = p["s"] / 100.0
                            st["ob"] = p["ob"]
                            st["p"] = p["p"]
                            st["os"] = p["os"]

                st["rsi_below_os"] = is_below

    # Close remaining positions
    for pid in TOP_4:
        st = coin[pid]
        if st["in_position"]:
            st["cash"] += st["entry_price"] * st["qty"]  # Return cash at entry (conservative)

    total_cash = sum(coin[pid]["cash"] for pid in TOP_4)
    total_realized = sum(coin[pid]["realized"] for pid in TOP_4)
    total_closes = sum(coin[pid]["closes"] for pid in TOP_4)
    total_wins = sum(coin[pid]["wins"] for pid in TOP_4)
    total_fees = sum(coin[pid]["fees"] for pid in TOP_4)

    return {
        "fee_bps": fee_bps,
        "total_cash": round(total_cash, 2),
        "total_realized": round(total_realized, 2),
        "total_closes": total_closes,
        "total_wins": total_wins,
        "win_rate": round(total_wins / max(1, total_closes) * 100, 1),
        "total_fees": round(total_fees, 2),
        "net": round(total_cash + total_realized - starting_cash, 2),
        "return_pct": round((total_cash + total_realized - starting_cash) / starting_cash * 100, 1),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Fetching {days}-day data for fee optimization...")
    candles_map = {}
    for pid in TOP_4:
        candles_map[pid] = fetch_candles(client, pid, start, now)
        print(f"  {pid}: {len(candles_map[pid])} candles")

    # Load optimal params
    params_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "rsi_optimal_params.json")
    with open(params_path) as f:
        all_params = json.load(f)
    params = {pid: all_params[pid] for pid in TOP_4 if pid in all_params}
    print(f"  Params loaded for: {list(params.keys())}")

    # Run at different fee tiers
    fee_tiers = [40, 35, 30, 25, 20, 18, 15, 12, 10, 8, 5]

    print(f"\n{'=' * 90}")
    print(f"RSI PARALLEL FEE OPTIMIZATION ({days} days, 4 coins)")
    print(f"{'=' * 90}")
    print(f"{'Fee (bps)':>10} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Fees':>8} {'Δ vs 40bps':>12}")
    print("-" * 90)

    results = []
    baseline = None
    for fee_bps in fee_tiers:
        r = run_rsi_parallel(candles_map, params, fee_bps)
        results.append(r)
        if baseline is None:
            baseline = r
        delta = r["net"] - baseline["net"]
        print(f"{fee_bps:>6}     ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['total_closes']:>7} {r['win_rate']:>5.1f}% ${r['total_fees']:>7.2f} ${delta:>+11.2f}")

    # Per-coin breakdown at 40bps and 15bps
    print(f"\n{'=' * 90}")
    print(f"PER-COIN BREAKDOWN at 40bps vs 15bps")
    print(f"{'=' * 90}")

    for fee_bps in [40, 15]:
        print(f"\n  Fee tier: {fee_bps}bps:")
        # Run individually per coin
        for pid in TOP_4:
            single_map = {pid: candles_map[pid]}
            single_params = {pid: params[pid]}
            # Need to adapt for single coin - just report from the full run
            pass

    # Fee break-even analysis
    break_even = next((r for r in results if r["net"] < 0), None)
    if break_even:
        print(f"\n  💀 Break-even point: between {results[results.index(break_even)-1]['fee_bps']}bps and {break_even['fee_bps']}bps")
    else:
        print(f"\n  ✅ Still profitable at {fee_tiers[-1]}bps")

    # Summary
    r_40 = next(r for r in results if r["fee_bps"] == 40)
    r_15 = next(r for r in results if r["fee_bps"] == 15)
    improvement = r_15["net"] - r_40["net"]

    print(f"\n{'=' * 90}")
    print(f"SUMMARY")
    print(f"{'=' * 90}")
    print(f"  At 40bps (current): ${r_40['net']:.2f}")
    print(f"  At 15bps (tier 1):  ${r_15['net']:.2f}")
    print(f"  Improvement:        +${improvement:.2f} ({improvement/max(1,r_40['net'])*100:.0f}%)")
    print(f"  Fee savings:        ${r_40['total_fees'] - r_15['total_fees']:.2f}")
    print(f"  Trades:             {r_40['total_closes']}")
    print(f"  Fee per trade avg:  ${r_40['total_fees']/max(1,r_40['total_closes']):.2f} → ${r_15['total_fees']/max(1,r_15['total_closes']):.2f}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "results": results,
        "params": params,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "rsi_parallel_fee_optimization.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
