#!/usr/bin/env python3
"""
Debug IOTX BB Reversion discrepancy:
- Reconciliation engine: -$35.46, WR=33.3%, 144 signals
- Sweep engine (qwen-trading-bots): +$44, WR=79.1%, 43 trades
- Strategy library: ??? (this script finds out)

Run with EXACT reconciliation params to isolate the semantic gap.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import bb_reversion, compute_bb

RECONCILIATION_CANDLES = os.path.join(
    os.path.dirname(__file__), "..", "reports", "reconciliation_candles.json"
)

def load_candles():
    with open(RECONCILIATION_CANDLES) as f:
        data = json.load(f)
    # Structure: {"coins": {"IOTX-USD": {"candles": [...], "count": N}}}
    # Also normalize string values to floats
    raw = data["coins"]["IOTX-USD"]["candles"]
    candles = []
    for c in raw:
        candles.append({
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "start": int(c["start"]),
            "volume": float(c["volume"]),
        })
    return candles

def main():
    candles = load_candles()
    print(f"Loaded {len(candles)} IOTX-USD candles")
    print(f"First candle: {candles[0]}")
    print(f"Last candle:  {candles[-1]}")
    print()

    # Sweep engine params (from qwen-trading-bots claims)
    sweep_params = {
        "bb_period": 20,
        "rsi_period": 3,
        "rsi_thresh": 30,
        "proximity_pct": 3.0,
        "sl_pct": 5.0,
        "max_hold": 24,
    }

    print("=" * 70)
    print("STRATEGY LIBRARY — IOTX BB Reversion")
    print("Params: bb_period=20, rsi_period=3, rsi_thresh=30, proximity=3%, SL=5%, max_hold=24")
    print()

    # Run with reconciliation-equivalent params
    result = bb_reversion(
        candles,
        bb_period=20,
        rsi_period=3,
        rsi_thresh=30,
        proximity_pct=3.0,
        sl_pct=5.0,
        max_hold=24,
        fee_rate=0.004,
        starting_cash=48.0,
        entry_slip=0.0,       # Reconciliation has no slippage
        exit_slip=0.0,
        fill_prob=1.0,
    )

    print(f"  Net PnL:    ${result['net_pnl']:>8.2f}")
    print(f"  Return:      {result['return_pct']:>7.1f}%")
    print(f"  Trades:      {result['trades']}")
    print(f"  Win Rate:    {result['win_rate']}%")
    print(f"  Max DD:      {result['max_drawdown']}%")
    print(f"  Signals:     {result['signals']}")
    print(f"  Fill Rate:   {result['fill_rate']}%")
    print()

    # Compare with reconciliation ground truth
    print("=" * 70)
    print("COMPARISON:")
    print(f"  {'Metric':<15} {'Recon Engine':>14} {'Sweep Engine':>14} {'Strat Library':>14}")
    print(f"  {'Net PnL':<15} {'$-35.46':>14} {'+$44.00':>14} ${result['net_pnl']:>11.2f}")
    print(f"  {'Win Rate':<15} {'33.3%':>14} {'79.1%':>14} {result['win_rate']:>12.1f}%")
    print(f"  {'Trades':<15} {'144':>14} {'43':>14} {result['trades']:>14}")
    print(f"  {'Signals':<15} {'144':>14} {'101':>14} {result['signals']:>14}")
    print()

    # Now test: what if we use $100 starting cash (library default)?
    result_100 = bb_reversion(
        candles,
        bb_period=20, rsi_period=3, rsi_thresh=30, proximity_pct=3.0,
        sl_pct=5.0, max_hold=24,
        fee_rate=0.004, starting_cash=100.0,
        entry_slip=0.0008, exit_slip=0.0, fill_prob=1.0,
    )
    print("=" * 70)
    print("STRATEGY LIBRARY — With default $100 cash + 0.08% entry slippage:")
    print(f"  Net PnL:    ${result_100['net_pnl']:>8.2f}")
    print(f"  Win Rate:    {result_100['win_rate']}%")
    print(f"  Trades:      {result_100['trades']}")
    print(f"  Signals:     {result_100['signals']}")
    print()

    # Signal-level analysis: sample a few signals to see what they look like
    print("=" * 70)
    print("SIGNAL DIAGNOSTIC — First 5 signals with context:")
    closes_history = []
    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        closes_history.append(close)
        if len(closes_history) > 500:
            closes_history = closes_history[-500:]

        if len(closes_history) < 22:  # need bb_period+2
            continue

        rsi_val = compute_rsi_simple(closes_history[:-1], 3)
        sma, upper, lower = compute_bb(closes_history[:-1], 20)
        if lower is None:
            continue

        proximity = (close - lower) / lower * 100 if lower > 0 else 999

        if rsi_val <= 30 and proximity <= 3.0:
            # This would be a signal
            bar_high = float(c["high"])
            bar_low = float(c["low"])
            candle_open = float(c["open"])
            print(f"  Signal at candle {i}: close={close:.6f}, open={candle_open:.6f}, "
                  f"high={bar_high:.6f}, low={bar_low:.6f}, "
                  f"BB_lower={lower:.6f}, proximity={proximity:.2f}%, RSI={rsi_val:.1f}")
            # What's the TP target?
            print(f"    TP target (BB mid): {sma:.6f} ({(sma/close-1)*100:.2f}% from close)")
            # Check: does price reach TP within next 24 bars?
            tp_hit = False
            sl_hit = False
            sl_level = close * 0.95
            for j in range(i+1, min(i+25, len(candles))):
                if float(candles[j]["high"]) >= sma:
                    tp_hit = True
                    break
                if float(candles[j]["low"]) <= sl_level:
                    sl_hit = True
                    break
            print(f"    Within 24 bars: TP_hit={tp_hit}, SL_hit={sl_hit}")

    print()
    print("=" * 70)
    print("DIAGNOSIS COMPLETE")


def compute_rsi_simple(closes, period=3):
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


if __name__ == "__main__":
    main()
