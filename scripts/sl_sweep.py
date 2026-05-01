#!/usr/bin/env python3
"""
Stop Loss Sweep — SL=0% vs SL=3% vs SL=5% for RAVE, A8, CFG

Validates @qwen-2's proposal: adding 3% asymmetric stop losses to strategies
that currently rely on timeout-only downside protection.

Fetches 30d of 5-min candles from Coinbase and runs the full backtest sweep.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest, _momentum_entry


def _supertrend_entry(candles_hist, closes, candle, params):
    """Supertrend: enter when price crosses above the supertrend line."""
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)
    if len(candles_hist) < atr_period + 10:
        return False
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < atr_period:
        return False
    atr = sum(trs[-atr_period:]) / atr_period
    hl2 = (float(candle["high"]) + float(candle["low"])) / 2
    supertrend = hl2 - atr_mult * atr
    return float(candle["close"]) > supertrend and len(closes) > 1 and closes[-1] > closes[-2]

COINS = {
    # Supertrend coins — expect SL=5% to benefit all (trend-following)
    "RAVE-USD": {"strategy": "supertrend", "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "max_hold": 48}},
    "TRU-USD":  {"strategy": "supertrend", "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "max_hold": 48}},
    "BAL-USD":  {"strategy": "supertrend", "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "max_hold": 96}},
    "IOTX-USD": {"strategy": "supertrend", "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "max_hold": 48}},
    # Momentum coins — expect SL=0% to stay best (breakouts need room)
    "A8-USD":   {"strategy": "momentum",   "params": {"lookback": 10, "tp_pct": 15.0, "max_hold": 48}},
    "CFG-USD":  {"strategy": "momentum",   "params": {"lookback": 50, "tp_pct": 15.0, "max_hold": 48}},
    # NOTE: Fibonacci coins (NOM, GHST, SUP) skipped — their signal logic
    # lives in the isolated runner, not strategy_library, so can't be backtested here.
}

SL_LEVELS = [0.0, 3.0, 5.0]  # percentages (backtest divides by 100 internally)
FEE_RATE = 0.004
STARTING_CASH = 100.0
DAYS = 30

def fetch_30d_candles(client, coin, granularity="FIVE_MINUTE"):
    """Fetch ~30 days of candles with chunked API calls."""
    end = int(time.time())
    start = end - (DAYS * 86400)
    chunk_sec = 300 * 5 * 60  # ~25 hours per chunk
    all_candles = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(coin, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_candles.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.3)  # rate limit padding
        except Exception as e:
            print(f"  ERR fetching {coin} at {cs}: {e}", flush=True)
            cs += chunk_sec
    # Sort by time
    all_candles.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_candles


def run_sweep():
    client = CoinbaseAdvancedClient()
    
    print(f"Fetching {DAYS}d of 5-min candles for {len(COINS)} coins...\n", flush=True)
    
    all_candles = {}
    for coin in COINS:
        print(f"  {coin}...", end=" ", flush=True)
        candles = fetch_30d_candles(client, coin)
        all_candles[coin] = candles
        print(f"{len(candles)} candles", flush=True)
    
    print(f"\n{'='*80}")
    print(f"  STOP LOSS SWEEP: SL=0% vs SL=3% vs SL=5%")
    print(f"{'='*80}\n", flush=True)
    
    for coin, cfg in COINS.items():
        candles = all_candles[coin]
        if len(candles) < 60:
            print(f"  ⚠️ {coin}: only {len(candles)} candles, skipping\n", flush=True)
            continue
        
        strategy = cfg["strategy"]
        base_params = dict(cfg["params"])
        
        print(f"--- {coin} ({strategy}, {len(candles)} candles) ---", flush=True)
        print(f"  {'SL%':<8} {'Net PnL':>10} {'WR':>8} {'Trades':>8} {'Avg Win':>10} {'Avg Loss':>10} {'Max DD':>8} {'Signals':>8}", flush=True)
        print(f"  {'-'*80}", flush=True)
        
        best_net = -999
        best_sl = None
        
        for sl_pct in SL_LEVELS:
            params = dict(base_params)
            params["sl_pct"] = sl_pct
            
            if strategy == "supertrend":
                result = backtest(candles, _supertrend_entry, params, FEE_RATE, STARTING_CASH)
            elif strategy == "momentum":
                result = backtest(candles, _momentum_entry, params, FEE_RATE, STARTING_CASH)
            
            net = result["net_pnl"]
            wr = result["win_rate"]
            trades = result["trades"]
            signals = result["signals"]
            max_dd = result["max_drawdown"]
            
            # Compute avg win/loss from trades
            avg_win = 0
            avg_loss = 0
            if result.get("wins", 0) > 0:
                avg_win = result.get("gross_profit", 0) / result["wins"]
            if result.get("losses", 0) > 0:
                avg_loss = result.get("gross_loss", 0) / result["losses"]
            
            # Exit reason breakdown
            exit_tp = result.get("exit_tp", 0)
            exit_sl = result.get("exit_sl", 0)
            exit_timeout = result.get("exit_timeout", 0)
            
            sl_label = f"{sl_pct:.0f}%"
            print(f"  {sl_label:<8} ${net:>9.2f} {wr:>7.1f}% {trades:>8} ${avg_win:>9.2f} ${avg_loss:>9.2f} {max_dd:>7.1f}% {signals:>8} (TP={exit_tp} SL={exit_sl} TO={exit_timeout})", flush=True)
            
            if net > best_net:
                best_net = net
                best_sl = sl_pct
        
        print(f"  → BEST SL: {best_sl*100:.0f}% (${best_net:+.2f})\n", flush=True)
    
    print(f"{'='*80}")
    print(f"  SWEEP COMPLETE")
    print(f"{'='*80}", flush=True)


if __name__ == "__main__":
    run_sweep()
