#!/usr/bin/env python3
"""
NOM Signal Condition Auditor
==============================
Replays the last N candles through the fibonacci signal logic and reports
exactly which conditions are passing/failing at each step.

This diagnoses why NOM-USD (Kelly shadow) hasn't fired despite 32+ candles.

Usage:
    python scripts/nom_signal_auditor.py --candles 50
    python scripts/nom_signal_auditor.py --candles 100
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import fetch_candles


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def audit_fib_signal(candles, fib_lookback=20, fib_level=0.618, min_breakout_pct=0.02):
    """Audit the fibonacci signal conditions for a series of candles."""
    results = []
    
    for i in range(fib_lookback + 5, len(candles)):
        window = candles[:i+1]
        recent = window[-fib_lookback:]
        
        highs = [float(c["high"]) for c in recent]
        lows = [float(c["low"]) for c in recent]
        period_high = max(highs)
        period_low = min(lows)
        
        fib_price = period_high - (period_high - period_low) * fib_level
        current = float(window[-1]["close"])
        breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0
        
        # Volume check
        volumes = [float(c.get("volume", 0)) for c in window[-20:]]
        avg_volume = sum(volumes) / len(volumes) if volumes and any(v > 0 for v in volumes) else 0
        current_volume = float(window[-1].get("volume", 0))
        volume_ok = (avg_volume > 0 and current_volume >= avg_volume * 0.8) if avg_volume > 0 else True
        
        # Momentum check
        recent_3 = window[-3:]
        green_count = sum(1 for c in recent_3 if float(c["close"]) > float(c["open"]))
        momentum_ok = green_count >= 2
        
        # Breakout threshold
        breakout_ok = breakout_pct >= min_breakout_pct
        
        # Overall signal
        signal = breakout_ok and volume_ok and momentum_ok
        
        results.append({
            "candle_idx": i,
            "timestamp": window[-1].get("start", ""),
            "current_price": round(current, 6),
            "fib_price": round(fib_price, 6),
            "breakout_pct": round(breakout_pct * 100, 3),
            "breakout_ok": breakout_ok,
            "volume_ok": volume_ok,
            "momentum_ok": momentum_ok,
            "green_count": green_count,
            "signal": signal,
        })
    
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NOM Signal Condition Auditor")
    parser.add_argument("--candles", type=int, default=50, help="Number of candles to fetch")
    args = parser.parse_args()
    
    print("=" * 72)
    print("NOM-USD FIBONACCI SIGNAL CONDITION AUDITOR")
    print("=" * 72)
    print()
    
    # Fetch NOM candles (1-minute for higher resolution)
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - args.candles * 60  # 1-minute candles
    
    try:
        candles = fetch_candles(client, "NOM-USD", start, now, granularity="ONE_MINUTE")
    except Exception as e:
        print(f"Error with 1-minute candles: {e}")
        print("Trying with 5-minute granularity instead...")
        start = now - args.candles * 300
        candles = fetch_candles(client, "NOM-USD", start, now, granularity="FIVE_MINUTE")
    
    if not candles:
        print("No candles received.")
        return
    
    print(f"Fetched {len(candles)} candles for NOM-USD")
    print(f"Time range: {candles[0].get('start', '?')} to {candles[-1].get('start', '?')}")
    print()
    
    # Audit fib signal conditions
    results = audit_fib_signal(candles)
    
    if not results:
        print(f"Need at least {20 + 5} candles, only have {len(candles)}")
        return
    
    # Summary
    signals = [r for r in results if r["signal"]]
    breakout_failures = [r for r in results if not r["breakout_ok"]]
    volume_failures = [r for r in results if not r["volume_ok"]]
    momentum_failures = [r for r in results if not r["momentum_ok"]]
    
    print(f"Analyzed {len(results)} candle positions:")
    print(f"  Signals that would have fired: {len(signals)}")
    print(f"  Failed breakout threshold: {len(breakout_failures)} ({len(breakout_failures)/len(results)*100:.0f}%)")
    print(f"  Failed volume check: {len(volume_failures)} ({len(volume_failures)/len(results)*100:.0f}%)")
    print(f"  Failed momentum check: {len(momentum_failures)} ({len(momentum_failures)/len(results)*100:.0f}%)")
    print()
    
    if signals:
        print("✅ SIGNAL CANDIDATES (all conditions met):")
        for s in signals[-5:]:  # Last 5
            print(f"  {s['timestamp']}: price={s['current_price']:.6f}, fib={s['fib_price']:.6f}, "
                  f"breakout={s['breakout_pct']:.2f}%")
        print()
    
    # Show most recent candle conditions
    latest = results[-1]
    print(f"Most recent candle ({latest['timestamp']}):")
    print(f"  Price: {latest['current_price']:.6f}")
    print(f"  Fib level: {latest['fib_price']:.6f}")
    print(f"  Breakout: {latest['breakout_pct']:.2f}% (need >= 2.0%) {'✅' if latest['breakout_ok'] else '❌'}")
    print(f"  Volume: {'✅ OK' if latest['volume_ok'] else '❌ BELOW 80% avg'}")
    print(f"  Momentum: {latest['green_count']}/3 green (need >= 2) {'✅' if latest['momentum_ok'] else '❌'}")
    print(f"  Signal: {'✅ WOULD FIRE' if latest['signal'] else '❌ WOULD NOT FIRE'}")
    print()
    
    # Save results
    output = {
        "coin": "NOM-USD",
        "strategy": "fibonacci",
        "fib_lookback": 20,
        "fib_level": 0.618,
        "min_breakout_pct": 0.02,
        "total_positions": len(results),
        "signals": len(signals),
        "failure_rates": {
            "breakout": len(breakout_failures) / len(results) * 100,
            "volume": len(volume_failures) / len(results) * 100,
            "momentum": len(momentum_failures) / len(results) * 100,
        },
        "latest_conditions": latest,
    }
    
    output_path = ROOT / "reports" / "nom_signal_audit.json"
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Audit saved to: {output_path}")


if __name__ == "__main__":
    import time
    main()
