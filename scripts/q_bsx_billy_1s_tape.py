#!/usr/bin/env python3
"""1s dislocation tape for BSX and BILLY - feeds codex's replay."""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps

def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()
duration = 120
trigger_spread = 150
trigger_depth = 15
trigger_move = 20

for target_pair in ['BSXUSD', 'BILLYUSD']:
    print(f"\n{'='*60}")
    print(f"Scanning {target_pair}: {duration}s, 1s interval")
    print(f"{'='*60}")

    ticks = []
    triggered = []
    prev_ask = None
    prev_bid = None

    for i in range(duration):
        try:
            tk = c.ticker([target_pair])
            t = tk.get(target_pair, {})
            bid = to_float((t.get('b') or [None])[0])
            ask = to_float((t.get('a') or [None])[0])
            last = to_float((t.get('c') or [None])[0])

            d = c.depth(target_pair, count=10)
            book = d.get(target_pair, {})
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
            ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

            sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0

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

    out = {
        'generated': utc_now(), 'product': target_pair, 'duration': duration,
        'triggers': {'spread': trigger_spread, 'depth': trigger_depth, 'move': trigger_move},
        'total_ticks': len(ticks), 'triggered_count': len(triggered),
        'ticks': ticks, 'triggered_events': triggered
    }
    path = f'reports/cache/{target_pair.lower()}_1s_dislocation_tape.json'
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)

    print(f"\n✅ {target_pair}: {len(ticks)} ticks, {len(triggered)} triggers -> {path}")
