#!/usr/bin/env python3
"""
Deep Trade Anatomy — Understanding WHY RSI exit 80 works and where the ceiling is.
Analyzes every trade from the champion config to find patterns.
"""
import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
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
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h RAVE data for Deep Anatomy...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"  RAVE: {len(rave_candles)} candles")

    # Run 3 configs and compare trade-by-trade
    configs_to_test = [
        {"name": "CROWN_JEWEL", "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3, "max_hold": 24, "rsi_exit_ob": 0, "compound": False},
        {"name": "RSI_EXIT_80", "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 7, "max_hold": 24, "rsi_exit_ob": 80, "compound": False},
        {"name": "NO_SL", "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 999, "max_hold": 24, "rsi_exit_ob": 0, "compound": False},
    ]

    all_results = {}

    for cfg in configs_to_test:
        cash = 48.0
        pos = None
        closes = 0
        wins = 0
        total_volume = 0.0
        total_fees = 0.0
        history = []
        trades = []
        exit_reasons = {"tp": 0, "sl": 0, "timeout": 0, "rsi_ob": 0}

        for i in range(len(rave_candles)):
            c = rave_candles[i]
            ts = int(c["start"])
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            history.append(cl)
            if len(history) > 200: history.pop(0)

            # BTC Gate
            btc_gate = True
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_gate = False

            # Session Gate
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            session_gate = (hour not in [12, 19, 6, 0])

            # Fee Tier
            if total_volume >= 50000: fr = 0.0015
            elif total_volume >= 10000: fr = 0.0025
            else: fr = 0.0040

            # Exit
            if pos:
                pos["hold"] += 1
                exit_p = None
                exit_reason = None

                if cfg["rsi_exit_ob"] > 0 and len(history) >= cfg["rsi_period"] + 1:
                    rsi_now = compute_rsi(history, cfg["rsi_period"])
                    if rsi_now >= cfg["rsi_exit_ob"]:
                        exit_p = cl; exit_reason = "rsi_ob"

                if exit_p is None and h >= pos["tp"]:
                    exit_p = pos["tp"]; exit_reason = "tp"
                if exit_p is None and l <= pos["sl"]:
                    exit_p = pos["sl"]; exit_reason = "sl"
                if exit_p is None and pos["hold"] >= cfg["max_hold"]:
                    exit_p = cl; exit_reason = "timeout"

                if exit_p is not None:
                    units = pos["quote"] / pos["ep"]
                    pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                    cash += pos["quote"] + pnl
                    total_volume += pos["quote"] + (exit_p * units)
                    total_fees += pos["quote"] * fr + exit_p * units * fr
                    closes += 1
                    is_win = exit_p > pos["ep"]
                    if is_win: wins += 1
                    exit_reasons[exit_reason] += 1
                    
                    # Calculate max favorable excursion (how far did price go in our favor?)
                    max_favorable = 0
                    for j in range(pos["entry_bar"], i + 1):
                        cc = rave_candles[j]
                        ch = float(cc["high"])
                        excursion = (ch - pos["ep"]) / pos["ep"] * 100
                        if excursion > max_favorable:
                            max_favorable = excursion

                    trades.append({
                        "bar": i, "entry": pos["ep"], "exit": exit_p,
                        "pnl": round(pnl, 4), "win": is_win, "hold_bars": pos["hold"],
                        "reason": exit_reason, "rsi_at_entry": round(pos.get("rsi_entry", 0), 1),
                        "max_favorable_pct": round(max_favorable, 2),
                        "exit_return_pct": round((exit_p - pos["ep"]) / pos["ep"] * 100, 2),
                    })
                    pos = None

            # Entry
            if pos is None and cash >= 10.0 and btc_gate and session_gate:
                if len(history) >= cfg["rsi_period"] + 2:
                    rsi_prev = compute_rsi(history[:-1], cfg["rsi_period"])
                    if rsi_prev <= cfg["os_thresh"]:
                        ep = float(c["open"])
                        tq = cash * 0.95 if cfg["compound"] else 48.0
                        if tq > cash: tq = cash
                        if tq >= 10.0:
                            pos = {
                                "ep": ep, "quote": tq, "hold": 0,
                                "tp": ep * (1 + cfg["tp_pct"] / 100.0),
                                "sl": ep * (1 - cfg["sl_pct"] / 100.0),
                                "rsi_entry": rsi_prev,
                                "entry_bar": i,
                            }
                            cash -= tq

        if pos:
            cash += pos["quote"]

        net = cash - 48.0
        wr = wins / max(1, closes) * 100

        all_results[cfg["name"]] = {
            "net": round(net, 2), "return_pct": round(net / 48 * 100, 1),
            "closes": closes, "wr": round(wr, 1), "trades": trades,
            "exit_reasons": exit_reasons, "total_fees": round(total_fees, 2),
        }

    # ANALYSIS
    print("\n" + "=" * 100)
    print("DEEP TRADE ANATOMY — 3 Configs Compared")
    print("=" * 100)

    for name in ["CROWN_JEWEL", "RSI_EXIT_80", "NO_SL"]:
        r = all_results[name]
        print(f"\n--- {name}: ${r['net']:.2f} ({r['return_pct']}%) {r['closes']} trades {r['wr']}%WR ---")
        print(f"  Exit reasons:")
        for reason, count in r["exit_reasons"].items():
            if count > 0:
                pct = count / max(1, r["closes"]) * 100
                print(f"    {reason}: {count} ({pct:.1f}%)")

        # Trade size distribution
        wins = [t for t in r["trades"] if t["win"]]
        losses = [t for t in r["trades"] if not t["win"]]
        if wins:
            print(f"  Win stats: avg=${sum(t['pnl'] for t in wins)/len(wins):.2f}, "
                  f"median=${sorted(t['pnl'] for t in wins)[len(wins)//2]:.2f}, "
                  f"best=${max(t['pnl'] for t in wins):.2f}, "
                  f"worst=${min(t['pnl'] for t in wins):.2f}")
        if losses:
            print(f"  Loss stats: avg=${sum(t['pnl'] for t in losses)/len(losses):.2f}, "
                  f"median=${sorted(t['pnl'] for t in losses)[len(losses)//2]:.2f}, "
                  f"best=${max(t['pnl'] for t in losses):.2f}, "
                  f"worst=${min(t['pnl'] for t in losses):.2f}")

        # Max favorable excursion analysis
        if r["trades"]:
            avg_max_fav = sum(t["max_favorable_pct"] for t in r["trades"]) / len(r["trades"])
            print(f"  Avg max favorable excursion: {avg_max_fav:.1f}%")
            
            # How many trades went >25% favorable but didn't hit TP?
            if name == "CROWN_JEWEL":
                missed_tps = [t for t in r["trades"] if t["max_favorable_pct"] >= 25 and t["reason"] != "tp"]
                print(f"  Trades that reached 25%+ but didn't TP: {len(missed_tps)} (left money: "
                      f"${sum(t['pnl'] for t in missed_tps):.2f})")
                
                # How many SL trades would have been profitable without SL?
                sl_trades = [t for t in r["trades"] if t["reason"] == "sl"]
                sl_would_win = [t for t in sl_trades if t["max_favorable_pct"] > 3]
                print(f"  SL trades that recovered to +3%+: {len(sl_would_win)}/{len(sl_trades)}")

    # TRADE-BY-TRADE comparison: what trades did RSI_EXIT_80 capture that CROWN_JEWEL missed?
    cj_trades = {t["bar"]: t for t in all_results["CROWN_JEWEL"]["trades"]}
    re_trades = {t["bar"]: t for t in all_results["RSI_EXIT_80"]["trades"]}
    
    extra_trades = [bar for bar in re_trades if bar not in cj_trades]
    print(f"\n--- EXTRA trades captured by RSI_EXIT_80 vs CROWN_JEWEL: {len(extra_trades)} ---")
    
    # Trades that CROWN_JEWEL got SL'd on but RSI_EXIT_80 held and won
    cj_sl = {t["bar"]: t for t in all_results["CROWN_JEWEL"]["trades"] if t["reason"] == "sl"}
    re_wins_at_same_bars = {bar: re_trades[bar] for bar in cj_sl if bar in re_trades and re_trades[bar]["win"]}
    print(f"  CROWN_JEWEL SL'd trades that RSI_EXIT_80 turned into wins: {len(re_wins_at_same_bars)}")
    if re_wins_at_same_bars:
        total_recovered = sum(t["pnl"] for t in re_wins_at_same_bars.values())
        print(f"  Total recovered from those: ${total_recovered:.2f}")

    # Save
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "deep_trade_anatomy.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    summary = {name: {k: v for k, v in r.items() if k != "trades"} for name, r in all_results.items()}
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to {output_path}")
    print(f"Full trade data available in memory for detailed analysis.")

if __name__ == "__main__":
    main()
