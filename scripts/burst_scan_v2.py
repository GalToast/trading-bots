#!/usr/bin/env python3
"""Coinbase spot burst scanner v2."""
from __future__ import annotations
import json, sys, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

client = CoinbaseAdvancedClient()
log_lines = []

def log(msg):
    log_lines.append(msg)
    print(msg, flush=True)

# 1. Pull full product list
try:
    products = client.list_products(product_type='SPOT', get_all_products=True)
    items = products.get('products', [])
    log(f"Total products: {len(items)}")
except Exception as e:
    log(f"list_products ERROR: {e}")
    items = []

# Filter USD pairs
usd_pairs = [p for p in items if p.get('product_id','').endswith('-USD') 
             and p.get('status') == 'online']
log(f"USD spot pairs: {len(usd_pairs)}")

# 2. Filter tradable
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
log(f"Tradable pairs: {len(tradable)}")

# 3. Scan top 15 by tightest spread AND top 15 widest spread AND some mid-range
# Tight spread = liquid but low volatility; Wide spread = volatile but less liquid
# We need BOTH to find the sweet spot
scan_candidates = tradable[:15]  # Top 15 tightest spread (liquid)
scan_candidates += tradable[15:30]  # Next 15
scan_candidates += tradable[-15:]  # Bottom 15 widest spread (volatile alts)

results = []
for pair in scan_candidates:
    pid = pair['id']
    try:
        end = int(time.time())
        start = end - (72 * 3600)
        candles = client.market_candles(pid, start=start, end=end, granularity='FIFTEEN_MINUTE')
        candles_list = candles.get('candles', [])
        
        if len(candles_list) < 50:
            log(f"  {pid}: only {len(candles_list)} candles, skipping")
            continue
            
        moves_above_1pct = 0
        moves_above_2pct = 0
        max_move = 0
        avg_range_pct = 0
        total = 0
        
        for c in candles_list:
            o = float(c.get('open', 0))
            h = float(c.get('high', 0))
            l = float(c.get('low', 0))
            cl = float(c.get('close', 0))
            mid = (h + l) / 2 if (h + l) > 0 else 0
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
        log(f"  {pid}: {moves_above_1pct}x 1%+, {moves_above_2pct}x 2%+, avg_range={avg_range_pct:.3f}%")
        time.sleep(1.2)  # Rate limiting - ~50 req/min max
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log(f"  {pid}: {type(e).__name__}: {e}")
        log(f"    {tb[:300]}")

# 4. Rank
results.sort(key=lambda x: x['moves_1pct'], reverse=True)

log(f"\n=== TOP 20 BY 1%+ MOVES (72h) ===")
for r in results[:20]:
    log(f"  {r['id']:15s} price=${r['price']:<12} spread={r['spread_pct']:.3f}%  1pct+={r['moves_1pct']:3d}  2pct+={r['moves_2pct']:3d}  avg_range={r['avg_range_pct']:.3f}%  max={r['max_move_pct']:.1f}%")

log(f"\nTotal scanned: {len(results)}")

# Save
out = Path(__file__).parent.parent / 'reports' / 'coinbase_burst_scan_v2.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, indent=2), encoding='utf-8')

# Also save log
log_path = Path(__file__).parent.parent / 'reports' / 'burst_scan_v2_log.txt'
log_path.write_text('\n'.join(log_lines), encoding='utf-8')
