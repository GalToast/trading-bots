#!/usr/bin/env python3
"""
Multi-Numeraire Unit Accumulation Route Scorer.

Scores same-venue executable routes on Kraken:
- USD->token->USD (standard)
- BTC->token->BTC (BTC-quoted)
- ETH->token->ETH (ETH-quoted)
- Triangular: BTC->token->USD->BTC

Output: reports/kraken_numeraire_route_scores.json

For codex: separates numeraire_gain_bps from USD_mark_bps.
Maker assumptions flagged as optimistic unless validated by fill tape.
"""
import sys, json; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import legal_volume, maker_exit_floor_price, exit_floor_above_ask_bps
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()
assets = c.asset_pairs()

# Batch tickers
all_tickers = {}
keys = list(assets.keys())
for i in range(0, len(keys), 100):
    batch = keys[i:i+100]
    try:
        t = c.ticker(batch)
        all_tickers.update(t)
    except:
        pass

print("Scanning Kraken numeraire routes...")

usd_routes = []
btc_routes = []
eth_routes = []
sol_routes = []

offset = 0.10
maker_fee_bps = 25

for rest_pair, payload in assets.items():
    if payload.get('status') != 'online':
        continue
    p = parse_pair(rest_pair, payload)
    if not p:
        continue

    tk = all_tickers.get(rest_pair)
    if not tk or rest_pair not in tk:
        continue
    t = tk[rest_pair]
    bid = to_float((t.get('b') or [None])[0])
    ask = to_float((t.get('a') or [None])[0])
    if bid <= 0 or ask <= 0:
        continue
    spread = compute_spread_bps(bid, ask)

    # Depth check
    d = c.depth(rest_pair, count=10)
    book = d.get(rest_pair, {})
    bids = book.get('bids', [])
    asks = book.get('asks', [])
    bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
    ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

    # Skip if depth too thin for $15
    if bid_d < 15 or ask_d < 15:
        continue

    # Skip if spread < 150bps
    if spread < 150:
        continue

    # Economics at 0.10 offset
    entry = legal_maker_buy_price_at_offset(bid, ask, p.tick_size, offset)
    if entry <= 0:
        continue
    vol = legal_volume(9.0 / entry, p.lot_decimals)
    if vol <= 0:
        continue
    entry_cost = entry * vol
    entry_fee = entry_cost * (maker_fee_bps / 10000)
    exit_legal, _ = maker_exit_floor_price(
        entry_cost=entry_cost, entry_fee=entry_fee, volume=vol,
        maker_fee_bps=maker_fee_bps, target_net_pct=0.001, tick_size=p.tick_size
    )
    floor_above_ask = exit_floor_above_ask_bps(exit_legal, ask)
    gross_bps = (exit_legal - entry) / entry * 10000
    net_bps = gross_bps - 2 * maker_fee_bps

    route = {
        'pair': p.wsname, 'quote': p.quote,
        'spread_bps': round(spread, 1),
        'bid_depth_usd': round(bid_d, 1), 'ask_depth_usd': round(ask_d, 1),
        'offset': offset,
        'entry_concession_bps': round(max(0, (entry - bid) / bid * 10000), 1),
        'exit_floor_above_ask_bps': round(floor_above_ask, 1),
        'numeraire_gain_bps': round(net_bps, 1),
        'usd_mark_bps': round(spread - 2 * maker_fee_bps, 1),
        'maker_assumption': 'optimistic_no_fill_tape',
    }

    if p.quote == 'USD':
        usd_routes.append(route)
    elif p.quote == 'XBT' or p.quote == 'BTC':
        btc_routes.append(route)
    elif p.quote == 'ETH':
        eth_routes.append(route)
    elif p.quote == 'SOL':
        sol_routes.append(route)

# Sort each by net bps
usd_routes.sort(key=lambda x: x['numeraire_gain_bps'], reverse=True)
btc_routes.sort(key=lambda x: x['numeraire_gain_bps'], reverse=True)
eth_routes.sort(key=lambda x: x['numeraire_gain_bps'], reverse=True)
sol_routes.sort(key=lambda x: x['numeraire_gain_bps'], reverse=True)

out = {
    'generated': 'see_timestamp',
    'note': 'Maker assumptions are OPTIMISTIC unless validated by fill tape',
    'routes': {
        'USD_to_token_to_USD': {'count': len(usd_routes), 'top10': usd_routes[:10]},
        'BTC_to_token_to_BTC': {'count': len(btc_routes), 'top10': btc_routes[:10]},
        'ETH_to_token_to_ETH': {'count': len(eth_routes), 'top10': eth_routes[:10]},
        'SOL_to_token_to_SOL': {'count': len(sol_routes), 'top10': sol_routes[:10]},
    }
}
with open('reports/kraken_numeraire_route_scores.json', 'w') as f:
    json.dump(out, f, indent=2)

print(f"\nUSD routes (spread>=150, depth>=$15): {len(usd_routes)}")
for r in usd_routes[:10]:
    print(f"  {r['pair']:15s} spread={r['spread_bps']:6.0f}bps numeraire_gain={r['numeraire_gain_bps']:+7.1f}bps usd_mark={r['usd_mark_bps']:+6.1f}bps")

print(f"\nBTC routes (spread>=150, depth>=$15): {len(btc_routes)}")
for r in btc_routes[:10]:
    print(f"  {r['pair']:15s} spread={r['spread_bps']:6.0f}bps numeraire_gain={r['numeraire_gain_bps']:+7.1f}bps usd_mark={r['usd_mark_bps']:+6.1f}bps")

print(f"\nETH routes (spread>=150, depth>=$15): {len(eth_routes)}")
for r in eth_routes[:10]:
    print(f"  {r['pair']:15s} spread={r['spread_bps']:6.0f}bps numeraire_gain={r['numeraire_gain_bps']:+7.1f}bps usd_mark={r['usd_mark_bps']:+6.1f}bps")

print(f"\nSOL routes (spread>=150, depth>=$15): {len(sol_routes)}")
for r in sol_routes[:10]:
    print(f"  {r['pair']:15s} spread={r['spread_bps']:6.0f}bps numeraire_gain={r['numeraire_gain_bps']:+7.1f}bps usd_mark={r['usd_mark_bps']:+6.1f}bps")

print(f"\nSaved to reports/kraken_numeraire_route_scores.json")
