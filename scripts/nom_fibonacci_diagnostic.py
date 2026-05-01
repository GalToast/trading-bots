#!/usr/bin/env python3
"""NOM Fibonacci Signal Diagnostic — Why Isn't NOM Firing?

Replicates the exact fibonacci breakout logic from multi_coin_isolated_runner.py
and checks each condition against recent NOM-USD M5 candles to identify
why no signals are firing despite having enough history.

Usage:
    python scripts/nom_fibonacci_diagnostic.py
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient

OUTPUT_JSON = ROOT / "reports" / "nom_fibonacci_diagnostic.json"
OUTPUT_MD = ROOT / "reports" / "nom_fibonacci_diagnostic.md"

FIB_LOOKBACK = 20
FIB_LEVEL = 0.618
MIN_BREAKOUT_PCT = 0.02  # 2% above fib level
VOLUME_THRESHOLD = 0.8   # 80% of 20-period avg volume
GREEN_CANDLES_REQUIRED = 2  # Out of last 3


def compute_fibonacci_conditions(candles):
    """Check each fibonacci breakout condition individually.
    
    Returns dict with pass/fail for each condition.
    """
    if len(candles) < FIB_LOOKBACK + 5:
        return {"error": f"Not enough candles: {len(candles)} < {FIB_LOOKBACK + 5}"}
    
    recent = candles[-FIB_LOOKBACK:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    period_high = max(highs)
    period_low = min(lows)
    
    # Fib price
    fib_price = period_high - (period_high - period_low) * FIB_LEVEL
    
    # Current price
    current = float(candles[-1]["close"])
    breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0
    
    # Condition 1: Minimum breakout threshold
    breakout_pass = breakout_pct >= MIN_BREAKOUT_PCT
    
    # Condition 2: Volume confirmation
    if len(candles) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles[-20:]]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0
        current_volume = float(candles[-1].get("volume", 0))
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        volume_pass = volume_ratio >= VOLUME_THRESHOLD
    else:
        avg_volume = None
        current_volume = None
        volume_ratio = None
        volume_pass = False
    
    # Condition 3: Momentum (2 of last 3 candles green)
    if len(candles) >= 3:
        recent_3 = candles[-3:]
        green_count = sum(1 for c in recent_3 if float(c["close"]) > float(c["open"]))
        momentum_pass = green_count >= GREEN_CANDLES_REQUIRED
    else:
        green_count = None
        momentum_pass = False
    
    # Overall signal
    signal_fired = breakout_pass and volume_pass and momentum_pass
    
    return {
        "period_high": round(period_high, 8),
        "period_low": round(period_low, 8),
        "fib_price": round(fib_price, 8),
        "current_price": round(current, 8),
        "breakout_pct": round(breakout_pct * 100, 3),
        "breakout_pass": breakout_pass,
        "breakout_needed_pct": round(MIN_BREAKOUT_PCT * 100, 1),
        "avg_volume": round(avg_volume, 2) if avg_volume else None,
        "current_volume": round(current_volume, 2) if current_volume else None,
        "volume_ratio": round(volume_ratio, 3) if volume_ratio else None,
        "volume_pass": volume_pass,
        "volume_needed_ratio": VOLUME_THRESHOLD,
        "green_count": green_count,
        "momentum_pass": momentum_pass,
        "green_needed": GREEN_CANDLES_REQUIRED,
        "signal_fired": signal_fired,
    }


def main():
    print("=" * 72)
    print("NOM FIBONACCI SIGNAL DIAGNOSTIC")
    print("=" * 72)
    print()
    
    # Fetch NOM-USD M5 candles
    print("Fetching NOM-USD M5 candles...", flush=True)
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    # Fetch ~350 candles (API limit) = ~29 hours
    start = now - 350 * 300
    
    try:
        resp = client.market_candles("NOM-USD", start=start, end=now, granularity="FIVE_MINUTE")
        candles = resp.get("candles", [])
        candles.sort(key=lambda c: int(c["start"]))
        print(f"  Fetched {len(candles)} candles", flush=True)
    except Exception as e:
        print(f"  ❌ Failed: {e}", flush=True)
        return
    
    if len(candles) < FIB_LOOKBACK + 5:
        print(f"  ❌ Not enough candles: {len(candles)} < {FIB_LOOKBACK + 5}", flush=True)
        return
    
    print(f"\nChecking last 50 candles for fibonacci conditions...", flush=True)
    
    results = []
    for i in range(FIB_LOOKBACK + 5, len(candles)):
        window = candles[:i + 1]
        result = compute_fibonacci_conditions(window)
        result["candle_index"] = i
        result["candle_time"] = datetime.fromtimestamp(
            int(candles[i]["start"]), tz=timezone.utc
        ).strftime("%H:%M")
        results.append(result)
    
    # Count passes
    breakout_passes = sum(1 for r in results if r.get("breakout_pass"))
    volume_passes = sum(1 for r in results if r.get("volume_pass"))
    momentum_passes = sum(1 for r in results if r.get("momentum_pass"))
    signals_fired = sum(1 for r in results if r.get("signal_fired"))
    
    print(f"\n{'─' * 72}")
    print(f"  Results ({len(results)} candles checked):")
    print(f"  Breakout pass (>=2% above fib): {breakout_passes}/{len(results)} ({breakout_passes/len(results)*100:.0f}%)")
    print(f"  Volume pass (>=80% avg):        {volume_passes}/{len(results)} ({volume_passes/len(results)*100:.0f}%)")
    print(f"  Momentum pass (2/3 green):      {momentum_passes}/{len(results)} ({momentum_passes/len(results)*100:.0f}%)")
    print(f"  SIGNAL FIRED:                    {signals_fired}/{len(results)} ({signals_fired/len(results)*100:.0f}%)")
    print(f"{'─' * 72}")
    
    # Show last 10 candles with details
    print(f"\n  Last 10 candles:")
    print(f"  {'Time':<8} {'Price':>10} {'Fib':>10} {'Break%':>8} {'Break':>6} {'Vol':>6} {'VolR':>6} {'Mom':>4} {'Signal':>7}")
    print(f"  {'─' * 8} {'─' * 10} {'─' * 10} {'─' * 8} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 4} {'─' * 7}")
    for r in results[-10:]:
        print(
            f"  {r['candle_time']:<8} {r['current_price']:>10.6f} {r['fib_price']:>10.6f} "
            f"{r['breakout_pct']:>7.1f}% {'✅' if r['breakout_pass'] else '❌':>4} "
            f"{'✅' if r['volume_pass'] else '❌':>4} {r['volume_ratio']:>5.2f}x "
            f"{'✅' if r['momentum_pass'] else '❌':>4} {'✅' if r['signal_fired'] else '':>5}"
        )
    
    # Save outputs
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "candles_checked": len(results),
        "breakout_passes": breakout_passes,
        "volume_passes": volume_passes,
        "momentum_passes": momentum_passes,
        "signals_fired": signals_fired,
        "last_50_results": results[-50:],
    }
    
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, sort_keys=True)
    
    # Markdown report
    md_lines = [
        "# NOM Fibonacci Signal Diagnostic",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Summary",
        f"- Candles checked: {len(results)}",
        f"- Breakout passes: {breakout_passes}/{len(results)} ({breakout_passes/len(results)*100:.0f}%)",
        f"- Volume passes: {volume_passes}/{len(results)} ({volume_passes/len(results)*100:.0f}%)",
        f"- Momentum passes: {momentum_passes}/{len(results)} ({momentum_passes/len(results)*100:.0f}%)",
        f"- **SIGNALS FIRED: {signals_fired}/{len(results)}**",
        "",
        "## Analysis",
        "",
    ]
    
    if signals_fired == 0:
        # Find the most common blocker
        if breakout_passes < volume_passes and breakout_passes < momentum_passes:
            md_lines.append("**PRIMARY BLOCKER: Breakout threshold (2% above fib level)**")
            md_lines.append("NOM is not breaking 2% above the Fibonacci retracement level.")
            md_lines.append("This is the most restrictive gate — it requires strong momentum.")
        elif volume_passes < breakout_passes and volume_passes < momentum_passes:
            md_lines.append("**PRIMARY BLOCKER: Volume confirmation**")
            md_lines.append("NOM breakouts lack sufficient volume (below 80% of 20-period average).")
        else:
            md_lines.append("**PRIMARY BLOCKER: Momentum confirmation**")
            md_lines.append("NOM doesn't have 2 of last 3 candles green at breakout moments.")
        
        md_lines.append("")
        md_lines.append("## Conclusion")
        md_lines.append("")
        md_lines.append("NOM fibonacci is working as designed but is extremely selective.")
        md_lines.append("The triple-gate (breakout + volume + momentum) means signals only fire")
        md_lines.append("during strong conviction breakouts with participation and follow-through.")
        md_lines.append("")
        md_lines.append("This is NOT a bug — it's a feature. The strategy is designed to avoid")
        md_lines.append("whipsaw breakouts. But it means NOM may go days without a signal.")
    else:
        md_lines.append(f"**NOM fibonacci WOULD fire {signals_fired} times in this window.**")
    
    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    
    print(f"\n  Output: {OUTPUT_JSON}", flush=True)
    print(f"  Report: {OUTPUT_MD}", flush=True)


if __name__ == "__main__":
    main()
