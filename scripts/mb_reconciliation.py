#!/usr/bin/env python3
"""
Momentum Breakout Reconciliation — Matching @main's exact engine.
Key differences from my V2:
1. @main deploys 100% of cash (tq = cash), I deployed 95%
2. @main uses CLOSE > recent_high for entry, I used HIGH > recent_high
3. @main uses geometric compounding, I used fixed fraction

This replicates @main's experiment_lab engine EXACTLY.
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

def get_fee(vol):
    if vol >= 50000: return 0.0015
    elif vol >= 10000: return 0.0025
    else: return 0.0040

def run_mb_exact(candles, btc_lk, lookback=5, tp_pct=10, sl_pct=10, max_hold=50, cash_start=48.0):
    """
    Exact replication of @main's experiment_lab engine for Momentum Breakout.
    Entry: CLOSE > lookback-bar high (not HIGH > high)
    Deploy: 100% of cash (tq = cash)
    Exit: strategy_fn returns exit price
    """
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    closes_data = []
    h = []  # close history
    cd = []  # (hi, lo, close, volume)
    pk = cash_start
    mdd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    for c in candles:
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        v = float(c.get("volume", 1.0))
        h.append(close)
        cd.append((hi, lo, close, v))
        if len(h) > 200:
            h.pop(0)
            cd.pop(0)

        # BTC gate
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001:
                boc = False

        # Session gate
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}:
            continue

        fr = get_fee(vol)

        # Exit
        if pos:
            pos["h"] += 1
            exit_p = None
            exit_reason = None

            if hi >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            elif lo <= pos["sl"]:
                exit_p = pos["sl"]; exit_reason = "sl"
            elif pos["max_hold"] and pos["h"] >= pos["max_hold"]:
                exit_p = close; exit_reason = "timeout"

            if exit_p is not None:
                u = pos["q"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * u - (pos["q"] * fr) - (exit_p * u * fr)
                if pnl > 0: gross_profit += pnl
                else: gross_loss += abs(pnl)
                cash += pos["q"] + pnl
                vol += pos["q"] + exit_p * u
                closes_count += 1
                if exit_p > pos["ep"]: wins += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd

                closes_data.append({
                    "exit_p": round(exit_p, 6), "entry_p": pos["ep"],
                    "pnl": round(pnl, 4), "win": exit_p > pos["ep"],
                    "reason": exit_reason, "hold": pos["h"],
                })
                pos = None

        # Entry: CLOSE > lookback-bar high
        if pos is None and cash >= 10 and boc and len(cd) >= lookback + 2:
            recent_high = max(c[0] for c in cd[-lookback-1:-1])  # previous lookback highs
            current_close = cd[-1][2]  # current candle close
            if current_close > recent_high:
                ep = float(c["open"])  # Use NEXT candle open (which is this candle's open since we're at close)
                # Wait - we're processing this candle already. The entry should be at NEXT candle's open.
                # But @main's engine uses float(c["open"]) which is THIS candle's open.
                # Since we're at the END of this candle, the "open" is the candle's open, not close.
                # This means we're entering at the candle's open even though we've already seen the close.
                # This is a LOOKAHEAD BUG — we know the close before entering at the open.
                
                tq = cash  # FULL deployment
                if tq >= 10:
                    pos = {
                        "ep": ep, "q": tq, "h": 0,
                        "tp_pct": tp_pct, "sl_pct": sl_pct,
                        "max_hold": max_hold,
                        "tp": ep * (1 + tp_pct / 100.0),
                        "sl": ep * (1 - sl_pct / 100.0),
                    }
                    cash -= tq

    # Close remaining position at last close
    if pos:
        u = pos["q"] / pos["ep"]
        pnl = (close - pos["ep"]) * u - (pos["q"] * fr) - (close * u * fr)
        if pnl > 0: gross_profit += pnl
        else: gross_loss += abs(pnl)
        cash += pos["q"] + pnl
        vol += pos["q"] + close * u
        closes_count += 1
        if close > pos["ep"]: wins += 1

    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    pf = gross_profit / max(0.01, gross_loss) if gross_loss > 0 else 999.0

    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "avg_trade": round(net / max(1, closes_count), 2),
        "total_fees": round(gross_profit + gross_loss - net if net > 0 else gross_profit + gross_loss + net, 2),
        "max_dd": round(mdd * 100, 1),
        "profit_factor": round(pf, 2),
        "closes_data": closes_data,
    }

def run_mb_no_lookahead(candles, btc_lk, lookback=5, tp_pct=10, sl_pct=10, max_hold=50, cash_start=48.0):
    """
    FIXED version: Entry at NEXT candle's open, no lookahead.
    We detect breakout at candle[i], enter at candle[i+1].open.
    """
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    closes_data = []
    h = []
    cd = []
    pk = cash_start
    mdd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    signal_pending = None  # (tp, sl, max_hold) from detected signal

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        v = float(c.get("volume", 1.0))
        h.append(close)
        cd.append((hi, lo, close, v))
        if len(h) > 200:
            h.pop(0)
            cd.pop(0)

        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001:
                boc = False

        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}:
            continue

        fr = get_fee(vol)

        # Exit
        if pos:
            pos["h"] += 1
            exit_p = None
            exit_reason = None

            if hi >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            elif lo <= pos["sl"]:
                exit_p = pos["sl"]; exit_reason = "sl"
            elif pos["max_hold"] and pos["h"] >= pos["max_hold"]:
                exit_p = close; exit_reason = "timeout"

            if exit_p is not None:
                u = pos["q"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * u - (pos["q"] * fr) - (exit_p * u * fr)
                if pnl > 0: gross_profit += pnl
                else: gross_loss += abs(pnl)
                cash += pos["q"] + pnl
                vol += pos["q"] + exit_p * u
                closes_count += 1
                if exit_p > pos["ep"]: wins += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd

                closes_data.append({
                    "exit_p": round(exit_p, 6), "entry_p": pos["ep"],
                    "pnl": round(pnl, 4), "win": exit_p > pos["ep"],
                    "reason": exit_reason, "hold": pos["h"],
                })
                pos = None

        # Entry: if signal pending from PREVIOUS candle, enter at THIS candle's open
        if pos is None and cash >= 10 and boc and signal_pending is not None:
            tp, sl, mh = signal_pending
            ep = float(c["open"])  # THIS candle's open — no lookahead
            tq = cash
            if tq >= 10:
                pos = {
                    "ep": ep, "q": tq, "h": 0,
                    "tp": ep * (1 + tp / 100.0),
                    "sl": ep * (1 - sl / 100.0),
                    "max_hold": mh,
                }
                cash -= tq
            signal_pending = None

        # Detect signal at THIS candle's close (for NEXT candle entry)
        if signal_pending is None and len(cd) >= lookback + 2:
            recent_high = max(c[0] for c in cd[-lookback-1:-1])
            current_close = cd[-1][2]
            if current_close > recent_high:
                signal_pending = (tp_pct, sl_pct, max_hold)

    if pos:
        u = pos["q"] / pos["ep"]
        pnl = (close - pos["ep"]) * u - (pos["q"] * fr) - (close * u * fr)
        if pnl > 0: gross_profit += pnl
        else: gross_loss += abs(pnl)
        cash += pos["q"] + pnl
        vol += pos["q"] + close * u
        closes_count += 1
        if close > pos["ep"]: wins += 1

    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    pf = gross_profit / max(0.01, gross_loss) if gross_loss > 0 else 999.0

    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "avg_trade": round(net / max(1, closes_count), 2),
        "max_dd": round(mdd * 100, 1),
        "profit_factor": round(pf, 2),
        "closes_data": closes_data,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"MB Reconciliation — {days} days")
    
    btc = fetch_candles(client, BTC, start, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    rave = fetch_candles(client, "RAVE-USD", start, now)
    print(f"  RAVE: {len(rave)} candles")

    # Test exact @main engine vs no-lookahead
    configs = [
        ("LB5 TP10 SL10 H50 EXACT (lookahead)", {"lb": 5, "tp": 10, "sl": 10, "mh": 50}, "exact"),
        ("LB5 TP10 SL10 H50 FIXED (no lookahead)", {"lb": 5, "tp": 10, "sl": 10, "mh": 50}, "fixed"),
        ("LB10 TP10 SL7 H50 EXACT", {"lb": 10, "tp": 10, "sl": 7, "mh": 50}, "exact"),
        ("LB10 TP10 SL7 H50 FIXED", {"lb": 10, "tp": 10, "sl": 7, "mh": 50}, "fixed"),
        ("LB10 TP10 SL7 H50 V2 (my original)", {"lb": 10, "tp": 10, "sl": 7, "mh": 50}, "v2"),
    ]

    print(f"\n{'Config':<45} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'PF':>6}")
    print("-" * 90)

    for name, params, version in configs:
        if version == "exact":
            r = run_mb_exact(rave, btc_lk, params["lb"], params["tp"], params["sl"], params["mh"])
        elif version == "fixed":
            r = run_mb_no_lookahead(rave, btc_lk, params["lb"], params["tp"], params["sl"], params["mh"])
        else:
            # My V2: HIGH > recent_high, 95% deploy
            from momentum_breakout_v2 import run_mb_v2
            cfg = {"tp": params["tp"], "sl": params["sl"], "max_hold": params["mh"], "lookback": params["lb"]}
            r = run_mb_v2(rave, cfg)

        print(f"{name:<45} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}% {r.get('profit_factor', 0):>5.1f}")

    # Exit reason breakdown for exact vs fixed
    print(f"\n{'=' * 90}")
    print(f"EXIT REASON BREAKDOWN")
    print(f"{'=' * 90}")

    r_exact = run_mb_exact(rave, btc_lk, 5, 10, 10, 50)
    r_fixed = run_mb_no_lookahead(rave, btc_lk, 5, 10, 10, 50)

    for label, r in [("EXACT (lookahead)", r_exact), ("FIXED (no lookahead)", r_fixed)]:
        reasons = {}
        for t in r["closes_data"]:
            reason = t["reason"]
            if reason not in reasons:
                reasons[reason] = {"count": 0, "wins": 0, "total_pnl": 0}
            reasons[reason]["count"] += 1
            if t["win"]: reasons[reason]["wins"] += 1
            reasons[reason]["total_pnl"] += t["pnl"]

        print(f"\n  {label}:")
        for reason, stats in reasons.items():
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            print(f"    {reason}: {stats['count']} trades, {wr:.0f}% WR, total PnL=${stats['total_pnl']:.2f}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "exact": r_exact,
        "fixed": r_fixed,
    }
    # Remove trade details to keep file small
    output["exact"].pop("closes_data", None)
    output["fixed"].pop("closes_data", None)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "mb_reconciliation.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
