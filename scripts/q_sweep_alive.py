import sys, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()

# Get all USD pairs
assets = c.asset_pairs()
usd_pairs = {}
for k, v in assets.items():
    if v.get('status') != 'online':
        continue
    ws = v.get('wsname', '')
    if '/USD' in ws.upper() or 'USD' in ws.upper():
        usd_pairs[k] = v

# Batch ticker
all_tickers = {}
keys = list(usd_pairs.keys())
for i in range(0, len(keys), 100):
    batch = keys[i:i+100]
    try:
        t = c.ticker(batch)
        all_tickers.update(t)
    except:
        pass

# Find wide spreads
wide = []
for k, v in usd_pairs.items():
    tk = all_tickers.get(k)
    if not tk:
        continue
    t = tk.get(k, {})
    bid = to_float((t.get('b') or [None])[0])
    ask = to_float((t.get('a') or [None])[0])
    if bid <= 0 or ask <= 0:
        continue
    sp = compute_spread_bps(bid, ask)
    if sp >= 150:
        alt = v.get('altname', k)
        wide.append({'pair': alt, 'spread': sp, 'bid': bid, 'ask': ask, 'key': k})

wide.sort(key=lambda x: x['spread'], reverse=True)
print(f'Found {len(wide)} USD pairs with spread >= 150bps')
print()

# Now take 2 snapshots 3s apart for the top 15 to detect movement
top15 = wide[:15]
print('Checking for price movement (3s window):')
print()

for item in top15:
    k = item['key']
    try:
        t1 = c.ticker([k])
        t1d = t1.get(k, t1.get(k.replace('/',''), {}))
        last1 = to_float((t1d.get('c') or [None])[0])

        time.sleep(3)

        t2 = c.ticker([k])
        t2d = t2.get(k, t2.get(k.replace('/',''), {}))
        last2 = to_float((t2d.get('c') or [None])[0])

        if last1 > 0 and last2 > 0:
            move = abs(last2 - last1) / ((last1 + last2) / 2) * 10000
            direction = 'UP' if last2 > last1 else 'DOWN' if last2 < last1 else 'FLAT'
            flag = '🔥' if move > 20 else '⏳' if move > 5 else '➡️'
            print(f'{flag} {item["pair"]:15s} spread={item["spread"]:6.0f}bps move={move:6.1f}bps {direction}')
    except Exception as e:
        print(f'?  {item["pair"]:15s} error: {e}')
