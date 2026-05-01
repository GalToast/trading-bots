#!/usr/bin/env python3
"""
Signal Probability Estimator — Checks current market conditions to predict
which strategies are likely to fire signals soon.

Answers: "If I deploy now, how long until I get my first signal/close?"

Usage:
    python scripts/signal_probability_estimator.py
    python scripts/signal_probability_estimator.py --coins NOM-USD GHST-USD RAVE-USD
"""
import sys
import json
import statistics
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

COINS_TO_CHECK = [
    "NOM-USD", "GHST-USD", "RAVE-USD", "TRU-USD", "SUP-USD",
    "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD"
]

STRATEGIES = {
    "fibonacci": {"lookback": 20, "fib_level": 0.618, "desc": "Fibonacci breakout"},
    "supertrend": {"atr_period": 10, "atr_mult": 3.0, "desc": "Supertrend trend-follow"},
    "momentum_10": {"lookback": 10, "desc": "Momentum 10-bar"},
    "momentum_20": {"lookback": 20, "desc": "Momentum 20-bar"},
    "momentum_50": {"lookback": 50, "desc": "Momentum 50-bar"},
}


def check_fibonacci_signal(candles):
    """Check if fibonacci breakout condition is met RIGHT NOW."""
    if len(candles) < 25:
        return False, 0
    
    lookback = 20
    fib_level = 0.618
    
    recent = candles[-lookback:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    period_high = max(highs)
    period_low = min(lows)
    fib_price = period_high - (period_high - period_low) * fib_level
    
    current = float(candles[-1]["close"])
    distance_pct = (current - fib_price) / fib_price * 100
    
    return current > fib_price, distance_pct


def check_supertrend_signal(candles):
    """Check if supertrend condition is met RIGHT NOW."""
    if len(candles) < 15:
        return False, 0
    
    atr_period = 10
    atr_mult = 3.0
    
    # Calculate ATR
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        cp = candles[i-1]
        tr = max(
            float(c["high"]) - float(c["low"]),
            abs(float(c["high"]) - float(cp["close"])),
            abs(float(c["low"]) - float(cp["close"]))
        )
        trs.append(tr)
    
    if len(trs) < atr_period:
        return False, 0
    
    atr = sum(trs[-atr_period:]) / atr_period
    hl2 = (float(candles[-1]["high"]) + float(candles[-1]["low"])) / 2
    lower = hl2 - atr_mult * atr
    
    current = float(candles[-1]["close"])
    distance_pct = (current - lower) / lower * 100
    
    return current > lower, distance_pct


def check_momentum_signal(candles, lookback):
    """Check if momentum breakout condition is met RIGHT NOW."""
    if len(candles) < lookback + 2:
        return False, 0
    
    recent = candles[-(lookback+1):-1]
    recent_high = max(float(c["high"]) for c in recent)
    current = float(candles[-1]["close"])
    
    distance_pct = (current - recent_high) / recent_high * 100
    
    return current > recent_high, distance_pct


def estimate_signal_frequency(candles, strategy_name, strategy_params):
    """Estimate how often this strategy fires signals based on recent history."""
    # Count signals in last 100 candles
    signals = 0
    closes = [float(c["close"]) for c in candles]
    
    if strategy_name == "fibonacci":
        lookback = strategy_params.get("lookback", 20)
        fib_level = strategy_params.get("fib_level", 0.618)
        for i in range(lookback + 5, len(candles)):
            recent = candles[i-lookback:i]
            highs = [float(c["high"]) for c in recent]
            lows = [float(c["low"]) for c in recent]
            period_high = max(highs)
            period_low = min(lows)
            fib_price = period_high - (period_high - period_low) * fib_level
            if closes[i] > fib_price:
                signals += 1
    
    elif strategy_name == "supertrend":
        atr_period = strategy_params.get("atr_period", 10)
        atr_mult = strategy_params.get("atr_mult", 3.0)
        for i in range(atr_period + 5, len(candles)):
            trs = []
            for j in range(max(1, i-atr_period), i):
                c = candles[j]
                cp = candles[j-1]
                tr = max(float(c["high"]) - float(c["low"]),
                        abs(float(c["high"]) - float(cp["close"])),
                        abs(float(c["low"]) - float(cp["close"])))
                trs.append(tr)
            if len(trs) < atr_period:
                continue
            atr = sum(trs[-atr_period:]) / atr_period
            hl2 = (float(candles[i]["high"]) + float(candles[i]["low"])) / 2
            lower = hl2 - atr_mult * atr
            if closes[i] > lower:
                signals += 1
    
    elif "momentum" in strategy_name:
        lookback = strategy_params.get("lookback", 10)
        for i in range(lookback + 5, len(candles)):
            recent = candles[i-lookback-1:i-1]
            recent_high = max(float(c["high"]) for c in recent)
            if closes[i] > recent_high:
                signals += 1
    
    return signals


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", nargs="+", default=None)
    args = parser.parse_args()
    
    coins = args.coins if args.coins else COINS_TO_CHECK
    
    print("=" * 80)
    print("  SIGNAL PROBABILITY ESTIMATOR")
    print("=" * 80)
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Checking {len(coins)} coins x {len(STRATEGIES)} strategies")
    print()
    
    results = {}
    
    for coin in coins:
        print(f"--- {coin} ---")
        try:
            candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=7*24*60)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        
        if not candles or len(candles) < 50:
            print(f"  Insufficient data: {len(candles) if candles else 0} candles")
            continue
        
        print(f"  Loaded {len(candles)} candles")
        
        coin_results = {}
        
        # Check current signal conditions
        fib_active, fib_dist = check_fibonacci_signal(candles)
        st_active, st_dist = check_supertrend_signal(candles)
        mom10_active, mom10_dist = check_momentum_signal(candles, 10)
        mom20_active, mom20_dist = check_momentum_signal(candles, 20)
        mom50_active, mom50_dist = check_momentum_signal(candles, 50)
        
        print(f"\n  Current signal conditions:")
        print(f"    Fibonacci:   {'🟢 ACTIVE' if fib_active else '⚪ FLAT'} (distance: {fib_dist:+.2f}%)")
        print(f"    Supertrend:  {'🟢 ACTIVE' if st_active else '⚪ FLAT'} (distance: {st_dist:+.2f}%)")
        print(f"    Momentum 10: {'🟢 ACTIVE' if mom10_active else '⚪ FLAT'} (distance: {mom10_dist:+.2f}%)")
        print(f"    Momentum 20: {'🟢 ACTIVE' if mom20_active else '⚪ FLAT'} (distance: {mom20_dist:+.2f}%)")
        print(f"    Momentum 50: {'🟢 ACTIVE' if mom50_active else '⚪ FLAT'} (distance: {mom50_dist:+.2f}%)")
        
        # Estimate signal frequency
        print(f"\n  Signal frequency (last 100 candles):")
        fib_freq = estimate_signal_frequency(candles[-100:], "fibonacci", {"lookback": 20})
        st_freq = estimate_signal_frequency(candles[-100:], "supertrend", {"atr_period": 10, "atr_mult": 3.0})
        mom10_freq = estimate_signal_frequency(candles[-100:], "momentum_10", {"lookback": 10})
        mom20_freq = estimate_signal_frequency(candles[-100:], "momentum_20", {"lookback": 20})
        mom50_freq = estimate_signal_frequency(candles[-100:], "momentum_50", {"lookback": 50})
        
        print(f"    Fibonacci:   {fib_freq} signals/100 bars (~{fib_freq*3} signals/day)")
        print(f"    Supertrend:  {st_freq} signals/100 bars (~{st_freq*3} signals/day)")
        print(f"    Momentum 10: {mom10_freq} signals/100 bars (~{mom10_freq*3} signals/day)")
        print(f"    Momentum 20: {mom20_freq} signals/100 bars (~{mom20_freq*3} signals/day)")
        print(f"    Momentum 50: {mom50_freq} signals/100 bars (~{mom50_freq*3} signals/day)")
        
        # Time to next signal estimate
        print(f"\n  Estimated time to next signal:")
        if fib_active:
            print(f"    Fibonacci: 🟢 SIGNALING NOW")
        elif fib_freq > 0:
            bars_to_signal = 100 / fib_freq
            print(f"    Fibonacci: ~{bars_to_signal:.0f} bars (~{bars_to_signal*5/60:.1f} hours)")
        else:
            print(f"    Fibonacci: ❌ No recent signals")
        
        if st_active:
            print(f"    Supertrend: 🟢 SIGNALING NOW")
        elif st_freq > 0:
            bars_to_signal = 100 / st_freq
            print(f"    Supertrend: ~{bars_to_signal:.0f} bars (~{bars_to_signal*5/60:.1f} hours)")
        else:
            print(f"    Supertrend: ❌ No recent signals")
        
        if mom10_active:
            print(f"    Momentum 10: 🟢 SIGNALING NOW")
        elif mom10_freq > 0:
            bars_to_signal = 100 / mom10_freq
            print(f"    Momentum 10: ~{bars_to_signal:.0f} bars (~{bars_to_signal*5/60:.1f} hours)")
        else:
            print(f"    Momentum 10: ❌ No recent signals")
        
        coin_results = {
            "fibonacci": {"active": fib_active, "distance_pct": fib_dist, "freq_per_100": fib_freq},
            "supertrend": {"active": st_active, "distance_pct": st_dist, "freq_per_100": st_freq},
            "momentum_10": {"active": mom10_active, "distance_pct": mom10_dist, "freq_per_100": mom10_freq},
            "momentum_20": {"active": mom20_active, "distance_pct": mom20_dist, "freq_per_100": mom20_freq},
            "momentum_50": {"active": mom50_active, "distance_pct": mom50_dist, "freq_per_100": mom50_freq},
        }
        results[coin] = coin_results
        
        print()
    
    # Summary table
    print(f"{'='*80}")
    print(f"  DEPLOYMENT RECOMMENDATION")
    print(f"{'='*80}")
    
    active_signals = []
    for coin, coin_results in results.items():
        for strat, info in coin_results.items():
            if info["active"]:
                active_signals.append((coin, strat))
    
    if active_signals:
        print(f"\n  🟢 {len(active_signals)} strategies are SIGNALING RIGHT NOW:")
        for coin, strat in active_signals:
            print(f"    {coin} — {strat}")
        print(f"\n  ✅ GOOD TIME TO DEPLOY — signals are firing now")
    else:
        print(f"\n  ⚪ No strategies currently signaling")
        print(f"  Market is quiet — signals may take hours to fire")
        print(f"  Consider waiting or using momentum (highest frequency)")
    
    # Find best coin to deploy for quick signals
    best_coin = None
    best_freq = 0
    for coin, coin_results in results.items():
        total_freq = sum(info["freq_per_100"] for info in coin_results.values())
        if total_freq > best_freq:
            best_freq = total_freq
            best_coin = coin
    
    if best_coin:
        print(f"\n  🏆 Highest signal frequency: {best_coin} ({best_freq:.0f} signals/100 bars)")
        print(f"  Expected first signal in ~{100/best_freq:.0f} bars (~{100/best_freq*5/60:.1f} hours)")
    
    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coins_checked": len(results),
        "active_signals": active_signals,
        "results": results,
        "recommendation": "deploy_now" if active_signals else "wait_or_use_momentum",
        "best_coin_for_signals": best_coin,
    }
    
    output_path = Path("reports/signal_probability_estimate.json")
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report saved: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
