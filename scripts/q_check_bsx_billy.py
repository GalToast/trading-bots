import sys, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
c = KrakenSpotClient()
for pair in ['BSXUSD', 'BILLYUSD']:
    tk = c.ticker([pair])
    if tk and pair in tk:
        t = tk[pair]
        bid = to_float((t.get('b') or [None])[0])
        ask = to_float((t.get('a') or [None])[0])
        last = to_float((t.get('c') or [None])[0])
        vol24 = to_float((t.get('v') or [None, None])[1])
        sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0
        d = c.depth(pair, count=10)
        book = d.get(pair, {})
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        bd = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
        ad = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0
        imb = (bd - ad) / (bd + ad) if (bd + ad) > 0 else 0
        print(f'{pair}: bid={bid} ask={ask} spread={sp:.0f}bps vol24=${vol24:.0f} bid_depth=${bd:.0f} ask_depth=${ad:.0f} imb={imb:+.3f}')
    else:
        print(f'{pair}: NOT FOUND, keys in ticker: {list(tk.keys()) if tk else "empty"}')
