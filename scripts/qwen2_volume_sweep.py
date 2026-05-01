#!/usr/bin/env python3
"""
Volume Strategy Category Sweep for Qwen-2's 500 Strategy Goal.
Sweeps Volume-based strategies across 235 coins on 7-day data.
"""

import json
import os
import sys
import time
import math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest

# ==========================================
# VOLUME STRATEGY ALGORITHMS
# ==========================================

def compute_obv(candles):
    obv = [0]
    for i in range(1, len(candles)):
        close = float(candles[i]["close"])
        prev_close = float(candles[i-1]["close"])
        vol = float(candles[i]["volume"])
        
        if close > prev_close:
            obv.append(obv[-1] + vol)
        elif close < prev_close:
            obv.append(obv[-1] - vol)
        else:
            obv.append(obv[-1])
    return obv

def _volume_spike_followthru_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    # Entry on massive volume spike + green candle
    if len(candles_hist) < 30: return False
    vol_lookback = params.get("vol_lookback", 20)
    vol_mult = params.get("vol_mult", 3.0)
    
    recent_vols = [float(c["volume"]) for c in candles_hist[-vol_lookback-1:-1]]
    avg_vol = sum(recent_vols) / vol_lookback
    
    current_vol = float(candle["volume"])
    current_close = float(candle["close"])
    current_open = float(candle["open"])
    
    if current_vol > avg_vol * vol_mult and current_close > current_open:
        return True
    return False

def _obv_breakout_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    # OBV breaking its own N-period high
    if len(candles_hist) < 50: return False
    lookback = params.get("obv_lookback", 20)
    
    obv_values = compute_obv(candles_hist)
    if len(obv_values) < lookback + 1: return False
    
    current_obv = obv_values[-1]
    prev_obv_max = max(obv_values[-lookback-1:-1])
    
    if current_obv > prev_obv_max:
        # Check if price is also trending or just volume
        if closes[-1] > closes[-5]: # 5-bar trend confirm
            return True
    return False

def _volume_price_divergence_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    # High volume but price decreasing (exhaustion?) or vice versa
    # Let's try "Volume Climax Reversion" (entry when volume is high but price fails to make new low)
    if len(candles_hist) < 30: return False
    vol_lookback = params.get("vol_lookback", 20)
    vol_mult = params.get("vol_mult", 4.0)
    
    recent_vols = [float(c["volume"]) for c in candles_hist[-vol_lookback-1:-1]]
    avg_vol = sum(recent_vols) / vol_lookback
    
    current_vol = float(candle["volume"])
    current_low = float(candle["low"])
    prev_lows = [float(c["low"]) for c in candles_hist[-10:-1]]
    min_prev_low = min(prev_lows)
    
    # Volume is 4x average, but we are closing above the previous local low (absorption?)
    if current_vol > avg_vol * vol_mult and float(candle["close"]) > min_prev_low:
        return True
    return False

# ==========================================

STRATEGIES = [
    {
        "name": "vol_spike_follow",
        "entry_fn": _volume_spike_followthru_entry,
        "variants": [
            {"vol_mult": 3.0, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 24},
            {"vol_mult": 5.0, "tp_pct": 15.0, "sl_pct": 5.0, "max_hold": 48},
        ]
    },
    {
        "name": "obv_breakout",
        "entry_fn": _obv_breakout_entry,
        "variants": [
            {"obv_lookback": 20, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 24},
            {"obv_lookback": 50, "tp_pct": 15.0, "sl_pct": 5.0, "max_hold": 48},
        ]
    },
    {
        "name": "vol_divergence",
        "entry_fn": _volume_price_divergence_entry,
        "variants": [
            {"vol_mult": 4.0, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 12},
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
    start = now - 7 * 86400 # 7d scan
    
    print("Fetching Coinbase products...", flush=True)
    products = client.list_products(get_all_products=True, limit=1000).get("products", [])
    usd_coins = filter_usd_coins(products)
    
    # Prioritize our top coins
    top_coins = ["NOM-USD", "MOG-USD", "GHST-USD", "RAVE-USD", "TRU-USD", "A8-USD", "SUP-USD"]
    scan_coins = top_coins + [c for c in usd_coins if c not in top_coins][:20]
    
    print(f"Scanning {len(scan_coins)} coins x {sum(len(s['variants']) for s in STRATEGIES)} volume strategies on 7d data...")
    
    results = []
    
    for idx, coin in enumerate(scan_coins):
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
                
                if res["net_pnl"] > 0:
                    results.append({
                        "coin": coin,
                        "strategy": strat["name"],
                        "params": params,
                        "net_pnl": res["net_pnl"],
                        "wr": res["win_rate"],
                        "trades": res["trades"],
                        "signals": res["signals"]
                    })
        
        if idx % 5 == 0:
            print(f"Processed {idx}/{len(scan_coins)} coins...", flush=True)
    
    results.sort(key=lambda x: x["net_pnl"], reverse=True)
    
    with open("reports/qwen2_volume_sweep_7d.json", "w") as f:
        json.dump(results, f, indent=2)
        
    print(f"Found {len(results)} profitable volume edges!")
    if results:
        print("Top 5 Volume Edges:")
        for r in results[:5]:
            print(f"  {r['coin']} | {r['strategy']} | {r['params']} | +${r['net_pnl']:.2f}")

if __name__ == "__main__":
    run()
