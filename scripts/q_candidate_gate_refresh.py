#!/usr/bin/env python3
"""
Candidate Gate Refresh for codex-21.
Scans USD and BTC-quote pairs for validate-only candidates.
Filters: spread >= 100bps, depth >= $10 both sides, positive economics at 0.10.
"""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import (
    legal_volume, maker_exit_floor_price, exit_floor_above_ask_bps
)
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()
assets = c.asset_pairs()

# Batch tickers
all_tickers = {}
keys = [k for k in assets if assets[k].get('status') == 'online']
for i in range(0, len(keys), 100):
    try:
        all_tickers.update(c.ticker(keys[i:i+100]))
    except:
        pass

print(f"Scanning {len(keys)} online pairs...")
print()

candidates = []
skipped_no_ticker = 0
skipped_low_spread = 0
skipped_low_depth = 0
skipped_negative_econ = 0

for rest_pair, payload in assets.items():
    if payload.get('status') != 'online':
        continue
    p = parse_pair(rest_pair, payload)
    if not p or p.quote not in ('USD', 'XBT', 'BTC'):
        continue
    # Skip GLMR
    if 'GLMR' in p.wsname.upper():
        continue

    tk = all_tickers.get(rest_pair)
    if not tk or rest_pair not in tk:
        skipped_no_ticker += 1
        continue

    t = tk[rest_pair]
    bid = to_float((t.get('b') or [None])[0])
    ask = to_float((t.get('a') or [None])[0])
    if bid <= 0 or ask <= 0:
        continue

    spread = compute_spread_bps(bid, ask)
    if spread < 100:
        skipped_low_spread += 1
        continue

    # Depth check - handle key format variations
    d = c.depth(rest_pair, count=10)
    # Try different key formats
    book = d.get(rest_pair, {})
    if not book:
        # Try with slash format
        alt = rest_pair.replace('USD', '/USD').replace('XBT', '/XBT').replace('BTC', '/BTC').replace('ETH', '/ETH')
        book = d.get(alt, {})
    if not book:
        # If only one key, use that
        if len(d) == 1:
            book = list(d.values())[0]
    bids = book.get('bids', [])
    asks = book.get('asks', [])
    bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
    ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

    if bid_d < 10 or ask_d < 10:
        skipped_low_depth += 1
        continue

    # Economics at 0.10 offset
    entry = legal_maker_buy_price_at_offset(bid, ask, p.tick_size, 0.10)
    if entry <= 0:
        continue
    vol = legal_volume(9.0 / entry, p.lot_decimals)
    if vol <= 0:
        continue
    entry_cost = entry * vol
    entry_fee = entry_cost * 0.0025
    exit_legal, _ = maker_exit_floor_price(
        entry_cost=entry_cost, entry_fee=entry_fee, volume=vol,
        maker_fee_bps=25.0, target_net_pct=0.001, tick_size=p.tick_size
    )
    floor_above_ask = exit_floor_above_ask_bps(exit_legal, ask)
    gross = (exit_legal - entry) / entry * 10000
    net = gross - 50

    if net <= 0:
        skipped_negative_econ += 1
        continue

    candidates.append({
        'pair': p.wsname,
        'quote': p.quote,
        'spread_bps': round(spread, 1),
        'bid_depth_usd': round(bid_d, 1),
        'ask_depth_usd': round(ask_d, 1),
        'net_margin_bps': round(net, 1),
        'exit_floor_above_ask_bps': round(floor_above_ask, 1),
        'entry_concession_bps': round(max(0, (entry - bid) / bid * 10000), 1),
        'volume': round(vol, 6),
    })

candidates.sort(key=lambda x: x['net_margin_bps'], reverse=True)

print(f"Scan complete:")
print(f"  Skipped (no ticker): {skipped_no_ticker}")
print(f"  Skipped (spread < 100bps): {skipped_low_spread}")
print(f"  Skipped (depth < $10): {skipped_low_depth}")
print(f"  Skipped (negative economics): {skipped_negative_econ}")
print(f"  CANDIDATES: {len(candidates)}")
print()

if candidates:
    print(f"{'Pair':15s} {'Quote':>5s} {'Spread':>7s} {'BidDepth':>9s} {'AskDepth':>9s} {'NetMargin':>10s} {'ExitFloor':>10s}")
    print(f"{'-'*70}")
    for r in candidates[:30]:
        print(f"{r['pair']:15s} {r['quote']:>5s} {r['spread_bps']:7.0f}bps ${r['bid_depth_usd']:8.0f} ${r['ask_depth_usd']:8.0f} {r['net_margin_bps']:+9.1f}bps {r['exit_floor_above_ask_bps']:9.1f}bps")
else:
    print("NO CANDIDATES found matching all filters.")

# Save
out = {
    'generated': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),
    'filters': {'min_spread_bps': 100, 'min_depth_usd': 10, 'offset': 0.10},
    'skipped': {
        'no_ticker': skipped_no_ticker,
        'low_spread': skipped_low_spread,
        'low_depth': skipped_low_depth,
        'negative_econ': skipped_negative_econ,
    },
    'candidates': candidates,
}
with open('reports/candidate_gate_refresh.json', 'w') as f:
    json.dump(out, f, indent=2)

print(f"\nSaved to reports/candidate_gate_refresh.json")
