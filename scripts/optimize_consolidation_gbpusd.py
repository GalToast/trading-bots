#!/usr/bin/env python3
"""Consolidation Range Optimizer — focused on GBPUSD (the only survivor).

After the initial scan found GBPUSD +$0.094/trade at 64.5% WR, this
script does a deep parameter sweep to find the optimal consolidation
range config for GBPUSD specifically.

Usage: python scripts/optimize_consolidation_gbpusd.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "GBPUSD"
PIP = 0.0001
SPREAD = 1.0
UNITS_001_LOT = 1_000
MAX_HOLD = 20


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def compute_atr(bars: list[dict], idx: int, period: int = 14) -> float:
    if idx < period:
        return 0.0
    trs = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float) -> float:
    raw = (exit_price - entry) / PIP if direction == "BUY" else (entry - exit_price) / PIP
    price_move = (raw - spread_pips) * PIP
    raw_usd = price_move * UNITS_001_LOT
    return raw_usd


def simulate(
    bars: list[dict], spread_pips: float,
    lookback: int, min_move: float, max_range: float, min_bars: int,
    entry_pct: float, target_pct: float, stop_pips: float,
    max_hold: int = MAX_HOLD,
) -> list[dict]:
    trades = []
    idx = lookback + 100
    
    while idx < len(bars) - max_hold - 2:
        # Detect consolidation after large move
        if idx < lookback + 100:
            idx += 1
            continue
        
        # Find recent large move
        recent = bars[max(0, idx-100):idx]
        if len(recent) < 20:
            idx += 1
            continue
        
        prices = [b["close"] for b in recent]
        total_move = (max(prices) - min(prices)) / PIP
        
        if total_move < min_move:
            idx += 1
            continue
        
        # Check consolidation
        cons = bars[idx - lookback: idx]
        if len(cons) < min_bars:
            idx += 1
            continue
        
        cons_high = max(b["high"] for b in cons)
        cons_low = min(b["low"] for b in cons)
        cons_range = (cons_high - cons_low) / PIP
        
        if cons_range > max_range:
            idx += 1
            continue
        
        # Check ATR contraction
        cur_atr = compute_atr(bars, idx, 14)
        prev_atr = compute_atr(bars, idx - lookback, 14)
        
        if cur_atr <= 0 or prev_atr <= 0 or cur_atr / prev_atr >= 0.8:
            idx += 1
            continue
        
        range_width = cons_range
        cur_price = bars[idx]["close"]
        pos = (cur_price - cons_low) / (cons_high - cons_low) if cons_range > 0 else 0.5
        
        direction = None
        entry_price = None
        
        if pos >= (1.0 - entry_pct):
            direction = "SELL"
            entry_price = cur_price
        elif pos <= entry_pct:
            direction = "BUY"
            entry_price = cur_price
        
        if direction:
            entry_idx = idx + 1
            if entry_idx >= len(bars) - 1:
                break
            
            target = range_width * target_pct
            if target < 2.0:
                target = 2.0
            
            mfe = mae = 0.0
            exit_idx = exit_price = None
            
            for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
                bar = bars[j]
                if direction == "BUY":
                    fav = (bar["high"] - entry_price) / PIP
                    adv = (entry_price - bar["low"]) / PIP
                else:
                    fav = (entry_price - bar["low"]) / PIP
                    adv = (bar["high"] - entry_price) / PIP
                
                mfe = max(mfe, fav)
                mae = max(mae, adv)
                
                if mfe >= target:
                    exit_idx = j
                    exit_price = entry_price + target * PIP if direction == "BUY" else entry_price - target * PIP
                    break
                if adv >= stop_pips:
                    exit_idx = j
                    exit_price = entry_price - stop_pips * PIP if direction == "BUY" else entry_price + stop_pips * PIP
                    break
            
            if exit_idx is None:
                exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
                exit_price = bars[exit_idx]["close"]
            
            pnl_pips = ((exit_price - entry_price) / PIP if direction == "BUY"
                        else (entry_price - exit_price) / PIP) - spread_pips
            pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips)
            
            trades.append({
                "direction": direction, "entry_idx": entry_idx, "exit_idx": exit_idx,
                "hold_bars": exit_idx - entry_idx + 1, "pnl_pips": pnl_pips,
                "pnl_usd": pnl_usd, "mfe_pips": mfe, "mae_pips": mae,
                "range_width": round(range_width, 1), "position": round(pos, 2),
            })
            idx += 5
        else:
            idx += 1
    
    return trades


def main():
    mt5.initialize()
    bars = load_bars(SYMBOL, 30)
    print(f"GBPUSD Consolidation Optimizer — {len(bars)} bars, 30 days")
    print()
    
    results = []
    count = 0
    
    # Deep grid search
    for lookback in [20, 30, 40, 50, 60, 80]:
        for min_move in [10.0, 15.0, 20.0, 25.0, 30.0, 40.0]:
            for max_range in [5.0, 8.0, 10.0, 12.0, 15.0]:
                for min_bars in [8, 10, 15, 20, 25]:
                    for entry_pct in [0.1, 0.15, 0.2, 0.25, 0.3]:
                        for target_pct in [0.3, 0.4, 0.5, 0.6]:
                            for stop in [2.0, 2.5, 3.0, 4.0]:
                                count += 1
                                trades = simulate(
                                    bars, SPREAD,
                                    lookback, min_move, max_range, min_bars,
                                    entry_pct, target_pct, stop,
                                )
                                if len(trades) < 10:
                                    continue
                                
                                wins = [t for t in trades if t["pnl_usd"] > 0]
                                net = sum(t["pnl_usd"] for t in trades)
                                wr = len(wins) / len(trades) * 100
                                exp = net / len(trades)
                                
                                # Score: prioritize expectancy with trade count bonus
                                score = exp * min(len(trades) / 15, 1.0)
                                
                                if score > 0.02:  # Only keep promising configs
                                    results.append({
                                        "lookback": lookback,
                                        "min_move": min_move,
                                        "max_range": max_range,
                                        "min_bars": min_bars,
                                        "entry_pct": entry_pct,
                                        "target_pct": target_pct,
                                        "stop": stop,
                                        "trades": len(trades),
                                        "trades_per_day": round(len(trades) / 30, 1),
                                        "wr_pct": round(wr, 1),
                                        "net_usd": round(net, 2),
                                        "exp_usd": round(exp, 3),
                                        "score": round(score, 3),
                                    })
                                
                                if count % 1000 == 0:
                                    print(f"  Processed {count} configs, {len(results)} viable so far...", flush=True)
    
    results.sort(key=lambda r: r["score"], reverse=True)
    
    print(f"\n=== TOP 20 GBPUSD CONSOLIDATION CONFIGS ===")
    print(f"{'#':>3} | {'Params':>30} | {'Trd/Day':>7} | {'WR':>5} | {'Exp':>7} | {'Net':>7} | {'Score':>6}")
    print("-" * 80)
    
    for i, r in enumerate(results[:20], 1):
        params = f"l{r['lookback']}m{r['min_move']}r{r['max_range']}e{r['entry_pct']}t{r['target_pct']}"
        print(f"{i:>3} | {params:>30} | {r['trades_per_day']:>7.1f} | {r['wr_pct']:>4.1f}% | ${r['exp_usd']:>6.3f} | ${r['net_usd']:>6.2f} | {r['score']:>6.3f}")
    
    # Save
    if results:
        output_path = ROOT / "reports" / "consolidation_gbpusd_optimized.csv"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nSaved {len(results)} configs to {output_path}")
    
    mt5.shutdown()


if __name__ == "__main__":
    main()
