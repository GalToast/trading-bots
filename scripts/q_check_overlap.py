import json
kraken = json.load(open('reports/cache/kraken_spot_live_radar_ticks.json'))
coinbase = json.load(open('reports/cache/coinbase_spot_live_radar_ticks.json'))

print(f'Kraken keys: {list(kraken.keys())}')
print(f'Coinbase keys: {list(coinbase.keys())}')

# Find overlapping products
k_products = set()
for k, v in kraken.items():
    if isinstance(v, list):
        for tick in v[:1]:
            if isinstance(tick, dict) and 'product' in tick:
                k_products.add(tick['product'])
            elif isinstance(tick, dict) and 'pair' in tick:
                k_products.add(tick['pair'])

c_products = set()
for k, v in coinbase.items():
    if isinstance(v, list):
        for tick in v[:1]:
            if isinstance(tick, dict) and 'product' in tick:
                c_products.add(tick['product'])
            elif isinstance(tick, dict) and 'pair' in tick:
                c_products.add(tick['pair'])

print(f'\nKraken products in radar: {sorted(k_products)[:20]}')
print(f'Coinbase products in radar: {sorted(c_products)[:20]}')
overlap = k_products & c_products
print(f'\nOverlap ({len(overlap)}): {sorted(overlap)[:20]}')
