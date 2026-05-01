import sys; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
c = KrakenSpotClient()
all_pairs = c.asset_pairs()
results = []
for k, v in all_pairs.items():
    alt = v.get('altname', '').upper()
    ws = v.get('wsname', '').upper()
    quote = v.get('wsname', '').upper().split('/')[-1] if '/' in v.get('wsname', '') else ''
    if quote != 'USD' or v.get('status') != 'online':
        continue
    tk = c.ticker([k])
    if k not in tk:
        continue
    bid = to_float((tk[k].get('b') or [None])[0])
    ask = to_float((tk[k].get('a') or [None])[0])
    if bid <= 0 or ask <= 0:
        continue
    spread = compute_spread_bps(bid, ask)
    if spread >= 150:
        results.append({'pair': alt, 'spread': round(spread, 0), 'bid': bid, 'ask': ask})
results.sort(key=lambda x: x['spread'], reverse=True)
with open('reports/wide_spread_scan.txt', 'w') as f:
    f.write(f"Found {len(results)} USD pairs with spread >= 150bps\n\n")
    for r in results[:30]:
        f.write(f"{r['pair']:15s} spread={r['spread']:.0f}bps\n")
