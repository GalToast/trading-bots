import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()

print("=== CHECKING KNOWN PAIRS ===")
for pair in ['HONEYUSD', 'DUCKUSD', 'CQTUSD', 'BILLYUSD']:
    print(f"\n{pair}:")
    tk = c.ticker([pair])
    if pair in tk:
        t = tk[pair]
        bid = to_float((t.get('b') or [None])[0])
        ask = to_float((t.get('a') or [None])[0])
        last = to_float((t.get('c') or [None])[0])
        sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0
        print(f"  bid={bid} ask={ask} last={last} spread={sp:.0f}bps")
        
        try:
            d = c.depth(pair, count=10)
            if pair in d:
                book = d[pair]
            else:
                # Try alternate key
                alt = pair.replace('USD', '/USD')
                if alt in d:
                    book = d[alt]
                else:
                    book = d  # Maybe it's the only key
                    print(f"  depth keys: {list(d.keys())}")
            
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            
            if bids:
                for i, b in enumerate(bids[:3]):
                    vol = to_float(b[1])
                    price = to_float(b[0])
                    print(f"  bid L{i+1}: price={price} vol={vol} usd=\${vol*price:.0f}")
            else:
                print("  NO BIDS")
            
            if asks:
                for i, a in enumerate(asks[:3]):
                    vol = to_float(a[1])
                    price = to_float(a[0])
                    print(f"  ask L{i+1}: price={price} vol={vol} usd=\${vol*price:.0f}")
            else:
                print("  NO ASKS")
        except Exception as e:
            print(f"  depth error: {e}")
    else:
        print(f"  no ticker! keys={list(tk.keys()) if tk else 'empty'}")

print("\n=== CHECKING BTC QUOTED ===")
for pair in ['ZRXXBT', 'ZRX/XBT']:
    print(f"\n{pair}:")
    try:
        tk = c.ticker([pair])
        print(f"  ticker keys: {list(tk.keys())[:5]}")
        d = c.depth(pair, count=10)
        print(f"  depth keys: {list(d.keys())[:5]}")
        for k, v in list(d.items())[:2]:
            bids = v.get('bids', [])
            asks = v.get('asks', [])
            print(f"  {k}: {len(bids)} bids, {len(asks)} asks")
            if bids:
                vol = to_float(bids[0][1])
                price = to_float(bids[0][0])
                print(f"    best bid: {price} x {vol}")
            if asks:
                vol = to_float(asks[0][1])
                price = to_float(asks[0][0])
                print(f"    best ask: {price} x {vol}")
    except Exception as e:
        print(f"  error: {e}")
