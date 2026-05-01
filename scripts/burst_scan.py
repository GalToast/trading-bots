#!/usr/bin/env python3
"""Coinbase spot burst scanner - pulls full USD universe and scans 72h volatility."""
from __future__ import annotations
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

client = CoinbaseAdvancedClient()

# 1. Pull full product list
products = client.list_products(product_type='SPOT', get_all_products=True, limit=None)
items = products.get('products', [])
print(f"Total products: {len(items)}", flush=True)

usd_pairs = [p for p in items if p.get('product_id','').endswith('-USD') 
             and p.get('status') == 'online']
print(f"USD spot pairs: {len(usd_pairs)}", flush=True)

# 2. Filter for tradable (price available, spread reasonable)
tradable = []
for p in usd_pairs:
    try:
        spread = float(p.get('bid_ask_spread_pct', 0) or 0)
        price_str = p.get('price', '0')
        price = float(price_str) if price_str else 0
        min_size = float(p.get('base_min_size', 0) or 0)
        if price > 0 and spread >= 0:
            tradable.append({
                'id': p['product_id'],
                'price': price,
                'spread_pct': spread,
                'min_size': min_size,
            })
    except:
        pass

tradable.sort(key=lambda x: x['spread_pct'])
print(f"Tradable pairs: {len(tradable)}", flush=True)

# 3. Scan 72h candles for each pair - measure volatility
results = []
for pair in tradable[:30]:  # Start with top 30 by tightest spread
    pid = pair['id']
    try:
        end = int(time.time())
        start = end - (72 * 3600)
        candles = client.market_candles(pid, start=start, end=end, granularity='FIVE_MINUTES')
        candles_list = candles.get('candles', [])
        
        if len(candles_list) < 100:
            continue
            
        # Calculate 5m returns
        moves_above_1pct = 0
        moves_above_2pct = 0
        max_move = 0
        avg_range_pct = 0
        total = 0
        
        for c in candles_list:
            o, h, l, cl = float(c[0]), float(c[1]), float(c[2]), float(c[3])  # open, high, low, close
            mid = (h + l) / 2
            if mid == 0:
                continue
            rng = (h - l) / mid * 100
            total += 1
            avg_range_pct += rng
            if rng > max_move:
                max_move = rng
            if rng > 1.0:
                moves_above_1pct += 1
            if rng > 2.0:
                moves_above_2pct += 1
        
        avg_range_pct = (avg_range_pct / total) if total > 0 else 0
        
        results.append({
            'id': pid,
            'price': pair['price'],
            'spread_pct': pair['spread_pct'],
            'candles': len(candles_list),
            'moves_1pct': moves_above_1pct,
            'moves_2pct': moves_above_2pct,
            'max_move_pct': round(max_move, 3),
            'avg_range_pct': round(avg_range_pct, 3),
        })
    except Exception as e:
        print(f"  {pid}: ERROR - {e}", flush=True)

# 4. Rank by burst frequency
results.sort(key=lambda x: x['moves_1pct'], reverse=True)

print("\n=== TOP 20 BY 1%+ MOVES (72h) ===", flush=True)
for r in results[:20]:
    print(f"  {r['id']:15s} price=${r['price']:<12} spread={r['spread_pct']:.3f}%  1pct+={r['moves_1pct']:3d}  2pct+={r['moves_2pct']:3d}  avg_range={r['avg_range_pct']:.3f}%  max={r['max_move_pct']:.1f}%", flush=True)

print(f"\nTotal scanned: {len(results)}", flush=True)

# Save results
out = Path(__file__).parent.parent / 'reports' / 'coinbase_burst_scan_results.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, indent=2), encoding='utf-8')
print(f"Saved to {out}", flush=True)
