"""
Backtest: SL=0% vs SL=3% on RAVE/A8/CFG momentum strategies

This script validates the stop loss improvement proposal by running
a sweep comparing zero stop loss vs 3% asymmetric stops.

Usage:
    python scripts/backtest_sl_comparison.py

Output:
    Prints comparison table showing PnL, WR, trade count, avg trade for each coin
    with SL=0% vs SL=3%
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# Test coins with SL=0%
TEST_COINS = [
    {"coin": "RAVE-USD", "lookback": 15, "tp_pct": 0.10, "max_hold": 36},
    {"coin": "A8-USD", "lookback": 10, "tp_pct": 0.15, "max_hold": 48},
    {"coin": "CFG-USD", "lookback": 50, "tp_pct": 0.15, "max_hold": 48},
]

SL_LEVELS = [0.00, 0.03]  # 0% vs 3%

# Fee rate (low volume tier)
FEE_RATE = 0.0040  # 40bps per side


def fetch_historical_candles(coin, days=30):
    """
    Fetch historical candles for backtesting.
    In production, this would call the Coinbase API.
    For now, returns mock data structure for validation.
    """
    # TODO: Implement actual Coinbase API fetch
    # For validation, we can use cached data or generate realistic mock data
    print(f"  Fetching {days} days of {coin} data...")
    
    # Placeholder: Return empty list (needs real data)
    # In production, replace with:
    # from coinbase_advanced_client import CoinbaseAdvancedClient
    # client = CoinbaseAdvancedClient(...)
    # return client.get_candles(coin, days=days)
    
    return []


def run_backtest(coin_config, sl_pct, candles):
    """
    Run momentum strategy backtest with given stop loss level.
    
    Returns dict with:
    - pnl: net PnL
    - wr: win rate
    - trades: total trades
    - avg_trade: average PnL per trade
    - fees: total fees paid
    """
    cash = 100.0  # Start with $100
    deploy_fraction = 0.95
    position = None
    history = []
    candle_history = []
    
    signals = 0
    closes = 0
    wins = 0
    losses = 0
    total_fees = 0
    
    lookback = coin_config["lookback"]
    tp_pct = coin_config["tp_pct"]
    max_hold = coin_config["max_hold"]
    
    for candle in candles:
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])
        
        history.append(close)
        candle_history.append(candle)
        
        # Keep history manageable
        if len(history) > 500:
            history = history[-500:]
            candle_history = candle_history[-500:]
        
        # Exit logic
        if position:
            position["hold"] += 1
            
            exit_price = None
            exit_reason = None
            
            # Take profit
            if high >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "tp"
            # Stop loss (if enabled)
            elif sl_pct > 0 and low <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "sl"
            # Timeout
            elif position["hold"] >= max_hold:
                exit_price = close
                exit_reason = "timeout"
            
            if exit_price:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                entry_fee = position["entry_fee"]
                exit_fee = exit_price * units * FEE_RATE
                net = gross - entry_fee - exit_fee
                
                cash += position["q"] + net
                closes += 1
                total_fees += entry_fee + exit_fee
                
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                
                position = None
        
        # Entry logic
        if position is None and cash >= 10.0:
            if len(candle_history) > lookback + 1:
                recent_high = max(float(c["high"]) for c in candle_history[-(lookback+1):-1])
                breakout_pct = (high - recent_high) / recent_high if recent_high > 0 else 0
                
                # Require minimum breakout (0.5%)
                min_breakout = 0.005
                if high > recent_high and breakout_pct >= min_breakout:
                    signals += 1
                    
                    deploy = cash * deploy_fraction
                    entry_price = open_price
                    entry_fee = deploy * FEE_RATE
                    units = (deploy - entry_fee) / entry_price
                    tp = entry_price * (1 + tp_pct)
                    sl = entry_price * (1 - sl_pct) if sl_pct > 0 else 0
                    
                    cash -= deploy
                    position = {
                        "ep": entry_price,
                        "q": deploy,
                        "units": units,
                        "tp": tp,
                        "sl": sl,
                        "hold": 0,
                        "entry_fee": entry_fee,
                    }
    
    # Close any remaining position at last close
    if position:
        units = position["units"]
        exit_price = history[-1] if history else position["ep"]
        gross = (exit_price - position["ep"]) * units
        entry_fee = position["entry_fee"]
        exit_fee = exit_price * units * FEE_RATE
        net = gross - entry_fee - exit_fee
        
        cash += position["q"] + net
        closes += 1
        total_fees += entry_fee + exit_fee
        
        if net > 0:
            wins += 1
        else:
            losses += 1
    
    wr = wins / max(1, closes) * 100
    pnl = cash - 100.0
    avg_trade = pnl / max(1, closes)
    
    return {
        "pnl": round(pnl, 2),
        "wr": round(wr, 1),
        "trades": closes,
        "avg_trade": round(avg_trade, 2),
        "fees": round(total_fees, 2),
        "final_cash": round(cash, 2),
    }


def main():
    print("=" * 80)
    print("STOP LOSS COMPARISON BACKTEST: SL=0% vs SL=3%")
    print("=" * 80)
    print()
    
    results = []
    
    for coin_cfg in TEST_COINS:
        coin = coin_cfg["coin"]
        print(f"\n{'─' * 80}")
        print(f"Testing {coin}")
        print(f"{'─' * 80}")
        
        # Fetch candles
        candles = fetch_historical_candles(coin, days=30)
        
        if not candles:
            print(f"  ⚠️  No candle data available for {coin}")
            print(f"  To complete this backtest, you need:")
            print(f"    - 30 days of 5-minute candles for {coin}")
            print(f"    - Approx {30 * 288} candles (288 per day)")
            print(f"")
            print(f"  Options:")
            print(f"    1. Add Coinbase API credentials and implement fetch_historical_candles()")
            print(f"    2. Use cached data from previous backtests")
            print(f"    3. Generate realistic mock data for validation")
            continue
        
        # Test each SL level
        for sl_pct in SL_LEVELS:
            sl_label = f"SL={sl_pct*100:.0f}%"
            print(f"  Running {sl_label}...", end=" ")
            
            result = run_backtest(coin_cfg, sl_pct, candles)
            results.append({
                "coin": coin,
                "sl_pct": sl_pct,
                **result
            })
            
            print(f"PnL=${result['pnl']:+.2f}, WR={result['wr']:.1f}%, Trades={result['trades']}")
    
    # Print comparison table
    if results:
        print(f"\n\n{'=' * 80}")
        print("COMPARISON TABLE")
        print(f"{'=' * 80}")
        print(f"{'Coin':<12} {'SL':<8} {'PnL':>10} {'WR':>8} {'Trades':>8} {'Avg Trade':>10} {'Fees':>10}")
        print(f"{'─' * 80}")
        
        for r in results:
            sl_label = f"{r['sl_pct']*100:.0f}%"
            print(f"{r['coin']:<12} {sl_label:<8} ${r['pnl']:>9.2f} {r['wr']:>7.1f}% {r['trades']:>8} ${r['avg_trade']:>9.2f} ${r['fees']:>9.2f}")
        
        print(f"{'=' * 80}")
        print()
        print("Interpretation:")
        print("  - If SL=3% has similar PnL with fewer losses → net positive (proposal validated)")
        print("  - If SL=3% has lower PnL → stops are hitting trades that would recover (proposal rejected)")
        print("  - If SL=3% has higher WR → stops improve trade quality (proposal strongly validated)")
        print()
    
    print("Backtest complete.")


if __name__ == "__main__":
    main()
