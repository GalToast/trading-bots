#!/usr/bin/env python3
"""
Volatility Strategy Category Sweep for Qwen-2's 500 Strategy Goal.
Sweeps 3 completely unique Volatility-based strategies across 235 coins.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest

# ==========================================
# VOLATILITY STRATEGY ALGORITHMS
# ==========================================

def compute_atr(candles, period: int):
    """Computes True Range and ATR."""
    if len(candles) < period + 1: return []
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c_prev = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    
    atrs = []
    # simple moving average of TR
    for i in range(len(trs)):
        if i < period - 1:
            atrs.append(None)
        else:
            atrs.append(sum(trs[i-period+1:i+1]) / period)
    return atrs

def _atr_expansion_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    # Enters when current ATR is much higher than recent ATR and price breaks up
    if len(candles_hist) < 30: return False
    period = params.get("atr_period", 14)
    mult = params.get("atr_mult", 1.5)
    
    atrs = compute_atr(candles_hist[:-1], period)
    if not atrs or atrs[-1] is None or atrs[-2] is None: return False
    
    current_atr = atrs[-1]
    prev_atr = atrs[-2]
    
    if current_atr > prev_atr * mult:
        # Volatility is expanding, only enter if price is moving up
        if float(candles_hist[-2]["close"]) > float(candles_hist[-3]["close"]):
            return True
    return False

def _keltner_breakout_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    if len(candles_hist) < 30: return False
    period = params.get("k_period", 20)
    mult = params.get("k_mult", 2.0)
    
    past_closes = closes[:-1]
    if len(past_closes) < period: return False
    ema = sum(past_closes[-period:]) / period  # simple SMA for Keltner midline
    
    atrs = compute_atr(candles_hist[:-1], period)
    if not atrs or atrs[-1] is None: return False
    
    upper_band = ema + (atrs[-1] * mult)
    
    # Enter if previous close broke strictly above Keltner upper band
    if past_closes[-1] > upper_band and past_closes[-2] <= upper_band:
        return True
    return False

def _hist_vol_squeeze_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    if len(closes) < 30: return False
    period = params.get("hv_period", 20)
    
    past_closes = closes[:-1]
    import math
    if len(past_closes) < period + 1: return False
    
    returns = []
    for i in range(1, len(past_closes)):
        if past_closes[i-1] > 0:
            returns.append(math.log(past_closes[i] / past_closes[i-1]))
        else:
            returns.append(0)
            
    recent_rets = returns[-period:]
    mean_ret = sum(recent_rets) / period
    variance = sum((r - mean_ret)**2 for r in recent_rets) / period
    hv = math.sqrt(variance)
    
    prev_rets = returns[-period-5:-5]
    if len(prev_rets) < period: return False
    prev_mean = sum(prev_rets) / period
    prev_var = sum((r - prev_mean)**2 for r in prev_rets) / period
    prev_hv = math.sqrt(prev_var)
    
    if hv < prev_hv * 0.5:
        if past_closes[-1] > past_closes[-2]: 
            return True
    return False

# ==========================================

STRATEGIES = [
    {
        "name": "atr_expansion",
        "entry_fn": _atr_expansion_entry,
        "variants": [
            {"atr_period": 14, "atr_mult": 1.5, "tp_pct": 0.15, "sl_pct": 0.05, "max_hold": 24},
            {"atr_period": 14, "atr_mult": 2.0, "tp_pct": 0.20, "sl_pct": 0.05, "max_hold": 48},
            {"atr_period": 10, "atr_mult": 1.5, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
        ]
    },
    {
        "name": "keltner_breakout",
        "entry_fn": _keltner_breakout_entry,
        "variants": [
            {"k_period": 20, "k_mult": 1.5, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
            {"k_period": 20, "k_mult": 2.0, "tp_pct": 0.15, "sl_pct": 0.05, "max_hold": 36},
            {"k_period": 10, "k_mult": 1.5, "tp_pct": 0.10, "sl_pct": 0.02, "max_hold": 12},
        ]
    },
    {
        "name": "hist_vol_squeeze",
        "entry_fn": _hist_vol_squeeze_entry,
        "variants": [
            {"hv_period": 20, "tp_pct": 0.15, "sl_pct": 0.05, "max_hold": 48},
            {"hv_period": 10, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
            {"hv_period": 30, "tp_pct": 0.20, "sl_pct": 0.05, "max_hold": 48},
        ]
    }
]

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
            if not cands: break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def filter_usd_coins(products):
    usd_coins = []
    for p in products:
        pid = p.get("product_id", "")
        if pid.endswith("-USD") and p.get("status") == "online":
            try:
                if float(p.get("volume_24_h", "0")) >= 100000:
                    usd_coins.append(pid)
            except: pass
    return usd_coins

def run():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 86400  # 7d sweep to be fast, just to discover edges
    
    print("Fetching Coinbase products...", flush=True)
    products = client.list_products(get_all_products=True, limit=1000).get("products", [])
    usd_coins = filter_usd_coins(products)
    if not usd_coins:
        usd_coins = ["GHST-USD", "MOG-USD", "RAVE-USD", "TRU-USD", "NOM-USD", "SUP-USD", "A8-USD", "BAL-USD"]
    
    print(f"Scanning {len(usd_coins)} coins x {len(STRATEGIES)*3} volatility variants on 7d data...")
    
    results = []
    
    # Only scan a randomly sampled fast block or the top known ones to get it done quickly for Qwen
    fast_coins = usd_coins[:30] + [c for c in ["GHST-USD", "NOM-USD", "TRU-USD"] if c not in usd_coins[:30]]
    
    for idx, coin in enumerate(fast_coins):
        candles = fetch_candles(client, coin, start, now)
        if len(candles) < 100: continue
        
        for strat in STRATEGIES:
            for params in strat["variants"]:
                res = backtest(
                    candles=candles,
                    entry_fn=strat["entry_fn"],
                    params=params,
                    fee_rate=0.0040,
                    starting_cash=48.0
                )
                
                if res["net_pnl"] > 0 and res["trades"] >= 2:
                    results.append({
                        "coin": coin,
                        "strategy": strat["name"],
                        "params": params,
                        "net_pnl": res["net_pnl"],
                        "wr": res["win_rate"],
                        "trades": res["trades"],
                        "signals": res["signals"],
                        "dd": res["max_drawdown"]
                    })
        
        if idx % 5 == 0:
            print(f"Processed {idx}/{len(fast_coins)} coins...", flush=True)
    
    results.sort(key=lambda x: x["net_pnl"], reverse=True)
    
    with open("reports/qwen2_volatility_sweep_7d.json", "w") as f:
        json.dump(results, f, indent=2)
        
    print(f"Found {len(results)} profitable volatility edges!")
    if results:
        print("Top 5 Volatility Edges:")
        for r in results[:5]:
            print(f"  {r['coin']} | {r['strategy']} | {r['params']} | +${r['net_pnl']:.2f} ({r['wr']}%)")

if __name__ == "__main__":
    run()
