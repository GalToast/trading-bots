#!/usr/bin/env python3
"""Quick economics check for all USD pairs at low offsets."""
import sys; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import exit_floor_above_ask_bps, maker_exit_floor_price, legal_volume
from crossing_pressure_scanner import compute_spread_bps
import json

c = KrakenSpotClient()
assets = c.asset_pairs()

# Batch tickers
ticker_pairs = [k for k in assets if assets[k].get('status') == 'online']
all_tickers = {}
for i in range(0, len(ticker_pairs), 100):
    batch = ticker_pairs[i:i+100]
    try:
        t = c.ticker(batch)
        all_tickers.update(t)
    except:
        pass

results = []
for rest_pair, payload in assets.items():
    if payload.get('status') != 'online':
        continue
    p = parse_pair(rest_pair, payload)
    if not p or p.quote != 'USD':
        continue
    tk = all_tickers.get(rest_pair)
    if not tk:
        continue
    bid = to_float((tk.get('b') or [None])[0])
    ask = to_float((tk.get('a') or [None])[0])
    if bid <= 0 or ask <= 0:
        continue
    spread = compute_spread_bps(bid, ask)

    for offset in [0.10, 0.25]:
        entry = legal_maker_buy_price_at_offset(bid, ask, p.tick_size, offset)
        if entry <= 0:
            continue
        vol = legal_volume(9.0 / entry, p.lot_decimals)
        if vol <= 0:
            continue
        entry_cost = entry * vol
        entry_fee = entry_cost * 0.0025
        exit_legal, exit_raw = maker_exit_floor_price(
            entry_cost=entry_cost, entry_fee=entry_fee,
            volume=vol, maker_fee_bps=25.0, target_net_pct=0.001,
            tick_size=p.tick_size
        )
        floor_above_ask = exit_floor_above_ask_bps(exit_legal, ask)
        entry_concession = max(0, (entry - bid) / bid * 10000)
        gross_capture = (exit_legal - entry) / entry * 10000
        net_margin = gross_capture - 50

        results.append({
            'pair': p.wsname, 'spread': round(spread, 0),
            'offset': offset, 'entry_concession': round(entry_concession, 1),
            'floor_above_ask': round(floor_above_ask, 1),
            'net_margin': round(net_margin, 1),
            'vol': round(vol, 6)
        })

# Sort and output
results.sort(key=lambda x: (x['net_margin'], x['spread']), reverse=True)
with open('reports/economics_check.json', 'w') as f:
    json.dump({'total': len(results), 'top_by_net': results[:20], 'profitable': [r for r in results if r['net_margin'] > 0][:10]}, f, indent=2)

# Also print
print(f"Total combos: {len(results)}")
profitable = [r for r in results if r['net_margin'] > 0]
print(f"Positive net margin: {len(profitable)}")
print("\nTop 15 by net margin:")
for r in results[:15]:
    print(f"  {r['pair']:15s} spread={r['spread']:5.0f}bps offset={r['offset']:.2f} concession={r['entry_concession']:6.1f}bps floor_ask={r['floor_above_ask']:6.1f}bps net={r['net_margin']:+6.1f}bps")
if profitable:
    print(f"\nAll profitable pairs:")
    for r in sorted(profitable, key=lambda x: x['net_margin'], reverse=True):
        print(f"  {r['pair']:15s} spread={r['spread']:5.0f}bps offset={r['offset']:.2f} net={r['net_margin']:+6.1f}bps")
