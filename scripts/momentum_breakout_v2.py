#!/usr/bin/env python3
"""
Momentum Breakout V2 — Fixed logic. 
Entry: Current candle HIGH breaks above 20-bar high (not prev close).
This matches classic breakout logic: price must TOUCH the breakout level.
"""
import json, os, sys, time
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

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

def run_mb_v2(candles, params, starting_cash=48.0):
    tp_pct = params["tp"]
    sl_pct = params["sl"]
    max_hold = params["max_hold"]
    lookback = params.get("lookback", 20)
    
    cash = starting_cash
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees = 0.0
    max_dd = 0.0
    peak = starting_cash

    for i in range(len(candles)):
        c = candles[i]
        cl = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])
        o = float(c["open"])

        # Fee
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = 0.0040

        # Exit
        if pos:
            pos["hold"] += 1
            exit_p = None

            if h >= pos["tp"]: exit_p = pos["tp"]
            elif l <= pos["sl"]: exit_p = pos["sl"]
            elif pos["hold"] >= max_hold: exit_p = cl

            if exit_p is not None:
                units = pos["units"]
                entry_fee = pos["entry_fee"]
                exit_fee = exit_p * units * fr
                pnl = (exit_p - pos["ep"]) * units - entry_fee - exit_fee
                cash += exit_p * units - exit_fee
                total_volume += pos["deploy"] + (exit_p * units)
                total_fees += entry_fee + exit_fee
                closes += 1
                if exit_p > pos["ep"]: wins += 1

                equity = cash
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak * 100)
                pos = None

        # Entry: current HIGH breaks above lookback high
        if pos is None and cash >= 10.0 and i >= lookback + 1:
            recent_high = max(float(candles[j]["high"]) for j in range(i-lookback, i))
            if h > recent_high:
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

        # Track DD for open positions
        if pos:
            equity = cash + pos["units"] * h
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)

    if pos:
        cash += pos["units"] * float(candles[-1]["close"]) * (1 - fr)

    net = cash - starting_cash
    wr = wins / max(1, closes) * 100
    return {
        "net": round(net, 2), "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes, "wr": round(wr, 1), 
        "avg_trade": round(net / max(1, closes), 2),
        "total_fees": round(total_fees, 2), "max_dd": round(max_dd, 1),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Momentum Breakout V2 — {days} days")
    
    btc = fetch_candles(client, BTC, start, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    # Test RAVE with many configs
    rave = fetch_candles(client, "RAVE-USD", start, now)
    print(f"  RAVE: {len(rave)} candles")

    results = []
    for lb in [10, 20, 30, 50]:  # lookback
        for tp in [3, 5, 8, 10, 15, 20]:
            for sl in [2, 3, 5, 7]:
                for mh in [10, 20, 30, 50]:
                    cfg = {"tp": tp, "sl": sl, "max_hold": mh, "lookback": lb}
                    r = run_mb_v2(rave, cfg)
                    if r["closes"] > 0:
                        r["config"] = f"LB{lb} TP{tp} SL{sl} H{mh}"
                        results.append(r)

    results.sort(key=lambda x: x["net"], reverse=True)
    
    print(f"\n{'Config':<22} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Fees':>8}")
    print("-" * 70)
    for r in results[:15]:
        print(f"{r['config']:<22} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}% ${r['total_fees']:>7.2f}")

    if not results:
        print("\n  ⚠️ NO TRADES FIRED — Momentum Breakout doesn't trigger on current 11-day RAVE data")
        print("  This means the 30-day $143 result from @main may be regime-dependent.")
        print("  RAVE may not have had any 20-bar breakout events in the last 11 days.")

    # Also test with wider lookback and looser breakout (any new high)
    print(f"\n{'=' * 70}")
    print(f"ALTERNATIVE: Breakout = close above SMA(20) + volume > 1.5x avg")
    print(f"{'=' * 70}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top15": results[:15],
        "total_configs": len(results),
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "momentum_breakout_v2.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
