#!/usr/bin/env python3
"""Consolidation Breakout — the REAL edge: trade the RANGE BREAK, not the range.

User insight: "After large movements we have CONSOLIDATION. Ranges ARE predictable."

Technique:
1. Price makes a big move (20+ pips)
2. Price consolidates in a tight range (5-15 pips) for N bars
3. When price breaks the consolidation boundary, enter in that direction
4. Target: 2-3x the consolidation range width
5. Stop: opposite side of consolidation

The consolidation boundary is the PREDICTABLE part. The breakout direction becomes
predictable because:
- Consolidation accumulates energy (like a coiled spring)
- The first break of a tight range has momentum
- Market makers push price through the boundary to trigger stops

This is DIFFERENT from confirmed-displacement because:
- We're detecting the CONSOLIDATION first, then trading the break
- The consolidation acts as a volatility compression chamber
- The breakout has higher probability because the range was tight

Usage: python scripts/consolidation_breakout.py [--days 30] [--symbols SYM1 SYM2]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent

ALL_FX_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CHFJPY", "CADJPY",
    "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "GBPAUD", "GBPCAD", "GBPCHF",
    "GBPNZD", "NZDCAD", "NZDCHF",
]

SPREADS = {
    "EURUSD": 0.8, "GBPUSD": 1.0, "USDJPY": 0.6, "USDCHF": 1.0,
    "AUDUSD": 1.2, "NZDUSD": 1.5, "USDCAD": 1.2,
    "EURGBP": 1.0, "EURJPY": 1.2, "GBPJPY": 1.5, "AUDJPY": 1.5,
    "NZDJPY": 2.0, "CHFJPY": 1.5, "CADJPY": 1.5,
    "AUDCAD": 1.5, "AUDCHF": 1.5, "AUDNZD": 2.0, "CADCHF": 1.5,
    "GBPAUD": 2.0, "GBPCAD": 2.0, "GBPCHF": 1.5, "GBPNZD": 2.5,
    "NZDCAD": 2.0, "NZDCHF": 2.0,
}

PIP_SIZES = {}
for s in ALL_FX_SYMBOLS:
    PIP_SIZES[s] = 0.01 if "JPY" in s else 0.0001

UNITS_001_LOT = 1_000
MAX_HOLD = 30


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


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float, pip: float) -> float:
    raw = (exit_price - entry) / pip if direction == "BUY" else (entry - exit_price) / pip
    price_move = (raw - spread_pips) * pip
    raw_usd = price_move * UNITS_001_LOT
    if pip >= 0.01:
        raw_usd /= max(exit_price, 0.0001)
    return raw_usd


def simulate_consolidation_breakout(
    symbol: str, bars: list[dict], spread_pips: float, pip: float,
    lookback: int = 30,
    min_move_pips: float = 15.0,
    max_consolidation_range: float = 10.0,
    min_consolidation_bars: int = 10,
    breakout_buffer: float = 1.0,  # pips beyond range to confirm breakout
    target_mult: float = 2.0,  # target = range_width * target_mult
    stop_mult: float = 1.0,  # stop = range_width * stop_mult
    max_hold: int = MAX_HOLD,
) -> list[dict]:
    """Trade consolidation breakouts."""
    trades = []
    idx = lookback + 100
    
    while idx < len(bars) - max_hold - 2:
        # Check for prior large move
        recent = bars[max(0, idx-100):idx]
        if len(recent) < 20:
            idx += 1
            continue
        
        prices = [b["close"] for b in recent]
        total_move = (max(prices) - min(prices)) / pip
        
        if total_move < min_move_pips:
            idx += 1
            continue
        
        # Check consolidation
        cons = bars[idx - lookback: idx]
        if len(cons) < min_consolidation_bars:
            idx += 1
            continue
        
        cons_high = max(b["high"] for b in cons)
        cons_low = min(b["low"] for b in cons)
        cons_range = (cons_high - cons_low) / pip
        
        if cons_range > max_consolidation_range:
            idx += 1
            continue
        
        # Check for breakout
        cur = bars[idx]
        
        # Breakout above
        if cur["close"] > cons_high + breakout_buffer * pip:
            direction = "BUY"
            entry_price = cur["close"]
        
        # Breakout below
        elif cur["close"] < cons_low - breakout_buffer * pip:
            direction = "SELL"
            entry_price = cur["close"]
        
        else:
            idx += 1
            continue
        
        entry_idx = idx + 1
        if entry_idx >= len(bars) - 1:
            break
        
        # Target and stop based on range width
        target_pips = cons_range * target_mult
        stop_pips = cons_range * stop_mult
        
        # Minimums
        if target_pips < 3.0:
            target_pips = 3.0
        if stop_pips < 2.0:
            stop_pips = 2.0
        
        mfe = mae = 0.0
        exit_idx = exit_price = None
        
        for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
            bar = bars[j]
            if direction == "BUY":
                fav = (bar["high"] - entry_price) / pip
                adv = (entry_price - bar["low"]) / pip
            else:
                fav = (entry_price - bar["low"]) / pip
                adv = (bar["high"] - entry_price) / pip
            
            mfe = max(mfe, fav)
            mae = max(mae, adv)
            
            if mfe >= target_pips:
                exit_idx = j
                exit_price = entry_price + target_pips * pip if direction == "BUY" else entry_price - target_pips * pip
                break
            if adv >= stop_pips:
                exit_idx = j
                exit_price = entry_price - stop_pips * pip if direction == "BUY" else entry_price + stop_pips * pip
                break
        
        if exit_idx is None:
            exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
            exit_price = bars[exit_idx]["close"]
        
        pnl_pips = ((exit_price - entry_price) / pip if direction == "BUY"
                    else (entry_price - exit_price) / pip) - spread_pips
        pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips, pip)
        
        trades.append({
            "symbol": symbol, "direction": direction,
            "entry_idx": entry_idx, "exit_idx": exit_idx,
            "hold_bars": exit_idx - entry_idx + 1,
            "pnl_pips": pnl_pips, "pnl_usd": pnl_usd,
            "mfe_pips": mfe, "mae_pips": mae,
            "range_width": round(cons_range, 1),
        })
        
        # Skip ahead
        idx += 5
    else:
        idx += 1
    
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()
    
    mt5.initialize()
    
    symbols = args.symbols if args.symbols else ALL_FX_SYMBOLS
    
    print(f"Consolidation Breakout — {args.days} days, {len(symbols)} symbols")
    print()
    
    all_results = []
    
    for symbol in symbols:
        pip = PIP_SIZES.get(symbol, 0.0001)
        spread = SPREADS.get(symbol, 1.0)
        bars = load_bars(symbol, args.days)
        if not bars or len(bars) < 300:
            print(f"  {symbol}: INSUFFICIENT DATA")
            continue
        
        print(f"  Testing {symbol}...", end=" ", flush=True)
        
        best_result = None
        best_score = -999
        
        # Minimal grid search
        for lookback in [30]:
            for min_move in [20.0]:
                for max_range in [10.0]:
                    for min_bars in [15]:
                        for buffer in [1.0]:
                            for target in [2.0]:
                                for stop in [1.0]:
                                    trades = simulate_consolidation_breakout(
                                        symbol, bars, spread, pip,
                                        lookback, min_move, max_range, min_bars,
                                        buffer, target, stop,
                                    )
                                    if len(trades) < 10:
                                        continue
                                    
                                    wins = [t for t in trades if t["pnl_usd"] > 0]
                                    net = sum(t["pnl_usd"] for t in trades)
                                    wr = len(wins) / len(trades) * 100
                                    exp = net / len(trades)
                                    
                                    score = exp * min(len(trades) / 15, 1.0)
                                    
                                    if score > best_score:
                                        best_score = score
                                        best_result = {
                                            "symbol": symbol,
                                            "lookback": lookback,
                                            "min_move": min_move,
                                            "max_range": max_range,
                                            "min_bars": min_bars,
                                            "buffer": buffer,
                                            "target_mult": target,
                                            "stop_mult": stop,
                                            "trades": len(trades),
                                            "trades_per_day": round(len(trades) / args.days, 1),
                                            "wr_pct": round(wr, 1),
                                            "net_usd": round(net, 2),
                                            "exp_usd": round(exp, 3),
                                            "avg_hold_bars": round(mean(t["hold_bars"] for t in trades), 1),
                                            "avg_range": round(mean(t["range_width"] for t in trades), 1),
                                        }
        
        if best_result:
            all_results.append(best_result)
            print(f"✅ exp=${best_result['exp_usd']:+.3f} n={best_result['trades']} wr={best_result['wr_pct']:.0f}%")
        else:
            print("❌ no viable configs")
    
    all_results.sort(key=lambda r: r["exp_usd"], reverse=True)
    
    if all_results:
        fieldnames = list(all_results[0].keys())
        print("\n" + ",".join(fieldnames))
        for r in all_results:
            print(",".join(str(r[k]) for k in fieldnames))
        
        output_path = ROOT / "reports" / "consolidation_breakout.csv"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nSaved to {output_path}")
        
        print("\n=== TOP 20 CONSOLIDATION BREAKOUT CONFIGS ===")
        print(f"{'Symbol':>8} | {'Params':>28} | {'Trd/Day':>7} | {'WR':>5} | {'Exp/Trade':>9} | {'Net USD':>9}")
        print("-" * 85)
        for r in all_results[:20]:
            params = f"l{r['lookback']}m{r['min_move']}r{r['max_range']}b{r['buffer']}t{r['target_mult']}"
            print(f"{r['symbol']:>8} | {params:>28} | {r['trades_per_day']:>7.1f} | {r['wr_pct']:>4.1f}% | ${r['exp_usd']:>8.3f} | ${r['net_usd']:>8.2f}")
    else:
        print("No viable configs found.")
    
    mt5.shutdown()


if __name__ == "__main__":
    main()
