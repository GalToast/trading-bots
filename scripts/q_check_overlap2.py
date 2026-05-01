import json
kraken = json.load(open('reports/cache/kraken_spot_live_radar_ticks.json'))
coinbase = json.load(open('reports/cache/coinbase_spot_live_radar_ticks.json'))

k_keys = list(kraken.get('samples', {}).keys())[:10]
c_keys = list(coinbase.get('samples', {}).keys())[:10]
print('Kraken keys:', k_keys)
print('Coinbase keys:', c_keys)

c_normalized = set()
for k in coinbase.get('samples', {}).keys():
    normalized = k.replace('-', '').replace('/', '').upper()
    c_normalized.add(normalized)

k_normalized = set()
for k in kraken.get('samples', {}).keys():
    normalized = k.replace('-', '').replace('/', '').upper()
    k_normalized.add(normalized)

overlap = k_normalized & c_normalized
print(f'\nNormalized overlap: {len(overlap)} products')
print('First 20:', sorted(overlap)[:20])
