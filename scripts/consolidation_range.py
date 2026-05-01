#!/usr/bin/env python3
"""Consolidation Range Trading — trade the predictable range AFTER big moves.

INSIGHT: After large movements, markets consolidate in tight ranges.
The consolidation boundaries are PREDICTABLE. The breakout direction is NOT.

Strategy:
1. Detect large movements (price moves > X pips in Y bars)
2. Identify the consolidation that follows (tight range, decreasing ATR)
3. Trade the range boundaries — BUY bottom, SELL top
4. Quick targets (range midpoint), tight stops (just outside range)
5. Exit BEFORE the consolidation breaks

The range is predictable because market makers accumulate/distribute within
a defined zone. The edges are where liquidity sits.

Usage: python scripts/consolidation_range.py [--days 30] [--symbols SYM1 SYM2]
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
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


def detect_consolidation(
    bars: list[dict], idx: int, pip: float,
    lookback: int = 30,
    min_move_pips: float = 15.0,
    max_consolidation_range: float = 8.0,
    min_consolidation_bars: int = 10,
) -> tuple[bool, float, float, int]:
    """Detect if we're in a consolidation after a large move.
    
    Returns: (is_consolidation, range_high, range_low, bars_in_consolidation)
    """
    if idx < lookback + 50:
        return False, 0, 0, 0

    # Check for a large move in the recent past
    # Look back up to 100 bars for a significant move
    max_lookback = 100
    start_idx = max(0, idx - max_lookback)
    
    # Find the most recent significant move
    recent_bars = bars[start_idx:idx]
    if len(recent_bars) < 20:
        return False, 0, 0, 0
    
    # Calculate total move over the lookback period
    prices = [b["close"] for b in recent_bars]
    max_price = max(prices)
    min_price = min(prices)
    total_move = (max_price - min_price) / pip
    
    if total_move < min_move_pips:
        return False, 0, 0, 0
    
    # Now check if we're in consolidation AFTER the move
    # Look at the most recent 'lookback' bars
    consolidation_bars = bars[idx - lookback: idx]
    if len(consolidation_bars) < min_consolidation_bars:
        return False, 0, 0, 0
    
    cons_high = max(b["high"] for b in consolidation_bars)
    cons_low = min(b["low"] for b in consolidation_bars)
    cons_range = (cons_high - cons_low) / pip
    
    if cons_range > max_consolidation_range:
        return False, 0, 0, 0
    
    # Check that ATR is decreasing (volatility contraction)
    current_atr = compute_atr(bars, idx, 14)
    prev_atr = compute_atr(bars, idx - lookback, 14)
    
    if current_atr <= 0 or prev_atr <= 0:
        return False, 0, 0, 0
    
    atr_ratio = current_atr / prev_atr
    
    # Consolidation confirmed if ATR has contracted
    if atr_ratio < 0.7:  # ATR is 30%+ lower than before
        return True, cons_high, cons_low, lookback
    
    return False, 0, 0, 0


def simulate_consolidation_range(
    symbol: str, bars: list[dict], spread_pips: float, pip: float,
    consolidation_lookback: int = 30,
    min_move_pips: float = 15.0,
    max_consolidation_range: float = 8.0,
    min_consolidation_bars: int = 10,
    entry_offset: float = 0.2,  # Enter when price is within this % of range edge
    target_pct: float = 0.5,  # Target is this % of range width
    stop_pips: float = 3.0,
    max_hold: int = 20,
) -> list[dict]:
    """Trade consolidation range boundaries."""
    trades = []
    idx = consolidation_lookback + 100  # Warmup
    
    while idx < len(bars) - max_hold - 2:
        is_cons, cons_high, cons_low, bars_in_cons = detect_consolidation(
            bars, idx, pip, consolidation_lookback,
            min_move_pips, max_consolidation_range, min_consolidation_bars
        )
        
        if not is_cons:
            idx += 1
            continue
        
        range_width = (cons_high - cons_low) / pip
        range_mid = (cons_high + cons_low) / 2
        
        cur = bars[idx]
        cur_price = cur["close"]
        
        # Calculate position in range (0 = bottom, 1 = top)
        if range_width > 0:
            position_in_range = (cur_price - cons_low) / (cons_high - cons_low)
        else:
            position_in_range = 0.5
        
        # Entry logic: fade the edges
        direction = None
        entry_price = None
        
        # Near top of range -> SELL
        if position_in_range >= (1.0 - entry_offset):
            direction = "SELL"
            entry_price = cur_price
        
        # Near bottom of range -> BUY
        elif position_in_range <= entry_offset:
            direction = "BUY"
            entry_price = cur_price
        
        if direction is not None:
            entry_idx = idx + 1
            if entry_idx >= len(bars) - 1:
                break
            
            # Target: move toward range midpoint
            target_pips = range_width * target_pct
            if target_pips < 2.0:
                target_pips = 2.0
            
            mfe_pips = 0.0
            mae_pips = 0.0
            exit_idx = None
            exit_price = None
            
            for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
                bar = bars[j]
                if direction == "BUY":
                    favorable = (bar["high"] - entry_price) / pip
                    adverse = (entry_price - bar["low"]) / pip
                else:
                    favorable = (entry_price - bar["low"]) / pip
                    adverse = (bar["high"] - entry_price) / pip
                
                mfe_pips = max(mfe_pips, favorable)
                mae_pips = max(mae_pips, adverse)
                
                # Target reached
                if mfe_pips >= target_pips:
                    exit_idx = j
                    exit_price = entry_price + target_pips * pip if direction == "BUY" else entry_price - target_pips * pip
                    break
                
                # Stop: just outside the range
                if adverse >= stop_pips:
                    exit_idx = j
                    exit_price = entry_price - stop_pips * pip if direction == "BUY" else entry_price + stop_pips * pip
                    break
                
                # Price broke out of consolidation range
                if direction == "BUY" and bar["close"] < cons_low - 2 * pip:
                    exit_idx = j
                    exit_price = bar["close"]
                    break
                if direction == "SELL" and bar["close"] > cons_high + 2 * pip:
                    exit_idx = j
                    exit_price = bar["close"]
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
                "mfe_pips": mfe_pips, "mae_pips": mae_pips,
                "range_width_pips": round(range_width, 1),
                "position_in_range": round(position_in_range, 2),
            })
            
            # Skip ahead after entry
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
    
    print(f"Consolidation Range Trading — {args.days} days, {len(symbols)} symbols")
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
        
        # Grid search over consolidation parameters (minimal for speed)
        for lookback in [30, 50]:
            for min_move in [20.0]:
                for max_range in [10.0]:
                    for min_bars in [15]:
                        for target_pct in [0.5]:
                            for stop in [3.0]:
                                trades = simulate_consolidation_range(
                                    symbol, bars, spread, pip,
                                    consolidation_lookback=lookback,
                                    min_move_pips=min_move,
                                    max_consolidation_range=max_range,
                                    min_consolidation_bars=min_bars,
                                    entry_offset=0.2,
                                    target_pct=target_pct,
                                    stop_pips=stop,
                                )
                                if len(trades) < 5:
                                    continue
                                
                                wins = [t for t in trades if t["pnl_usd"] > 0]
                                net_usd = sum(t["pnl_usd"] for t in trades)
                                wr = len(wins) / len(trades) * 100
                                exp_usd = net_usd / len(trades)
                                
                                # Score: prioritize expectancy with minimum trade count
                                score = exp_usd * min(len(trades) / 10, 1.0)
                                
                                if score > best_score:
                                    best_score = score
                                    best_result = {
                                        "symbol": symbol,
                                        "lookback": lookback,
                                        "min_move_pips": min_move,
                                        "max_range_pips": max_range,
                                        "min_bars": min_bars,
                                        "target_pct": target_pct,
                                        "stop_pips": stop,
                                        "trades": len(trades),
                                        "trades_per_day": round(len(trades) / args.days, 1),
                                        "wr_pct": round(wr, 1),
                                        "net_usd": round(net_usd, 2),
                                        "exp_usd": round(exp_usd, 3),
                                        "avg_hold_bars": round(mean(t["hold_bars"] for t in trades), 1),
                                        "avg_range_width": round(mean(t["range_width_pips"] for t in trades), 1),
                                    }
        
        if best_result:
            all_results.append(best_result)
            print(f"✅ exp=${best_result['exp_usd']:+.3f} n={best_result['trades']} wr={best_result['wr_pct']:.0f}%")
        else:
            print("❌ no viable configs")
    
    # Sort and output
    all_results.sort(key=lambda r: r["exp_usd"], reverse=True)
    
    if all_results:
        fieldnames = list(all_results[0].keys())
        print("\n" + ",".join(fieldnames))
        for r in all_results:
            print(",".join(str(r[k]) for k in fieldnames))
        
        output_path = ROOT / "reports" / "consolidation_range.csv"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nSaved to {output_path}")
        
        print("\n=== TOP 20 CONSOLIDATION RANGE CONFIGS ===")
        print(f"{'Symbol':>8} | {'Params':>25} | {'Trd/Day':>7} | {'WR':>5} | {'Exp/Trade':>9} | {'Net USD':>9} | {'Avg Range':>9}")
        print("-" * 90)
        for r in all_results[:20]:
            params = f"l{r['lookback']}m{r['min_move_pips']}r{r['max_range_pips']}"
            print(f"{r['symbol']:>8} | {params:>25} | {r['trades_per_day']:>7.1f} | {r['wr_pct']:>4.1f}% | ${r['exp_usd']:>8.3f} | ${r['net_usd']:>8.2f} | {r['avg_range_width']:>7.1f}p")
    else:
        print("No viable consolidation range configs found.")
    
    mt5.shutdown()


if __name__ == "__main__":
    main()
