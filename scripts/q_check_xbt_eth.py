import sys; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
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

# Focus on XBT and ETH quoted pairs
for quote in ['XBT', 'BTC', 'ETH']:
    print(f"\n{quote}-quoted pairs:")
    for rest_pair, payload in assets.items():
        if payload.get('status') != 'online':
            continue
        ws = payload.get('wsname', '')
        if f'/{quote}' not in ws:
            continue
        p = parse_pair(rest_pair, payload)
        tk = all_tickers.get(rest_pair, {})
        t = tk.get(rest_pair, {})
        bid = to_float((t.get('b') or [None])[0])
        ask = to_float((t.get('a') or [None])[0])
        spread = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0
        d = c.depth(rest_pair, count=10)
        book = d.get(rest_pair, {})
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
        ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0
        print(f"  {ws:15s} spread={spread:6.0f}bps bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f}")

# Also check USD pairs with widest spreads
print("\nTop 20 USD pairs by spread:")
usd_pairs = []
for rest_pair, payload in assets.items():
    if payload.get('status') != 'online':
        continue
    ws = payload.get('wsname', '')
    if '/USD' not in ws:
        continue
    tk = all_tickers.get(rest_pair, {})
    t = tk.get(rest_pair, {})
    bid = to_float((t.get('b') or [None])[0])
    ask = to_float((t.get('a') or [None])[0])
    spread = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0
    if spread > 0:
        usd_pairs.append((ws, spread, bid, ask))

usd_pairs.sort(key=lambda x: -x[1])
for ws, sp, bid, ask in usd_pairs[:20]:
    print(f"  {ws:15s} spread={sp:6.0f}bps")
