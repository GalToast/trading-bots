#!/usr/bin/env python3
"""
HONEY-USD 1-Second Dislocation Tape for codex's dislocation lab.

Triggers ONLY when codex's gates are met:
- spread >= 150bps
- bid_depth_usd >= 15 AND ask_depth_usd >= 15
- ask_down >= 20bps OR bid_up >= 20bps

Output: reports/cache/honey_1s_dislocation_tape.json

Usage:
    python scripts/q_honey_1s_tape.py --duration 120 --trigger-spread 150 --trigger-depth 15 --trigger-move 20
"""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()
duration = 120  # 2 minutes
trigger_spread = 150
trigger_depth = 15
trigger_move = 20

print(f"HONEY-USD 1s tape: {duration}s, trigger: spread>={trigger_spread}bps depth>=${trigger_depth} move>={trigger_move}bps")
print()

ticks = []
triggered = []
prev_ask = None
prev_bid = None

for i in range(duration):
    try:
        tk = c.ticker(['HONEYUSD'])
        t = tk.get('HONEYUSD', {})
        bid = to_float((t.get('b') or [None])[0])
        ask = to_float((t.get('a') or [None])[0])
        last = to_float((t.get('c') or [None])[0])

        d = c.depth('HONEYUSD', count=10)
        book = d.get('HONEYUSD', d.get('HONEY/USD', {}))
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
        ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

        sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0

        # Calculate move
        ask_down = 0
        bid_up = 0
        if prev_ask and prev_ask > 0:
            ask_down = max(0, (prev_ask - ask) / prev_ask * 10000)
        if prev_bid and prev_bid > 0:
            bid_up = max(0, (bid - prev_bid) / prev_bid * 10000)

        tick = {
            'ts': utc_now(), 's': i,
            'bid': bid, 'ask': ask, 'last': last,
            'spread_bps': round(sp, 1),
            'bid_depth_usd': round(bid_d, 1), 'ask_depth_usd': round(ask_d, 1),
            'ask_down_bps': round(ask_down, 1), 'bid_up_bps': round(bid_up, 1),
        }
        ticks.append(tick)

        # Check trigger
        if sp >= trigger_spread and bid_d >= trigger_depth and ask_d >= trigger_depth:
            if ask_down >= trigger_move or bid_up >= trigger_move:
                triggered.append(tick)
                print(f"  🔥 TRIGGER t={i}s: spread={sp:.0f}bps bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f} ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")

        prev_ask = ask
        prev_bid = bid

        if i % 10 == 0:
            print(f"  t={i}s: spread={sp:.0f}bps bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f} ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")

        time.sleep(1)
    except Exception as e:
        print(f"  ERROR t={i}s: {e}")
        time.sleep(1)

# Save
out = {'generated': utc_now(), 'duration': duration, 'triggers': {'spread': trigger_spread, 'depth': trigger_depth, 'move': trigger_move}, 'total_ticks': len(ticks), 'triggered_count': len(triggered), 'ticks': ticks, 'triggered_events': triggered}
path = 'reports/cache/honey_1s_dislocation_tape.json'
with open(path, 'w') as f:
    json.dump(out, f, indent=2)

print(f"\n✅ Saved {len(ticks)} ticks, {len(triggered)} triggers to {path}")
