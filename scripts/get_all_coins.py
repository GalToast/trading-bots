import sys, json
sys.path.insert(0, 'scripts')
from coinbase_advanced_client import CoinbaseAdvancedClient

c = CoinbaseAdvancedClient()
data = c.list_products(product_type='SPOT', limit=500)

with open('debug_products.json', 'w') as f:
    json.dump(data, f, indent=2, default=str)

prods = data.get('products', [])
coins = set()
for p in prods:
    pid = p.get('product_id', '')
    if pid.endswith('-USD') and not pid.startswith('USDC-'):
        coins.add(pid)

with open('coinbase_usd_pairs.txt', 'w') as f:
    for coin in sorted(coins):
        f.write(coin + '\n')
    f.write(f'\nTotal: {len(coins)}\n')

print(f'Got {len(prods)} products, {len(coins)} USD pairs')
