#!/usr/bin/env python3
"""
Multi-coin RSI MR scan with adapted TP targets.
Tests whether the RSI(3) oversold mean reversion edge generalizes
to other coins when using TP% scaled to each coin's volatility.
"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from rave_rsi_mr_live_v2 import fetch_candles_chunked

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "reports" / "_multicoin_adaptive_tp_results.json"

COINS = [
    "RAVE-USD", "MOG-USD", "FARTCOIN-USD", "VVV-USD",
    "COMP-USD", "PEPE-USD", "ARB-USD", "SUI-USD",
    "SOL-USD", "DOGE-USD", "LINK-USD", "ETH-USD",
]

TP_PCTS = [5.0, 10.0, 15.0, 25.0]  # Test multiple TP targets
RSI_PERIOD = 3
OS_THRESH = 30
MAX_HOLD = 48

def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0

def backtest_coin(candles, tp_pct, starting_cash=48.0):
    """Minimal RSI(3) MR backtest with configurable TP%."""
    cash = starting_cash
    position = None
    history = []
    wins = losses = 0
    realized = 0.0
    fee_rate = 0.004  # 40 bps
    
    for c in candles:
        close = float(c["close"])
        high = float(c["high"])
        candle_open = float(c["open"])
        history.append(close)
        if len(history) > 500:
            history = history[-500:]
        
        # Exit
        if position:
            position["hold"] += 1
            exit_price = None
            
            if high >= position["tp"]:
                exit_price = position["tp"]
            elif position["hold"] >= MAX_HOLD:
                exit_price = close
            
            if exit_price is not None:
                gross = (exit_price - position["ep"]) * position["units"]
                exit_fee = exit_price * position["units"] * fee_rate
                net = gross - position["entry_fee"] - exit_fee
                cash += position["q"] + net
                realized += net
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                position = None
        
        # Entry
        if position is None and cash >= 10.0 and len(history) >= RSI_PERIOD + 2:
            rsi = compute_rsi(history[:-1], RSI_PERIOD)
            if rsi <= OS_THRESH:
                deploy = cash
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / candle_open
                tp = candle_open * (1 + tp_pct / 100.0)
                cash -= deploy
                position = {
                    "ep": candle_open, "q": deploy, "hold": 0,
                    "tp": tp, "units": units, "entry_fee": entry_fee
                }
    
    closes_total = wins + losses
    total_pnl = cash + (position["q"] if position else 0) - starting_cash
    return {
        "closes": closes_total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, closes_total) * 100, 1),
        "realized_pnl": round(realized, 4),
        "total_pnl": round(total_pnl, 4),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600
    
    all_results = []
    
    print(f"Adaptive TP scan: {len(COINS)} coins × {len(TP_PCTS)} TP targets")
    print(f"{'Coin':>15}  {'TP%':>4}  {'Trades':>6}  {'WR':>5}  {'PnL':>10}")
    print("-" * 50)
    
    for coin in COINS:
        try:
            candles = fetch_candles_chunked(client, coin, start, now)
        except Exception as e:
            print(f"{coin:>15}  ERROR: {e}")
            continue
        
        if len(candles) < 20:
            print(f"{coin:>15}  SKIP (only {len(candles)} candles)")
            continue
        
        best_tp = None
        best_pnl = -999
        
        for tp in TP_PCTS:
            result = backtest_coin(candles, tp)
            result["coin"] = coin
            result["tp_pct"] = tp
            result["candles"] = len(candles)
            all_results.append(result)
            
            flag = "+" if result["realized_pnl"] > 0 else " "
            print(f"{coin:>15}  {tp:>3.0f}%  {result['closes']:>6}  "
                  f"{result['win_rate']:>4.0f}%  {flag}${result['realized_pnl']:>8.2f}")
            
            if result["realized_pnl"] > best_pnl:
                best_pnl = result["realized_pnl"]
                best_tp = tp
        
        if best_pnl > 0:
            print(f"  → BEST: {best_tp}% TP = +${best_pnl:.2f}")
        print()
        time.sleep(0.3)
    
    # Summary: best TP per coin
    print("\n" + "=" * 60)
    print("BEST TP% PER COIN (positive PnL only):")
    print("-" * 60)
    
    by_coin = {}
    for r in all_results:
        coin = r["coin"]
        if coin not in by_coin or r["realized_pnl"] > by_coin[coin]["realized_pnl"]:
            by_coin[coin] = r
    
    for coin, r in sorted(by_coin.items(), key=lambda x: x[1]["realized_pnl"], reverse=True):
        flag = "🟢" if r["realized_pnl"] > 0 else "🔴"
        print(f"{flag} {coin:>15}  TP={r['tp_pct']:>3.0f}%  "
              f"trades={r['closes']}  WR={r['win_rate']}%  PnL=${r['realized_pnl']:+.2f}")
    
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nFull results: {RESULTS_PATH}")

if __name__ == "__main__":
    main()
