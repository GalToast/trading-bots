#!/usr/bin/env python3
"""
Kelly Position Tracker
======================
Real-time analysis of all active Kelly positions.
Checks current prices, projects TP hit probability, and identifies optimal actions.

Usage:
  python scripts/kelly_position_tracker.py
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Add scripts to path for Coinbase client
import sys
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_advanced_client import CoinbaseAdvancedClient

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    print("=" * 70)
    print("KELLY POSITION TRACKER")
    print("=" * 70)
    print(f"Timestamp: {utc_now_iso()}")
    print()

    # Load Kelly state
    state_path = Path(__file__).parent.parent / "reports" / "kelly_shadow_state.json"
    if not state_path.exists():
        print("ERROR: kelly_shadow_state.json not found")
        return 1

    with open(state_path) as f:
        state = json.load(f)

    print(f"Cycle: {state['cycle']}")
    print(f"Total Equity: ${state['total_equity']:.2f}")
    print(f"Return: {state['return_pct']:.2f}%")
    print()

    # Check active positions
    active_positions = []
    for coin, data in state['ledgers'].items():
        if data.get('position') == 'active':
            active_positions.append({
                'coin': coin,
                'entry': data['position_entry'],
                'tp': data['position_tp'],
                'hold': data['position_hold'],
                'max_hold': data['position_max_hold'],
                'strategy': data.get('strategy', 'unknown'),
                'equity': data.get('equity', 0),
                'closes': data.get('closes', 0),
            })

    if not active_positions:
        print("No active positions.")
        return 0

    print(f"Active Positions: {len(active_positions)}")
    print("-" * 70)

    client = CoinbaseAdvancedClient()

    for pos in active_positions:
        coin = pos['coin']
        entry = pos['entry']
        tp = pos['tp']
        hold = pos['hold']
        max_hold = pos['max_hold']
        strategy = pos['strategy']

        print(f"\n{coin} ({strategy}):")
        print(f"  Hold: {hold}/{max_hold} ({hold/max_hold*100:.1f}%)")
        print(f"  Entry: ${entry:.6f}, TP: ${tp:.6f}")

        # Get current price
        try:
            candles = list(client.market_candles(coin, granularity='FIVE_MINUTE', limit=20))
            if candles:
                recent = candles[-5:]
                closes = [float(c['close']) for c in recent]
                highs = [float(c['high']) for c in recent]
                lows = [float(c['low']) for c in recent]
                current = closes[-1]

                pct_from_entry = ((current - entry) / entry) * 100
                pct_to_tp = ((tp - current) / current) * 100
                high_since_entry = max(highs)
                pct_from_high = ((high_since_entry - entry) / entry) * 100

                print(f"  Current: ${current:.6f}")
                print(f"  From entry: {pct_from_entry:+.2f}%")
                print(f"  To TP: {pct_to_tp:+.2f}%")
                print(f"  High since entry: ${high_since_entry:.6f} ({pct_from_high:+.2f}%)")
                print(f"  Recent range: ${min(lows):.6f} - ${max(highs):.6f}")

                # Estimate bars remaining
                bars_remaining = max_hold - hold
                print(f"  Bars remaining: {bars_remaining}")

                # Project whether TP is reachable
                if pct_to_tp > 0:
                    # Need to move UP
                    recent_volatility = (max(highs) - min(lows)) / min(lows) * 100
                    bars_needed = pct_to_tp / recent_volatility if recent_volatility > 0 else 999
                    print(f"  Recent 5-bar volatility: {recent_volatility:.2f}%")
                    print(f"  Bars needed at current vol: ~{bars_needed:.0f}")
                    if bars_needed <= bars_remaining:
                        print(f"  ✅ TP is REACHABLE within remaining bars")
                    else:
                        print(f"  ⚠️ TP may NOT be reached - timeout likely")

            else:
                print(f"  No candle data available")
        except Exception as e:
            print(f"  Error fetching price: {str(e)[:80]}")

    print()
    print("=" * 70)
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
