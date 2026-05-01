#!/usr/bin/env python3
"""
Bridge Tax Calculator — Using cached Kraken + Coinbase radar data.
Scores cross-exchange routes with REAL costs from historical cache.
"""
import json
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

# Fee structures
KRAKEN_TAKER_FEE_BPS = 40
KRAKEN_MAKER_FEE_BPS = 25
COINBASE_TAKER_FEE_BPS = 40
COINBASE_MAKER_FEE_BPS = 25

# Load cache data
kraken_cache = json.load(open('reports/cache/kraken_spot_live_radar_ticks.json'))
coinbase_cache = json.load(open('reports/cache/coinbase_spot_live_radar_ticks.json'))

kraken_samples = kraken_cache.get('samples', {})
coinbase_samples = coinbase_cache.get('samples', {})

# Build normalized key maps
def normalize(k):
    return k.replace('-', '').replace('/', '').upper()

k_map = {normalize(k): v for k, v in kraken_samples.items()}
c_map = {normalize(k): v for k, v in coinbase_samples.items()}

overlap = set(k_map.keys()) & set(c_map.keys())
print(f"Kraken products: {len(kraken_samples)}")
print(f"Coinbase products: {len(coinbase_samples)}")
print(f"Overlap (normalized): {len(overlap)} products")
print()

results = []
for norm_key in sorted(overlap):
    k_ticks = k_map[norm_key]
    c_ticks = c_map[norm_key]
    
    if not k_ticks or not c_ticks:
        continue
    
    k_last = k_ticks[-1] if isinstance(k_ticks, list) else k_ticks
    c_last = c_ticks[-1] if isinstance(c_ticks, list) else c_ticks
    
    if not isinstance(k_last, dict) or not isinstance(c_last, dict):
        continue
    
    k_bid = k_last.get('bid', 0)
    k_ask = k_last.get('ask', 0)
    c_bid = c_last.get('bid', 0)
    c_ask = c_last.get('ask', 0)
    
    if k_bid <= 0 or k_ask <= 0 or c_bid <= 0 or c_ask <= 0:
        continue
    
    k_spread = compute_spread_bps(k_bid, k_ask)
    c_spread = compute_spread_bps(c_bid, c_ask)
    
    k_mid = (k_bid + k_ask) / 2
    c_mid = (c_bid + c_ask) / 2
    lead_lag_bps = abs(k_mid - c_mid) / ((k_mid + c_mid) / 2) * 10000 if (k_mid + c_mid) > 0 else 0
    
    # Taker-Taker roundtrip
    total_fees = KRAKEN_TAKER_FEE_BPS + COINBASE_TAKER_FEE_BPS
    total_spread = k_spread + c_spread
    slippage = (k_spread + c_spread) * 0.1
    
    total_tax = total_fees + total_spread + slippage
    net_alpha = lead_lag_bps - total_tax
    
    # Maker-Maker roundtrip
    maker_fees = KRAKEN_MAKER_FEE_BPS + COINBASE_MAKER_FEE_BPS
    maker_spread = k_spread * 0.5 + c_spread * 0.5
    maker_tax = maker_fees + maker_spread + slippage
    maker_net = lead_lag_bps - maker_tax
    
    results.append({
        'product': norm_key,
        'kraken_spread_bps': round(k_spread, 1),
        'coinbase_spread_bps': round(c_spread, 1),
        'lead_lag_bps': round(lead_lag_bps, 1),
        'taker_taker_tax_bps': round(total_tax, 1),
        'taker_taker_net_bps': round(net_alpha, 1),
        'taker_profitable': net_alpha > 0,
        'maker_maker_tax_bps': round(maker_tax, 1),
        'maker_maker_net_bps': round(maker_net, 1),
        'maker_profitable': maker_net > 0,
    })

results.sort(key=lambda x: x['taker_taker_net_bps'], reverse=True)

print(f"Bridge Tax Results ({len(results)} overlapping products):")
print(f"{'Product':12s} {'LeadLag':>8s} {'K-Spd':>6s} {'C-Spd':>6s} {'TakerTax':>9s} {'TakerNet':>9s} {'MkrTax':>8s} {'MkrNet':>8s}")
print(f"{'-'*75}")
for r in results[:30]:
    t_flag = '✅' if r['taker_profitable'] else '❌'
    m_flag = '✅' if r['maker_profitable'] else '❌'
    print(f"{r['product']:12s} {r['lead_lag_bps']:8.1f}bps {r['kraken_spread_bps']:6.1f}bps {r['coinbase_spread_bps']:6.1f}bps {r['taker_taker_tax_bps']:8.1f}bps {r['taker_taker_net_bps']:+8.1f}bps {t_flag} {r['maker_maker_tax_bps']:8.1f}bps {r['maker_maker_net_bps']:+8.1f}bps {m_flag}")

taker_wins = sum(1 for r in results if r['taker_profitable'])
maker_wins = sum(1 for r in results if r['maker_profitable'])
print(f"\nSummary: {taker_wins}/{len(results)} profitable as Taker-Taker, {maker_wins}/{len(results)} profitable as Maker-Maker")

out = {
    'generated': utc_now(),
    'kraken_products': len(kraken_samples),
    'coinbase_products': len(coinbase_samples),
    'overlap': len(overlap),
    'results': results,
}
with open('reports/bridge_tax_calculator.json', 'w') as f:
    json.dump(out, f, indent=2)

print(f"\nSaved to reports/bridge_tax_calculator.json")
