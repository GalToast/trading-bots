import sys, json, time
sys.path.insert(0, 'scripts')
from coinbase_advanced_client import CoinbaseAdvancedClient
client = CoinbaseAdvancedClient()

lines = []

# Live spread
try:
    resp = client.best_bid_ask(['PRL-USD'])
    books = resp.get('pricebooks', [])
    if books:
        book = books[0]
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        if bids and asks:
            bid = float(bids[0]['price'])
            ask = float(asks[0]['price'])
            spread_pct = ((ask - bid) / ((bid + ask) / 2)) * 100
            lines.append(f"PRL-USD: bid={bid} ask={ask} spread={spread_pct:.4f}% bid_sz={bids[0]['size']} ask_sz={asks[0]['size']}")
        else:
            lines.append("PRL-USD: NO BIDS/ASKS")
    else:
        lines.append("PRL-USD: NO BOOK")
except Exception as e:
    lines.append(f"PRL-USD: ERROR - {e}")

# 72h burst
try:
    end = int(time.time())
    start = end - (72 * 3600)
    candles = client.market_candles('PRL-USD', start=start, end=end, granularity='FIFTEEN_MINUTE')
    c_list = candles.get('candles', [])
    if len(c_list) >= 50:
        m1 = m2 = 0
        max_rng = 0
        avg_rng = 0
        for c in c_list:
            h = float(c.get('high', 0))
            l = float(c.get('low', 0))
            mid = (h + l) / 2 if (h + l) > 0 else 0
            if mid == 0: continue
            rng = (h - l) / mid * 100
            avg_rng += rng
            if rng > max_rng: max_rng = rng
            if rng > 1.0: m1 += 1
            if rng > 2.0: m2 += 1
        avg_rng = avg_rng / len(c_list)
        lines.append(f"PRL-USD: {len(c_list)}candles 1%+={m1}({m1/len(c_list)*100:.1f}%) 2%+={m2}({m2/len(c_list)*100:.1f}%) avg={avg_rng:.3f}% max={max_rng:.1f}%")
    else:
        lines.append(f"PRL-USD: only {len(c_list)} candles")
except Exception as e:
    lines.append(f"PRL-USD: BURST ERROR - {e}")

out = '\n'.join(lines)
print(out)
