import sys, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()
assets = c.asset_pairs()

# Get all tickers
all_tickers = {}
keys = list(assets.keys())
for i in range(0, len(keys), 100):
    try:
        all_tickers.update(c.ticker(keys[i:i+100]))
    except:
        pass

# Take snapshot 1
snapshot1 = {}
for k in list(assets.keys())[:500]:
    if k in all_tickers:
        t = all_tickers[k]
        last = to_float((t.get('c') or [None])[0])
        if last > 0:
            snapshot1[k] = last

print(f"Snapshot 1: {len(snapshot1)} products with prices")
time.sleep(5)

# Take snapshot 2
all_tickers2 = {}
for i in range(0, len(keys), 100):
    try:
        all_tickers2.update(c.ticker(keys[i:i+100]))
    except:
        pass

snapshot2 = {}
for k in list(assets.keys())[:500]:
    if k in all_tickers2:
        t = all_tickers2[k]
        last = to_float((t.get('c') or [None])[0])
        if last > 0:
            snapshot2[k] = last

print(f"Snapshot 2: {len(snapshot2)} products with prices")

# Find products that moved
moved = []
for k in set(snapshot1.keys()) & set(snapshot2.keys()):
    p1 = snapshot1[k]
    p2 = snapshot2[k]
    if p1 > 0:
        change = abs(p2 - p1) / p1 * 10000
        if change > 10:  # More than 10bps move in 5 seconds
            moved.append((k, change, p1, p2))

moved.sort(key=lambda x: x[1], reverse=True)

print(f"\nProducts that moved >10bps in 5 seconds: {len(moved)}")
for k, change, p1, p2 in moved[:30]:
    alt = assets.get(k, {}).get('altname', k)
    print(f"  {alt:15s} {change:8.1f}bps ({p1:.8f} -> {p2:.8f})")
