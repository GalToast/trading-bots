"""Unified Three-Edge System: RSI MR + Momentum Breakout + Wick-Sniper with single bankroll."""
import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"

def fetch(client, pid, start, end, gran="FIVE_MINUTE"):
    chunk = 300*5*60
    all_c, cs = [], start
    while cs < end:
        ce = min(cs + chunk, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=gran)
            cands = resp.get("candles", [])
            all_c.extend(cands); cs = ce
            if not cands: break
            time.sleep(0.05)
        except:
            cs = ce; time.sleep(0.2)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def rsi(closes, p=3):
    if len(closes) < p+1: return 50.0
    d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g = [x if x>0 else 0 for x in d[-p:]]
    l = [-x if x<0 else 0 for x in d[-p:]]
    ag, al = sum(g)/p, sum(l)/p
    if al > 0: return 100 - 100/(1+ag/al)
    return 100.0

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt_unified(all_candles, btc_lk, strategies, cash_start=48.0):
    """
    Run multiple strategies sharing ONE bankroll. First signal (by priority) gets filled.

    strategies: list of {
        "name": str,
        "coin": str,
        "signal_fn": (h, cd, candles, i) -> dict or None,  # returns {"type": "rsi"|"mb"|"wick", ...}
        "tp_pct": float, "sl_pct": float, "max_hold": int or None
    }
    """
    cash = cash_start
    positions = []  # list of {strategy_name, coin, ep, fill_price, q, h, tp, sl, max_hold}
    total_trades = 0
    total_wins = 0
    total_vol = 0.0
    pk = cash_start
    mdd = 0.0
    strategy_stats = {s["name"]: {"trades": 0, "wins": 0, "pnl": 0.0} for s in strategies}

    # Build coin->candle mapping
    coin_candle_map = {}
    for s in strategies:
        coin = s["coin"]
        if coin not in all_candles:
            continue
        coin_candle_map[coin] = all_candles[coin]

    # Use the longest candle list as the time axis
    max_len = max(len(v) for v in all_candles.values()) if all_candles else 0
    if max_len == 0:
        return {"net": 0, "trades": 0, "wr": 0}

    # Create time-indexed data
    all_timestamps = set()
    for coin, candles in all_candles.items():
        for c in candles:
            all_timestamps.add(int(c["start"]))
    all_timestamps = sorted(all_timestamps)

    # Build candle lookup by timestamp
    candle_lookup = {}
    for coin, candles in all_candles.items():
        candle_lookup[coin] = {int(c["start"]): c for c in candles}

    # Track state per strategy
    rsi_history = {s["coin"]: [] for s in strategies if "rsi" in s["name"].lower() or "RSI" in s["name"]}

    for ts in all_timestamps:
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue

        # BTC gate
        pt, pt3 = ts - 60, ts - 180
        btc_ok = True
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: btc_ok = False

        fr = get_fee(total_vol)

        # Process exits for all positions
        to_remove = []
        for pi, pos in enumerate(positions):
            pos["h"] += 1
            coin = pos["coin"]
            if ts in candle_lookup.get(coin, {}):
                c = candle_lookup[coin][ts]
                hi = float(c["high"]); lo = float(c["low"]); close = float(c["close"])
                exited = False
                exit_p = None

                if "wick" in pos.get("type", ""):
                    # Wick-Sniper exit: target based on entry_open
                    target = pos.get("entry_open", pos["fill_price"]) * (1 + pos["tp_pct"]/100)
                    if hi >= target:
                        exit_p = target; pos["won"] = True; exited = True
                    elif pos["max_hold"] and pos["h"] >= pos["max_hold"]:
                        exit_p = close; exited = True
                        if exit_p > pos["fill_price"]: pos["won"] = True
                else:
                    # Market order: TP and SL
                    tp = pos["ep"] * (1 + pos["tp_pct"]/100)
                    sl = pos["ep"] * (1 - pos["sl_pct"]/100) if pos["sl_pct"] > 0 else 0
                    if hi >= tp:
                        exit_p = tp; pos["won"] = True; exited = True
                    elif sl > 0 and lo <= sl:
                        exit_p = sl; exited = True
                    elif pos["max_hold"] and pos["h"] >= pos["max_hold"]:
                        exit_p = close; exited = True
                        if exit_p > pos["fill_price"]: pos["won"] = True

                if exited:
                    u = pos["q"] / pos["fill_price"]
                    pnl = (exit_p - pos["fill_price"]) * u - (pos["q"] * fr) - (exit_p * u * fr)
                    cash += pos["q"] + pnl
                    total_vol += pos["q"] + exit_p * u
                    total_trades += 1
                    if pos.get("won"): total_wins += 1
                    strategy_stats[pos["strategy_name"]]["trades"] += 1
                    strategy_stats[pos["strategy_name"]]["pnl"] += pnl
                    if pos.get("won"): strategy_stats[pos["strategy_name"]]["wins"] += 1
                    if cash > pk: pk = cash
                    dd = (pk - cash) / pk
                    if dd > mdd: mdd = dd
                    to_remove.append(pi)

        for pi in reversed(to_remove):
            positions.pop(pi)

        # Process entries (first signal by priority gets filled)
        if cash >= 10 and btc_ok:
            for s in strategies:
                coin = s["coin"]
                if ts not in candle_lookup.get(coin, {}): continue
                c = candle_lookup[coin][ts]
                hi = float(c["high"]); lo = float(c["low"]); close = float(c["close"]); op = float(c["open"])

                # Update RSI history
                if coin in rsi_history:
                    rsi_history[coin].append(close)
                    if len(rsi_history[coin]) > 100: rsi_history[coin].pop(0)

                signal = None
                if "rsi" in s["name"].lower() or "RSI" in s["name"]:
                    # RSI Mean Reversion signal
                    hist = rsi_history.get(coin, [])
                    if len(hist) >= 5:
                        rv = rsi(hist[:-1], 3)
                        if rv < 30:
                            signal = {"type": "rsi", "ep": op, "fill": op}

                elif "breakout" in s["name"].lower() or "Breakout" in s["name"]:
                    # Momentum Breakout signal
                    if coin in candle_lookup:
                        coin_candles = candle_lookup[coin]
                        ts_list = sorted(coin_candles.keys())
                        current_idx = ts_list.index(ts) if ts in ts_list else -1
                        if current_idx >= s.get("lb", 20):
                            recent_high = max(float(coin_candles[ts_list[j]]["high"])
                                            for j in range(current_idx - s["lb"], current_idx))
                            if close > recent_high:
                                signal = {"type": "breakout", "ep": op, "fill": op}

                elif "wick" in s["name"].lower() or "Wick" in s["name"]:
                    # Wick-Sniper signal
                    wick_pct = s.get("wick_pct", 3.0)
                    limit_price = op * (1 - wick_pct / 100)
                    if lo <= limit_price:
                        signal = {"type": "wick", "ep": op, "fill": limit_price, "entry_open": op}

                if signal:
                    tq = cash
                    if tq >= 10:
                        positions.append({
                            "strategy_name": s["name"],
                            "coin": coin,
                            "type": signal["type"],
                            "ep": signal["ep"],
                            "fill_price": signal["fill"],
                            "q": tq,
                            "h": 0,
                            "tp_pct": s["tp_pct"],
                            "sl_pct": s.get("sl_pct", 0),
                            "max_hold": s.get("max_hold"),
                            "entry_open": signal.get("entry_open", signal["ep"]),
                            "won": False,
                        })
                        cash -= tq
                    break  # One position per bar (shared bankroll)

    # Close remaining positions
    for pos in positions:
        coin = pos["coin"]
        # Find last candle for this coin
        if coin in candle_lookup:
            last_ts = max(candle_lookup[coin].keys())
            c = candle_lookup[coin][last_ts]
            close = float(c["close"])
            exit_p = close
            u = pos["q"] / pos["fill_price"]
            pnl = (exit_p - pos["fill_price"]) * u - (pos["q"] * fr) - (exit_p * u * fr)
            cash += pos["q"] + pnl
            total_vol += pos["q"] + exit_p * u
            total_trades += 1
            if exit_p > pos["fill_price"]: total_wins += 1
            strategy_stats[pos["strategy_name"]]["trades"] += 1
            strategy_stats[pos["strategy_name"]]["pnl"] += pnl
            if exit_p > pos["fill_price"]: strategy_stats[pos["strategy_name"]]["wins"] += 1

    net = cash - cash_start
    wr = total_wins / max(1, total_trades) * 100
    return {
        "net": round(net, 2), "rpct": round(net / cash_start * 100, 1),
        "trades": total_trades, "wr": round(wr, 1),
        "avg": round(net / max(1, total_trades), 2),
        "mdd": round(mdd * 100, 2), "vol": round(total_vol, 2),
        "final_cash": round(cash, 2),
        "strategy_stats": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in strategy_stats.items()},
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30 * 24 * 3600

    print("🧪 UNIFIED THREE-EDGE SYSTEM — Shared $48 bankroll\n")

    # Fetch data
    print("Fetching candles (30d)...")
    all_candles = {}
    for coin in ["RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD"]:
        print(f"  {coin}...")
        all_candles[coin] = fetch(client, coin, s30, now)
        print(f"    {len(all_candles[coin])} candles")

    print("Fetching BTC M5...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    # Define strategies with optimal params
    strategies_v1 = [
        # RSI Mean Reversion on RAVE
        {"name": "RSI MR RAVE", "coin": "RAVE-USD", "tp_pct": 25, "sl_pct": 0, "max_hold": None},
        # Momentum Breakout on RAVE
        {"name": "MB RAVE LB5", "coin": "RAVE-USD", "tp_pct": 10, "sl_pct": 10, "max_hold": 50, "lb": 5},
        # Momentum Breakout on BAL
        {"name": "MB BAL LB30", "coin": "BAL-USD", "tp_pct": 10, "sl_pct": 7, "max_hold": 20, "lb": 30},
        # Momentum Breakout on IOTX
        {"name": "MB IOTX LB10", "coin": "IOTX-USD", "tp_pct": 10, "sl_pct": 3, "max_hold": 20, "lb": 10},
        # Wick-Sniper on RAVE
        {"name": "Wick RAVE", "coin": "RAVE-USD", "tp_pct": 2.0, "sl_pct": 0, "max_hold": 20, "wick_pct": 3.0},
        # Wick-Sniper on BAL
        {"name": "Wick BAL", "coin": "BAL-USD", "tp_pct": 1.5, "sl_pct": 0, "max_hold": 20, "wick_pct": 3.0},
    ]

    print(f"\n📊 Testing unified system with {len(strategies_v1)} strategies...")
    r1 = bt_unified(all_candles, btc_lk, strategies_v1, cash_start=48.0)
    print(f"\n  Net: ${r1['net']:.2f} ({r1['rpct']}%)")
    print(f"  Trades: {r1['trades']}, WR: {r1['wr']}%, DD: {r1['mdd']}%")
    print(f"  Per-strategy breakdown:")
    for name, stats in r1["strategy_stats"].items():
        print(f"    {name}: {stats['trades']}t, {stats['wins']}w, PnL=${stats['pnl']:.2f}")

    # Test with $288 (6 × $48) to match the combined system
    print(f"\n📊 Testing unified system with $288 (6x capital)...")
    r2 = bt_unified(all_candles, btc_lk, strategies_v1, cash_start=288.0)
    print(f"\n  Net: ${r2['net']:.2f} ({r2['rpct']}%)")
    print(f"  Trades: {r2['trades']}, WR: {r2['wr']}%, DD: {r2['mdd']}%")
    print(f"  Per-strategy breakdown:")
    for name, stats in r2["strategy_stats"].items():
        print(f"    {name}: {stats['trades']}t, {stats['wins']}w, PnL=${stats['pnl']:.2f}")

    # Comparison with separate bankrolls
    print(f"\n{'='*80}")
    print(f"🏆 SHARED vs SEPARATE BANKROLL COMPARISON")
    print(f"{'='*80}")
    print(f"Shared $48:  ${r1['net']:.2f} ({r1['rpct']}%), {r1['trades']}t, {r1['wr']}% WR")
    print(f"Shared $288: ${r2['net']:.2f} ({r2['rpct']}%), {r2['trades']}t, {r2['wr']}% WR")
    print(f"Separate $48×6: ~$1,013 (from individual backtests)")
    print(f"\nDelta: shared $288 vs separate: ${r2['net']-1013:.2f}")

    with open("reports/unified_system.json", "w") as f:
        json.dump({"shared_48": r1, "shared_288": r2}, f, indent=2)
    print(f"\nSaved to reports/unified_system.json")

if __name__ == "__main__":
    main()
