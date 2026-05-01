#!/usr/bin/env python3
"""
Param Optimization for TOP 3 EDGES from the 500-strategy initiative.

Tests multiple TP/SL/max_hold combinations for:
1. time_decay_signal ($2,140)
2. ma_atr ($1,954)  
3. hybrid_deep ($1,708)

Uses the same coin set and data as the original sweeps for apples-to-apples comparison.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest

# ==========================================
# RECREATE THE TOP 3 ENTRY FUNCTIONS
# ==========================================

def _time_decay_entry(candles_hist, closes, candle, params):
    """Signal strength decays with time since trigger."""
    if len(candles_hist) < 30:
        return False
    decay_period = params.get("decay_period", 10)
    signal_age = params.get("_signal_age", 0)
    
    # Simulated: detect if recent bars had a trigger condition
    # In the original: signal fires when there's a pattern, then decays
    recent_returns = []
    for i in range(max(1, len(closes) - decay_period - 1), len(closes) - 1):
        if closes[i] > 0 and closes[i-1] > 0:
            recent_returns.append(abs(closes[i] / closes[i-1] - 1))
    
    if not recent_returns:
        return False
    
    avg_return = sum(recent_returns) / len(recent_returns)
    current_return = abs(closes[-1] / closes[-2] - 1) if len(closes) > 1 and closes[-2] > 0 else 0
    
    # Signal fires when current return > 2x average (fresh signal)
    if avg_return > 0 and current_return > avg_return * 2.0:
        return True
    
    # Decay: also fire if recent signal was strong but fading
    if len(recent_returns) >= 3:
        recent_avg = sum(recent_returns[-3:]) / 3
        if recent_avg > avg_return * 1.5 and current_return > avg_return * 1.2:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    
    return False


def _ma_atr_entry(candles_hist, closes, candle, params):
    """MA crossover + ATR expansion confirmation."""
    if len(candles_hist) < 50:
        return False
    ma_period = params.get("ma_period", 20)
    atr_period = params.get("atr_period", 14)
    atr_mult = params.get("atr_mult", 1.5)
    
    # MA crossover
    if len(closes) < ma_period + 5:
        return False
    ma = sum(closes[-ma_period:]) / ma_period
    ma_prev = sum(closes[-ma_period-1:-1]) / ma_period
    
    current_price = closes[-1]
    
    # Price above MA and MA rising
    ma_rising = ma > ma_prev
    price_above = current_price > ma
    
    # ATR expansion
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    
    if len(trs) < atr_period + 1:
        return False
    
    current_atr = sum(trs[-atr_period:]) / atr_period
    prev_atr = sum(trs[-atr_period*2:-atr_period]) / atr_period if len(trs) >= atr_period * 2 else current_atr
    
    atr_expanding = current_atr > prev_atr * atr_mult if prev_atr > 0 else False
    
    if price_above and ma_rising and atr_expanding:
        return True
    return False


def _hybrid_deep_entry(candles_hist, closes, candle, params):
    """Deep ensemble: multiple layers of signal filtering."""
    if len(candles_hist) < 40:
        return False
    
    # Layer 1: Trend (price above 20-period MA)
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0
    trend_ok = closes[-1] > ma20
    
    # Layer 2: Momentum (5-bar return > 0)
    mom_ok = len(closes) >= 6 and closes[-1] > closes[-6]
    
    # Layer 3: Volume confirmation
    vols = [float(c["volume"]) for c in candles_hist[-20:]]
    avg_vol = sum(vols[:10]) / 10 if len(vols) >= 10 else 0
    recent_vol = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    vol_ok = avg_vol > 0 and recent_vol > avg_vol * 0.8
    
    # Layer 4: Volatility regime
    if len(closes) >= 11:
        rets = [abs(closes[i] / closes[i-1] - 1) for i in range(len(closes) - 10, len(closes)) if closes[i-1] > 0]
        avg_volatility = sum(rets) / len(rets) if rets else 0
        vol_regime_ok = avg_volatility < 0.05  # Not too volatile
    else:
        vol_regime_ok = True
    
    # All layers must pass (deep filter)
    if trend_ok and mom_ok and vol_ok and vol_regime_ok:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


ENTRY_FUNCS = {
    "time_decay_signal": _time_decay_entry,
    "ma_atr": _ma_atr_entry,
    "hybrid_deep": _hybrid_deep_entry,
}


def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
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


def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"PARAM OPTIMIZATION — TOP 3 EDGES")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()

    # Load coins
    coin_file = Path(__file__).parent.parent / "coinbase_usd_pairs.txt"
    coins = [line.strip() for line in open(coin_file) if line.strip() and not line.startswith("Total")]
    fast_coins = coins[:30] + [c for c in ["GHST-USD", "NOM-USD", "TRU-USD", "MOG-USD", "RAVE-USD"] if c not in coins[:30]]
    print(f"Testing on {len(fast_coins)} coins\n")

    # Fetch candles (7d)
    now = int(time.time())
    start_ts = now - 7 * 86400
    all_candles = {}
    for coin in fast_coins:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            if candles:
                all_candles[coin] = candles
        except Exception:
            pass
        time.sleep(0.2)

    print(f"Fetched {len(all_candles)} coins\n")

    # Param grids
    param_grids = {
        "time_decay_signal": {
            "decay_period": [5, 10, 15, 20],
            "tp_pct": [5, 8, 10, 12, 15],
            "sl_pct": [0, 2, 3, 5],
            "max_hold": [12, 24, 36, 48],
        },
        "ma_atr": {
            "ma_period": [10, 15, 20, 30],
            "atr_period": [10, 14, 20],
            "atr_mult": [1.0, 1.5, 2.0],
            "tp_pct": [5, 8, 10, 12],
            "sl_pct": [0, 2, 3],
            "max_hold": [12, 24, 36],
        },
        "hybrid_deep": {
            "tp_pct": [5, 8, 10, 12, 15],
            "sl_pct": [0, 2, 3, 5],
            "max_hold": [12, 24, 36, 48],
        },
    }

    all_results = {}

    for strat_name, entry_fn in ENTRY_FUNCS.items():
        print(f"\n{'='*60}")
        print(f"  Optimizing: {strat_name}")
        print(f"{'='*60}\n")

        grid = param_grids[strat_name]
        keys = list(grid.keys())
        combos = list(product(*[grid[k] for k in keys]))
        print(f"  Testing {len(combos)} param combos x {len(all_candles)} coins = {len(combos) * len(all_candles)} backtests\n")

        strat_results = []
        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            coin_results = []

            for coin, candles in all_candles.items():
                try:
                    result = backtest(candles, entry_fn, params, fee_rate=0.004, starting_cash=48.0)
                    coin_results.append({"coin": coin, **result})
                except Exception:
                    pass

            profitable = [r for r in coin_results if r.get("net_pnl", 0) > 0]
            total_pnl = sum(r.get("net_pnl", 0) for r in coin_results)
            avg_pnl = total_pnl / len(coin_results) if coin_results else 0
            hit_rate = len(profitable) / len(coin_results) * 100 if coin_results else 0

            strat_results.append({
                "params": params,
                "total_net_pnl": round(total_pnl, 2),
                "avg_net_pnl": round(avg_pnl, 2),
                "hit_rate": round(hit_rate, 1),
                "profitable_coins": len(profitable),
                "total_coins": len(coin_results),
            })

            if (idx + 1) % 50 == 0:
                elapsed = time.time() - start_time
                print(f"    Progress: {idx + 1}/{len(combos)} ({elapsed:.0f}s)")

        strat_results.sort(key=lambda x: x["total_net_pnl"], reverse=True)
        all_results[strat_name] = strat_results

        print(f"\n  TOP 5 PARAM COMBOS for {strat_name}:")
        for i, r in enumerate(strat_results[:5], 1):
            params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
            print(f"    {i}. PnL=${r['total_net_pnl']:>7.0f}  Hit={r['hit_rate']:>5.1f}%  Coins={r['profitable_coins']}/{r['total_coins']}  |  {params_str}")

    # Save results
    out_path = Path(__file__).parent.parent / "reports" / "param_optimization_top3.json"
    out_path.parent.mkdir(exist_ok=True)
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "results": {k: v[:20] for k, v in all_results.items()},  # Top 20 each
        "best_params": {
            strat: results[0] if results else None
            for strat, results in all_results.items()
        }
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"OPTIMIZATION COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results: {out_path}")
    print(f"{'='*70}\n")

    # Print best params
    for strat, best in output["best_params"].items():
        if best:
            params_str = ", ".join(f"{k}={v}" for k, v in best["params"].items())
            print(f"  BEST {strat}: {params_str} → ${best['total_net_pnl']:.0f} ({best['hit_rate']:.1f}% hit)")


if __name__ == "__main__":
    main()
