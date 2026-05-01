"""
Sibling scanner: find Coinbase+Kraken coins where:
1. Price in the sub-$0.001 range (MOG-like tick-jump geometry)  
2. 24h volume is high enough ($50k+ USD vol estimated)
3. A single price-tick jump clears 120bps round-trip fee hurdle
4. Bid/ask spread is tight enough to still have net edge after fees

MOG geometry insight:
- Price: ~0.000000130 to 0.000000160 USD
- Min tick: 1e-09 (1 sub-satoshi)
- One tick at 1.3e-07 = 1/13 ~ 7.7% move - massively clears 120bps (2.4%) fee
- Win condition = catch even a SINGLE tick jump up in 24h holding window

Strategy: look for coins where ask/bid spread and price level create similar
"geometric leverage" from discretized price ticks.
"""
import json

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  Could not load {path}: {e}")
        return {}

cb_radar = load_json('reports/coinbase_spot_live_radar.json')
kr_radar = load_json('reports/kraken_spot_live_radar.json')

cb_products = cb_radar if isinstance(cb_radar, list) else cb_radar.get('products', cb_radar.get('rows', []))
kr_products = kr_radar if isinstance(kr_radar, list) else kr_radar.get('products', kr_radar.get('rows', []))

def check_cb_sibling(p):
    pid = p.get('product_id', '')
    if not pid.endswith('-USD'):
        return None
    
    bid = p.get('bid', 0)
    ask = p.get('ask', 0)
    mid = (bid + ask) / 2 if bid and ask else 0
    
    if not mid or mid <= 0:
        return None
    
    # Spread as bps
    spread_bps = (ask - bid) / mid * 10000 if mid else 9999
    
    # Move in last period (proxy for volatility)
    move_last_bps = abs(p.get('move_last_bps', 0))
    ret_60m_bps = abs(p.get('ret_60m_bps', 0))
    best_short_bps = p.get('best_short_bps', 0)
    best_window_bps = p.get('best_window_bps', 0)
    
    # Estimate "tick size" as the minimum increment
    # For very low price assets: assume 1 sig-fig increment matters
    # A price like 1.3e-7: one tick up = 1.4e-7 = 7.7% move
    # We use the bid/ask to figure out approximate tick size
    # If bid=1.3e-7, ask=1.4e-7, that's 7.7% spread AND 7.7% min move
    
    # Key metric: if you catch one full "price level" increment, 
    # what % gain is that vs round-trip fee cost?
    # For MOG: (0.14 - 0.13) / 0.13 = 7.7% >> 2.4% (120bps x2)
    
    # Best proxy: what's the spread % (if it's also the min tick)?
    # Low price + large spread_bps = geometric leverage
    
    # Filter: best_window_bps must exist and exceed 240bps (fee hurdle)
    if best_window_bps < 250:  # Need >2.5% move somewhere in scan window
        return None
    
    # Also filter out extremely wide spreads (illiquid)
    if spread_bps > 1000:  # >10% spread is untradeable
        return None
    
    vol_native = p.get('quote_volume_native', 0)
    
    return {
        'product': pid,
        'mid': mid,
        'spread_bps': round(spread_bps, 1),
        'best_window_bps': round(best_window_bps, 1),
        'best_short_bps': round(best_short_bps, 1),
        'move_last_bps': round(move_last_bps, 1),
        'ret_60m_bps': round(ret_60m_bps, 1),
        'vol_native_usd': round(vol_native, 0),
        'fee_hurdle_bps': 240,
        'edge_score': round(best_window_bps / 240, 2),  # multiples of fee hurdle
    }

cb_siblings = []
for p in cb_products:
    result = check_cb_sibling(p)
    if result:
        cb_siblings.append(result)

# Sort by edge score descending
cb_siblings.sort(key=lambda x: x['edge_score'], reverse=True)

print("=" * 80)
print("COINBASE MOG SIBLINGS (best_window > 250bps, spread < 1000bps)")
print("=" * 80)
print(f"Found: {len(cb_siblings)} candidates")
print()
print(f"{'Product':<20} {'Price':>12} {'Spread_bps':>10} {'BestWin_bps':>12} {'BestShrt_bps':>13} {'EdgeScore':>10}")
print("-" * 80)
for s in cb_siblings[:30]:
    print(f"{s['product']:<20} {s['mid']:>12.4e} {s['spread_bps']:>10.0f} {s['best_window_bps']:>12.0f} {s['best_short_bps']:>13.0f} {s['edge_score']:>10.2f}x")

# Now check Kraken siblings
def check_kr_sibling(p):
    pid = p.get('product_id', '')
    if not pid.endswith('-USD') and not pid.endswith('/USD'):
        return None
    
    bid = p.get('bid', 0)
    ask = p.get('ask', 0)
    mid = (bid + ask) / 2 if bid and ask else 0
    
    if not mid or mid <= 0:
        return None
    
    spread_bps = (ask - bid) / mid * 10000 if mid else 9999
    
    move_last_bps = abs(p.get('move_last_bps', 0))
    best_short_bps = p.get('best_short_bps', 0)
    maker_taker_rt = p.get('maker_taker_round_trip_bps', 0)
    
    # Kraken has lower fees - check 80bps round-trip
    kraken_fee_hurdle = maker_taker_rt if maker_taker_rt > 0 else 80
    
    ret_15m = abs(p.get('ret_15m_bps', 0))
    
    if best_short_bps < 150:
        return None
    
    if spread_bps > 1000:
        return None
    
    return {
        'product': pid,
        'mid': mid,
        'spread_bps': round(spread_bps, 1),
        'best_short_bps': round(best_short_bps, 1),
        'move_last_bps': round(move_last_bps, 1),
        'ret_15m_bps': round(ret_15m, 1),
        'maker_taker_rt_bps': round(kraken_fee_hurdle, 1),
        'edge_score': round(best_short_bps / max(kraken_fee_hurdle, 80), 2),
    }

kr_siblings = []
for p in kr_products:
    result = check_kr_sibling(p)
    if result:
        kr_siblings.append(result)

kr_siblings.sort(key=lambda x: x['edge_score'], reverse=True)

print()
print("=" * 80)
print("KRAKEN SIBLINGS (best_short > 150bps, lower fee hurdle)")
print("=" * 80)
print(f"Found: {len(kr_siblings)} candidates")
print()
print(f"{'Product':<20} {'Price':>12} {'Spread_bps':>10} {'BestShrt_bps':>13} {'KR_Fee_RT':>10} {'EdgeScore':>10}")
print("-" * 80)
for s in kr_siblings[:20]:
    print(f"{s['product']:<20} {s['mid']:>12.4e} {s['spread_bps']:>10.0f} {s['best_short_bps']:>13.0f} {s['maker_taker_rt_bps']:>10.0f} {s['edge_score']:>10.2f}x")

# Overlap - products on BOTH venues
cb_bases = set(s['product'].replace('-USD','') for s in cb_siblings)
kr_bases = set(s['product'].replace('-USD','').replace('/USD','') for s in kr_siblings)
overlap = cb_bases & kr_bases
print()
print(f"Cross-venue OVERLAP (on both CB and Kraken): {overlap}")
