import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import legal_volume, maker_exit_floor_price, exit_floor_above_ask_bps
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()
assets = c.asset_pairs()
all_tickers = {}
keys = [k for k in assets if assets[k].get('status') == 'online']
for i in range(0, len(keys), 100):
    try:
        all_tickers.update(c.ticker(keys[i:i+100]))
    except:
        pass

print("Relaxed scan: spread >= 50bps, depth >= $5, any positive economics")
print()

# Track skip reasons
skipped = {'no_ticker': 0, 'wrong_quote': 0, 'low_spread': 0, 'low_depth': 0, 'negative_econ': 0, 'glm': 0}
near_misses = []  # Close to passing but not quite

for rest_pair, payload in assets.items():
    if payload.get('status') != 'online':
        continue
    p = parse_pair(rest_pair, payload)
    if not p:
        skipped['no_ticker'] += 1
        continue
    if p.quote not in ('USD', 'XBT', 'BTC', 'ETH'):
        skipped['wrong_quote'] += 1
        continue
    if 'GLMR' in p.wsname.upper():
        skipped['glm'] += 1
        continue

    tk = all_tickers.get(rest_pair)
    if not tk or rest_pair not in tk:
        skipped['no_ticker'] += 1
        continue

    t = tk[rest_pair]
    bid = to_float((t.get('b') or [None])[0])
    ask = to_float((t.get('a') or [None])[0])
    if bid <= 0 or ask <= 0:
        continue

    spread = compute_spread_bps(bid, ask)
    if spread < 50:
        skipped['low_spread'] += 1
        continue

    d = c.depth(rest_pair, count=10)
    book = d.get(rest_pair, {})
    bids = book.get('bids', [])
    asks = book.get('asks', [])
    bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
    ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

    if bid_d < 5 or ask_d < 5:
        skipped['low_depth'] += 1
        continue

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
        skipped['negative_econ'] += 1
        if net > -50:  # Near miss
            near_misses.append({
                'pair': p.wsname, 'quote': p.quote, 'spread': round(spread, 0),
                'bid_d': round(bid_d, 0), 'ask_d': round(ask_d, 0), 'net': round(net, 1)
            })
        continue

    near_misses.append({
        'pair': p.wsname, 'quote': p.quote, 'spread': round(spread, 0),
        'bid_d': round(bid_d, 0), 'ask_d': round(ask_d, 0), 'net': round(net, 1),
        'pass': True
    })

near_misses.sort(key=lambda x: x['net'], reverse=True)

print(f"Skipped: {json.dumps(skipped, indent=2)}")
print(f"\nNear misses and passes ({len(near_misses)} total):")
for r in near_misses[:20]:
    flag = '✅' if r.get('pass') else '❌'
    print(f"  {flag} {r['pair']:15s} {r['quote']:>4s} spread={r['spread']:5.0f}bps bid=${r['bid_d']:6.0f} ask=${r['ask_d']:6.0f} net={r['net']:+7.1f}bps")
