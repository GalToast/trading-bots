#!/usr/bin/env python3
"""
HONEY Fill Proxy On Trigger Test.

Replays the 1s HONEY tape and tests fill proxy at each trigger event.
For each trigger, checks if a post-only BUY at 0.10 offset would have filled
based on the book movement in the NEXT 10 seconds.

Usage:
    python scripts/q_honey_fill_proxy_on_trigger.py
"""
import sys, json; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()

# Load the tape
tape = json.load(open('reports/cache/honey_1s_dislocation_tape.json'))
ticks = tape['ticks']
triggers = tape['triggered_events']

print(f"HONEY fill proxy test: {len(triggers)} trigger events across {len(ticks)} ticks")
print()

# Get pair info for tick_size
assets = c.asset_pairs()
pair_info = None
for k, v in assets.items():
    if v.get('altname','').upper() == 'HONEYUSD':
        from kraken_spot_client import parse_pair
        pair_info = parse_pair(k, v)
        break

if not pair_info:
    # Fallback: try common tick sizes for low-price tokens
    class FakePair:
        def __init__(self):
            self.tick_size = 0.00001
            self.lot_decimals = 6
    pair_info = FakePair()
    print("Warning: using fallback pair info (tick_size=0.00001, lot_decimals=6)")
else:
    print(f"Pair info: {pair_info.wsname} tick_size={pair_info.tick_size} lot_decimals={pair_info.lot_decimals}")
print()

fill_proxies = 0
total_fill_trials = 0

for trig in triggers:
    s = trig['s']
    # Find the tick at this second
    tick = ticks[s]
    bid = tick['bid']
    ask = tick['ask']
    spread = tick['spread_bps']

    if bid <= 0 or ask <= 0:
        continue

    # Compute entry price at 0.10 offset
    entry = legal_maker_buy_price_at_offset(bid, ask, pair_info.tick_size, 0.10)
    if entry <= 0:
        continue

    total_fill_trials += 1

    # Check next 10 ticks for fill proxy
    # A BUY fill proxy happens if the ask moves DOWN to or below our entry price
    # OR if a trade happens at or below our entry price
    fill_like = False
    fill_second = None
    min_future_ask = ask
    max_future_bid = bid

    for j in range(s+1, min(s+11, len(ticks))):
        future = ticks[j]
        f_bid = future['bid']
        f_ask = future['ask']
        f_last = future['last']
        if f_bid > 0:
            max_future_bid = max(max_future_bid, f_bid)
        if f_ask > 0:
            min_future_ask = min(min_future_ask, f_ask)

        # Hard cross: ask drops below our entry price
        if f_ask > 0 and f_ask <= entry:
            fill_like = True
            fill_second = j
            break
        # Trade at or below entry
        if f_last > 0 and f_last <= entry:
            fill_like = True
            fill_second = j
            break
        # Ask crossed below bid (spread collapse)
        if f_bid > 0 and f_ask > 0 and f_ask <= f_bid:
            fill_like = True
            fill_second = j
            break

    if fill_like:
        fill_proxies += 1
        fill_rate = fill_proxies / total_fill_trials
        print(f"  FILL t={s}s: entry={entry:.8f} bid={bid:.8f} ask={ask:.8f} spread={spread:.0f}bps -> filled at t={fill_second}s (rate={fill_rate:.0%})")
    else:
        fill_rate = fill_proxies / total_fill_trials
        if total_fill_trials <= 5 or total_fill_trials % 10 == 0:
            print(f"  NO FILL t={s}s: entry={entry:.8f} bid={bid:.8f} ask={ask:.8f} spread={spread:.0f}bps min_future_ask={min_future_ask:.8f} (rate={fill_rate:.0%})")

print()
print(f"RESULTS: {fill_proxies}/{total_fill_trials} fill-like = {fill_proxies/total_fill_trials:.0%} fill rate")
