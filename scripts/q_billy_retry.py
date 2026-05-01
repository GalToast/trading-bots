import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()

for target_pair in ['BILLYUSD']:
    print(f"Scanning {target_pair}: 120s, 1s interval")
    ticks = []
    triggered_strict = []
    triggered_loose = []
    prev_ask = None
    prev_bid = None

    for i in range(120):
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

            # Strict: depth >= $15 both sides
            if sp >= 150 and bid_d >= 15 and ask_d >= 15 and (ask_down >= 20 or bid_up >= 20):
                triggered_strict.append(tick)
                print(f"  🔥 STRICT t={i}s: spread={sp:.0f}bps bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f} ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")

            # Loose: depth >= $5 (for BSX comparison)
            if sp >= 150 and bid_d >= 5 and ask_d >= 5 and (ask_down >= 20 or bid_up >= 20):
                triggered_loose.append(tick)

            prev_ask = ask
            prev_bid = bid

            if i % 10 == 0:
                print(f"  t={i}s: spread={sp:.0f}bps bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f} ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")

            time.sleep(1)
        except Exception as e:
            print(f"  ERROR t={i}s: {e}")
            time.sleep(1)

    out = {
        'generated': utc_now(), 'product': target_pair, 'duration': 120,
        'triggers': {'spread': 150, 'depth': 15, 'move': 20},
        'total_ticks': len(ticks),
        'triggered_count_strict': len(triggered_strict),
        'triggered_count_loose_depth5': len(triggered_loose),
        'ticks': ticks,
        'triggered_events_strict': triggered_strict,
        'triggered_events_loose': triggered_loose
    }
    path = f'reports/cache/{target_pair.lower()}_1s_dislocation_tape.json'
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)

    print(f"\n✅ {target_pair}: {len(ticks)} ticks, {len(triggered_strict)} strict triggers, {len(triggered_loose)} loose (depth>=$5)")
