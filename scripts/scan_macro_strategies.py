#!/usr/bin/env python3
"""
Macro Strategy Edge Scanner for Coinbase Spot.

Objective: Discover fundamentally different strategies that work on
established, deep-liquidity coins (e.g. SOL, ETH, SUI) over higher
timeframes (1-Hour candles) and longer holding periods (days).

Strategies tested:
1. Trend Following (EMA Cross + ATR Stop)
2. Structural Breakout (Confirmed Displacement over 48h range)
3. Relative Strength Rotation (Always holding the highest momentum asset)
"""
import json, os, sys, time, statistics
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "reports" / "_macro_strategy_scan_results.json"

COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "SUI-USD", "AVAX-USD", "LINK-USD", "NEAR-USD", "DOGE-USD"]
FEE_RATE = 0.004  # 40 bps
STARTING_CASH = 100.0


def fetch_candles(client, product_id, days=30, granularity="ONE_HOUR"):
    gsec = 3600
    end = int(time.time())
    start = end - days * 24 * 3600
    all_c, seen = [], set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - 300 * gsec)
        try:
            resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        except Exception:
            break
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_c.append({"time": t, "open": float(c["open"]), "high": float(c["high"]),
                              "low": float(c["low"]), "close": float(c["close"]),
                              "volume": float(c.get("volume", 0))})
        chunk_end = chunk_start - 1
        time.sleep(0.2)
    return sorted(all_c, key=lambda x: x["time"])


def calculate_ema(closes, period):
    if not closes: return []
    emas = [closes[0]]
    k = 2 / (period + 1)
    for c in closes[1:]:
        emas.append(c * k + emas[-1] * (1 - k))
    return emas


def calculate_atr(candles, period=14):
    if not candles: return []
    tr = [candles[0]["high"] - candles[0]["low"]]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    
    atr = [tr[0]] * period
    for i in range(period, len(tr)):
        atr.append(sum(tr[i-period+1:i+1]) / period)
    return atr


