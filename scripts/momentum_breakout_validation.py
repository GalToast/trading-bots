#!/usr/bin/env python3
"""
Momentum Breakout Independent Validation — @main found $143.83/30d on RAVE.
Validating on latest 11 days to confirm the edge is CURRENT.
Logic: Buy when price breaks above 20-bar high. TP 10%, SL 5%, max 30 bars.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TEST_COINS = {
    "RAVE-USD": {"tp": 10, "sl": 5, "max_hold": 30},
    "BAL-USD": {"tp": 10, "sl": 5, "max_hold": 30},
    "IOTX-USD": {"tp": 10, "sl": 5, "max_hold": 30},
}
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

def run_momentum_breakout(candles, btc_lookup, params, starting_cash=48.0):
    tp_pct = params["tp"]
    sl_pct = params["sl"]
    max_hold = params["max_hold"]
    
    cash = starting_cash
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees = 0.0
    max_dd = 0.0
    peak = starting_cash
    history = []
    trade_details = []

    for i in range(len(candles)):
        c = candles[i]
        cl = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])
        o = float(c["open"])

        history.append(cl)

        # Fee
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = 0.0040

        # Exit
        if pos:
            pos["hold"] += 1
            exit_p = None
            exit_reason = None

            if h >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            elif l <= pos["sl"]:
                exit_p = pos["sl"]; exit_reason = "sl"
            elif pos["hold"] >= max_hold:
                exit_p = cl; exit_reason = "timeout"

            if exit_p is not None:
                units = pos["units"]
                pnl = (exit_p - pos["ep"]) * units - (pos["entry_fee"]) - (exit_p * units * fr)
                cash += exit_p * units - exit_p * units * fr
                total_volume += pos["deploy"] + (exit_p * units)
                total_fees += pos["entry_fee"] + exit_p * units * fr
                closes += 1
                if exit_p > pos["ep"]: wins += 1

                equity = cash
                peak = max(peak, equity)
                dd = (peak - equity) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)

                trade_details.append({
                    "bar": i, "entry": pos["ep"], "exit": exit_p,
                    "pnl": round(pnl, 4), "win": exit_p > pos["ep"],
                    "hold": pos["hold"], "reason": exit_reason,
                })
                pos = None

        # Entry: breakout above 20-bar high
        if pos is None and cash >= 10.0 and len(history) >= 22:
            recent_high = max(float(candles[j]["high"]) for j in range(max(0, i-21), i))
            prev_close = history[-2] if len(history) >= 2 else 0
            # Breakout: previous close above recent high
            if prev_close > recent_high:
                deploy = cash * 0.95
                if deploy >= 10.0:
                    entry_fee = deploy * fr
                    units = (deploy - entry_fee) / o
                    if units > 0:
                        cash -= deploy
                        total_fees += entry_fee
                        pos = {
                            "ep": o, "deploy": deploy, "units": units,
                            "tp": o * (1 + tp_pct / 100.0),
                            "sl": o * (1 - sl_pct / 100.0),
                            "hold": 0,
                            "entry_fee": entry_fee,
                        }

        # Track DD
        equity = cash + (pos["deploy"] if pos else 0)
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)

    if pos:
        cash += pos["units"] * float(candles[-1]["close"]) * (1 - fr)

    net = cash - starting_cash
    wr = wins / max(1, closes) * 100
    avg_trade = net / max(1, closes)

    return {
        "net": round(net, 2), "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes, "wr": round(wr, 1), "avg_trade": round(avg_trade, 2),
        "total_fees": round(total_fees, 2), "max_dd": round(max_dd, 1),
        "trade_details": trade_details,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Validating Momentum Breakout on {days}-day data...")
    
    btc = fetch_candles(client, BTC, start, now, "FIVE_MINUTE")
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    results = []
    for coin, params in TEST_COINS.items():
        candles = fetch_candles(client, coin, start, now)
        print(f"  {coin}: {len(candles)} candles")
        r = run_momentum_breakout(candles, btc_lk, params)
        r["coin"] = coin
        results.append(r)
        print(f"    ${r['net']:.2f} ({r['return_pct']}%) {r['closes']} trades {r['wr']}%WR DD={r['max_dd']}%")

    # Also test wider TP/SL combos
    print(f"\n{'=' * 90}")
    print(f"PARAMETER SWEEP — RAVE Momentum Breakout")
    print(f"{'=' * 90}")

    rave = fetch_candles(client, "RAVE-USD", start, now)
    sweep_results = []
    for tp in [5, 10, 15, 20, 25]:
        for sl in [3, 5, 7, 10]:
            for mh in [20, 30, 40, 50]:
                cfg = {"tp": tp, "sl": sl, "max_hold": mh}
                r = run_momentum_breakout(rave, btc_lk, cfg)
                sweep_results.append({
                    "tp": tp, "sl": sl, "mh": mh, **r
                })

    sweep_results.sort(key=lambda x: x["net"], reverse=True)
    print(f"{'Config':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6}")
    print("-" * 60)
    for s in sweep_results[:10]:
        print(f"TP{s['tp']}% SL{s['sl']}% H{s['mh']:<2}   ${s['net']:>7.2f} {s['return_pct']:>6.1f}% {s['closes']:>7} {s['wr']:>5.1f}% {s['max_dd']:>5.1f}%")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "validation": results,
        "sweep_top10": sweep_results[:10],
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "momentum_breakout_validation.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

    # Verdict
    top = sweep_results[0]
    print(f"\n🏆 Best config: TP{top['tp']}% SL{top['sl']}% H{top['mh']} = ${top['net']:.2f} ({top['wr']}%WR, DD={top['max_dd']}%)")

if __name__ == "__main__":
    main()
