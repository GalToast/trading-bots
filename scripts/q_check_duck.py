import sys, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps, compute_book_imbalance
c = KrakenSpotClient()
tk = c.ticker(['DUCKUSD'])
t = tk.get('DUCKUSD', {})
bid = to_float((t.get('b') or [None])[0])
ask = to_float((t.get('a') or [None])[0])
last = to_float((t.get('c') or [None])[0])
vol24 = to_float((t.get('v') or [None, None])[1])
sp = compute_spread_bps(bid, ask)
print(f'DUCK-USD: bid={bid} ask={ask} spread={sp:.1f}bps vol24=${vol24:.0f}')
for i in range(5):
    time.sleep(2)
    d = c.depth('DUCKUSD', count=10)
    book = d.get('DUCKUSD', d.get('DUCK/USD', {}))
    bids = book.get('bids', [])
    asks = book.get('asks', [])
    bd = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
    ad = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0
    imb = compute_book_imbalance(bids, asks, 3)
    print(f'  t={i*2}s: bid_depth=${bd:.0f} ask_depth=${ad:.0f} imbalance={imb:+.3f}')