# ──────────── STRATEGY 1: EMA Trend Following + ATR Stop ────────────
def strat_ema_trend(candles):
    closes = [c["close"] for c in candles]
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    atr = calculate_atr(candles, 14)
    
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0
    
    for i in range(21, len(candles)):
        c = candles[i]
        
        if pos:
            pos["hold"] += 1
            # Update trailing stop if price moved up
            if c["close"] - 3 * atr[i] > pos["sl"]:
                pos["sl"] = c["close"] - 3 * atr[i]
            
            # Exit on EMA cross down OR hit trailing sl
            cross_down = ema9[i-1] >= ema21[i-1] and ema9[i] < ema21[i]
            hit_sl = c["low"] <= pos["sl"]
            
            if cross_down or hit_sl:
                exit_p = pos["sl"] if hit_sl else c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                if net > 0: wins += 1
                else: losses += 1
                pos = None

        if pos is None and cash >= 10.0:
            cross_up = ema9[i-1] <= ema21[i-1] and ema9[i] > ema21[i]
            if cross_up:
                deploy = cash
                efee = deploy * FEE_RATE
                units = (deploy - efee) / c["close"]
                cash -= deploy
                pos = {
                    "ep": c["close"], "q": deploy, "hold": 0,
                    "sl": c["close"] - 3 * atr[i], "units": units, "efee": efee
                }

    # Close out open pos at end for benchmark fairness
    if pos:
        c = candles[-1]
        gross = (c["close"] - pos["ep"]) * pos["units"]
        fee = c["close"] * pos["units"] * FEE_RATE
        net = gross - pos["efee"] - fee
        realized += net
        if net > 0: wins += 1
        else: losses += 1

    total = wins + losses
    return {"strategy": "ema_trend", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 2: Structural Breakout (Confirmed Displacement) ────────────
def strat_structural_breakout(candles, lookback=48):
    atr = calculate_atr(candles, 14)
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0
    
    for i in range(lookback, len(candles)):
        c = candles[i]
        window = candles[i-lookback:i]
        range_high = max(w["high"] for w in window)
        avg_vol = sum(w["volume"] for w in window) / lookback
        
        if pos:
            pos["hold"] += 1
            hit_tp = c["high"] >= pos["tp"]
            hit_sl = c["low"] <= pos["sl"]
            
            if hit_tp or hit_sl:
                exit_p = pos["tp"] if hit_tp else pos["sl"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                if net > 0: wins += 1
                else: losses += 1
                pos = None

        if pos is None and cash >= 10.0:
            breakout = c["close"] > range_high
            vol_confirm = c["volume"] > avg_vol * 2.0
            
            if breakout and vol_confirm:
                deploy = cash
                efee = deploy * FEE_RATE
                units = (deploy - efee) / c["close"]
                cash -= deploy
                pos = {
                    "ep": c["close"], "q": deploy, "hold": 0,
                    "tp": c["close"] + 3 * atr[i],
                    "sl": c["close"] - 2 * atr[i], 
                    "units": units, "efee": efee
                }

    if pos:
        c = candles[-1]
        gross = (c["close"] - pos["ep"]) * pos["units"]
        fee = c["close"] * pos["units"] * FEE_RATE
        net = gross - pos["efee"] - fee
        realized += net
        if net > 0: wins += 1
        else: losses += 1

    total = wins + losses
    return {"strategy": "structural_breakout", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 3: Relative Strength Rotation ────────────
def strat_relative_rotation(all_coin_candles, lookback=24):
    """
    Simulates a portfolio moving 100% of cash into the strongest asset over the last 24h.
    Evaluated every 4 hours.
    all_coin_candles param: { "SOL-USD": [candles], "ETH-USD": [candles] ... }
    """
    # Align by UTC time
    times = sorted(list(set(t for c in list(all_coin_candles.values())[0] for t in [c["time"]])))
    cash = STARTING_CASH
    pos_coin = None
    pos_units = 0.0
    realized = 0.0
    trades = 0
    
    # Precompute time index for O(1) lookups
    index_maps = {}
    for coin, candles in all_coin_candles.items():
        index_maps[coin] = {c["time"]: c for c in candles}

    for i in range(lookback, len(times), 4):  # Rebalance every 4 hours
        current_time = times[i]
        past_time = times[i - lookback]
        
        # Rank momentum
        scores = []
        for coin, c_map in index_maps.items():
            if current_time in c_map and past_time in c_map:
                curr_c = c_map[current_time]
                past_c = c_map[past_time]
                roc = (curr_c["close"] - past_c["close"]) / past_c["close"]
                scores.append((roc, coin, curr_c["close"]))
                
        if not scores: continue
        scores.sort(reverse=True)
        best_roc, best_coin, best_price = scores[0]

        if pos_coin != best_coin:
            # Exit existing
            if pos_coin:
                exit_price = index_maps[pos_coin][current_time]["close"]
                fee = exit_price * pos_units * FEE_RATE
                proceeds = exit_price * pos_units - fee
                net = proceeds - cash_in
                cash = proceeds
                realized += net
                trades += 1
            
            # Enter new (if momentum is positive, else hold cash)
            if best_roc > 0:
                pos_coin = best_coin
                cash_in = cash
                efee = cash * FEE_RATE
                pos_units = (cash - efee) / best_price
                cash = 0
            else:
                pos_coin = None
                pos_units = 0

    return {
        "strategy": "relative_rotation",
        "coin": "PORTFOLIO",
        "closes": trades,
        "win_rate": 0.0, # Not tracked per trade
        "realized_pnl": round(realized + (cash - STARTING_CASH if pos_coin is None else index_maps[pos_coin][times[-1]]["close"] * pos_units - cash_in - STARTING_CASH), 4)
    }

def main():
    client = CoinbaseAdvancedClient()
    all_results = []
    candles_cache = {}
    
    print(f"Macro Strategy Scanner: Fetching 30 days of 1-Hour candles for {len(COINS)} coins...")
    for coin in COINS:
        print(f"  {coin} ...", end="", flush=True)
        c = fetch_candles(client, coin)
        if len(c) > 500:
            candles_cache[coin] = c
            print(f" {len(c)} candles")
        else:
            print(" SKIP")
            
    print("\nRunning Independent Strategies...")
    for coin, candles in candles_cache.items():
        res1 = strat_ema_trend(candles)
        res1["coin"] = coin
        all_results.append(res1)
        
        res2 = strat_structural_breakout(candles)
        res2["coin"] = coin
        all_results.append(res2)
        
    print("Running Portfolio Rotation Strategy...")
    res3 = strat_relative_rotation(candles_cache)
    all_results.append(res3)

    print("\n=== MACRO SCAN RESULTS ===")
    print(f"{'Strategy':>25}  {'Coin':>10}  {'Trades':>6}  {'WR%':>4}  {'PnL':>10}")
    print("-" * 65)
    
    all_results.sort(key=lambda x: x["realized_pnl"], reverse=True)
    for r in all_results:
        flag = "🟢" if r["realized_pnl"] > 0 else "🔴"
        print(f"{flag} {r['strategy']:>23}  {r['coin']:>10}  {r['closes']:>6}  {r.get('win_rate', 0):>3.0f}%  ${r['realized_pnl']:>8.2f}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nSaved to: {RESULTS_PATH}")

if __name__ == "__main__":
    main()
