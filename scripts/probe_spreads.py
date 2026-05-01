#!/usr/bin/env python3
"""Probe live bid-ask spreads for top burst candidates."""
import sys, json
sys.path.insert(0, 'scripts')
from coinbase_advanced_client import CoinbaseAdvancedClient

client = CoinbaseAdvancedClient()

# Top burst candidates to probe
pairs = ['RAVE-USD', 'NOM-USD', 'IRYS-USD', 'MON-USD', 'DASH-USD', 
         'FARTCOIN-USD', 'VVV-USD', 'TROLL-USD', 'TAO-USD', 'ZEC-USD',
         # Majors for comparison
         'BTC-USD', 'ETH-USD', 'SOL-USD', 'DOGE-USD', 'AVAX-USD']

results = []
for pid in pairs:
    try:
        resp = client.best_bid_ask([pid])
        books = resp.get('pricebooks', [])
        if not books:
            print(f"{pid}: NO BOOK")
            continue
        book = books[0]
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        if not bids or not asks:
            print(f"{pid}: NO BIDS/ASKS")
            continue
        bid = float(bids[0]['price'])
        ask = float(asks[0]['price'])
        bid_size = float(bids[0]['size'])
        ask_size = float(asks[0]['size'])
        spread = ask - bid
        spread_pct = (spread / ((bid + ask) / 2)) * 100 if (bid + ask) > 0 else 0
        
        # Can we trade with $24 per side?
        units_for_24 = 24 / ask if ask > 0 else 0
        
        results.append({
            'id': pid,
            'bid': bid,
            'ask': ask,
            'spread': round(spread, 8),
            'spread_pct': round(spread_pct, 4),
            'bid_size': round(bid_size, 4),
            'ask_size': round(ask_size, 4),
            'units_24usd': round(units_for_24, 4),
        })
        print(f"{pid:20s} bid={bid:<14} ask={ask:<14} spread={spread:<10} spread%={spread_pct:.4f}% bid_sz={bid_size:.4f} ask_sz={ask_size:.4f}")
    except Exception as e:
        print(f"{pid:20s} ERROR: {e}")

print()
# Can any of these overcome 0.80% round-trip maker fees?
print("=== FEE FLOOR ANALYSIS (0.80% round-trip maker fees) ===")
for r in results:
    viable = "✅" if r['spread_pct'] > 0.80 else "❌"
    margin = r['spread_pct'] - 0.80
    print(f"  {r['id']:20s} spread={r['spread_pct']:.4f}% vs 0.80% floor → {viable} margin={margin:+.4f}%")
