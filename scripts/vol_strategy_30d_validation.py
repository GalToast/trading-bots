#!/usr/bin/env python3
"""
Volatility Strategy 30D Validation — Ground Truth Engine

Testing 2 strategies promoted from @qwen-strategies-tester's 7d sweep:
1. vol_breakout — Buy when BB width contracts then expands (squeeze → breakout)
2. atr_trailing — ATR-based trailing stop entry/exit

Tested on 9 portfolio coins with 30d cached candles.
"""
import json, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import backtest

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days="30d"):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}.json")
    if not os.path.exists(path): return []
    with open(path) as f: data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

def compute_bb_width(closes, period=20):
    if len(closes) < period: return None
    window = closes[-period:]
    sma = sum(window) / period
    std = (sum((x - sma)**2 for x in window) / period) ** 0.5
    return (2 * std) / sma * 100 if sma > 0 else None

def compute_atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i-1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period: return None
    return sum(trs[-period:]) / period

def vol_breakout_entry(candles_history, closes_history, candle, params):
    """Buy when BB width expands after contracting (squeeze → breakout)."""
    bb_period = params.get("bb_period", 20)
    squeeze_thresh = params.get("squeeze_thresh", 1.5)  # BB width must have been below this
    expansion_mult = params.get("expansion_mult", 1.5)  # Current width must be X times the recent avg
    lookback = params.get("lookback", 10)  # How many bars to check for squeeze

    if len(closes_history) < bb_period + lookback + 1:
        return False

    # Check if BB width was squeezed recently (within last lookback bars)
    widths = []
    for i in range(len(closes_history) - lookback, len(closes_history)):
        if i < bb_period: continue
        w = compute_bb_width(closes_history[:i+1], bb_period)
        if w is not None:
            widths.append(w)

    if not widths:
        return False

    min_recent_width = min(widths)
    current_width = compute_bb_width(closes_history, bb_period)
    if current_width is None or min_recent_width <= 0:
        return False

    # Signal: recent squeeze + current expansion
    return min_recent_width < squeeze_thresh and current_width > min_recent_width * expansion_mult

def atr_trailing_entry(candles_history, closes_history, candle, params):
    """Buy when price breaks above recent high with ATR confirmation."""
    lookback = params.get("lookback", 20)
    atr_period = params.get("atr_period", 14)
    atr_mult = params.get("atr_mult", 1.5)

    if len(closes_history) < lookback + 2:
        return False

    current_price = float(candle["close"])
    recent_high = max(closes_history[-lookback-1:-1])

    # Check ATR
    if len(closes_history) < atr_period + 1:
        return False

    # Simple ATR approximation using close-to-close changes
    changes = [abs(closes_history[i] - closes_history[i-1]) for i in range(max(1, len(closes_history)-atr_period), len(closes_history))]
    if not changes:
        return False
    avg_atr = sum(changes) / len(changes)
    if avg_atr <= 0:
        return False

    # Signal: breakout with sufficient volatility
    return current_price > recent_high and (current_price - recent_high) / avg_atr > atr_mult

COINS = ["RAVE-USD", "GHST-USD", "TRU-USD", "SUP-USD", "A8-USD", "BAL-USD",
         "IOTX-USD", "CFG-USD", "NOM-USD"]

print("=" * 70)
print("VOLATILITY STRATEGY 30D VALIDATION — Ground Truth Engine")
print("=" * 70)

results = []

for coin in COINS:
    candles = load(coin)
    if not candles or len(candles) < 500:
        print(f"\n{coin}: insufficient data ({len(candles)} candles)")
        continue

    print(f"\n{coin}: {len(candles)} candles")

    # Vol Breakout sweep
    vb_best = None
    vb_results = []
    for bp in [14, 20, 30]:
        for st in [0.5, 1.0, 1.5, 2.0]:
            for em in [1.2, 1.5, 2.0]:
                params = {"bb_period": bp, "squeeze_thresh": st, "expansion_mult": em}
                r = backtest(candles, vol_breakout_entry, params,
                             fee_rate=0.004, starting_cash=48.0,
                             entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                vb_results.append((r["net_pnl"], bp, st, em, r))
    vb_results.sort(reverse=True)
    vb_best = vb_results[0] if vb_results else None
    vb_profitable = len([x for x in vb_results if x[0] > 0])

    print(f"  Vol Breakout: {len(vb_results)} combos, {vb_profitable} profitable")
    if vb_best:
        print(f"    Best: bb={vb_best[1]}, squeeze={vb_best[2]}, expand={vb_best[3]}")
        print(f"    PnL=${vb_best[0]:.2f}, WR={vb_best[4]['win_rate']}%, Trades={vb_best[4]['trades']}")

    # ATR Trailing sweep
    at_results = []
    for lb in [10, 20, 30, 50]:
        for ap in [7, 14, 21]:
            for am in [0.5, 1.0, 1.5, 2.0]:
                params = {"lookback": lb, "atr_period": ap, "atr_mult": am}
                r = backtest(candles, atr_trailing_entry, params,
                             fee_rate=0.004, starting_cash=48.0,
                             entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                at_results.append((r["net_pnl"], lb, ap, am, r))
    at_results.sort(reverse=True)
    at_best = at_results[0] if at_results else None
    at_profitable = len([x for x in at_results if x[0] > 0])

    print(f"  ATR Trailing: {len(at_results)} combos, {at_profitable} profitable")
    if at_best:
        print(f"    Best: lb={at_best[1]}, atr={at_best[2]}, mult={at_best[3]}")
        print(f"    PnL=${at_best[0]:.2f}, WR={at_best[4]['win_rate']}%, Trades={at_best[4]['trades']}")

    results.append({
        "coin": coin,
        "vol_breakout_best": {"pnl": vb_best[0], "wr": vb_best[4]["win_rate"], "trades": vb_best[4]["trades"]} if vb_best else None,
        "atr_trailing_best": {"pnl": at_best[0], "wr": at_best[4]["win_rate"], "trades": at_best[4]["trades"]} if at_best else None,
    })

# Summary
print(f"\n{'='*70}")
print(f"SUMMARY")
print(f"{'='*70}")
print(f"\n{'Coin':<15} {'Vol Breakout':>20} {'ATR Trailing':>20}")
print(f"{'':15} {'PnL/WR/Trades':>20} {'PnL/WR/Trades':>20}")
print("-" * 55)

for r in results:
    coin = r["coin"]
    vb = r["vol_breakout_best"]
    at = r["atr_trailing_best"]
    vb_str = f"${vb['pnl']:7.2f} {vb['wr']:4.1f}% {vb['trades']:4d}" if vb else "N/A"
    at_str = f"${at['pnl']:7.2f} {at['wr']:4.1f}% {at['trades']:4d}" if at else "N/A"
    print(f"{coin:<15} {vb_str:>20} {at_str:>20}")

# Save
report_path = os.path.join(os.path.dirname(__file__), "..", "reports", "vol_strategy_30d_validation.json")
os.makedirs(os.path.dirname(report_path), exist_ok=True)
with open(report_path, "w") as f:
    json.dump({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": results}, f, indent=2)
print(f"\nReport saved: {report_path}")
