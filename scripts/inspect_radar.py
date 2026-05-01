import json

with open('reports/coinbase_spot_live_radar.json') as f:
    data = json.load(f)

products = data if isinstance(data, list) else data.get('products', data.get('rows', []))

def mid(p):
    b = p.get('bid', 0)
    a = p.get('ask', 0)
    return (b + a) / 2 if b and a else 0

def spread_bps(p):
    b = p.get('bid', 0)
    a = p.get('ask', 0)
    m = (b + a) / 2
    return (a - b) / m * 10000 if m else 9999

top = sorted(products, key=lambda x: x.get('best_window_bps', 0), reverse=True)[:30]
print('Top CB products by best_window_bps:')
for p in top:
    m = mid(p)
    sp = spread_bps(p)
    bw = p.get('best_window_bps', 0)
    bs = p.get('best_short_bps', 0)
    pid = p.get('product_id', '?')
    vol = p.get('quote_volume_native', 0)
    print(f"  {pid:<20} mid={m:.4e}  best_win={bw:.0f}bps  best_short={bs:.0f}bps  spread={sp:.0f}bps  vol_usd={vol:.0f}")

print()
print('All keys:', list(products[0].keys()) if products else [])
