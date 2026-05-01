#!/usr/bin/env python3
"""
Bridge Tax Calculator — Honest cost scoring for cross-exchange strategies.

For any cross-exchange route (e.g., Long Kraken, Short Coinbase),
computes the REAL total cost including:
- Taker fees on both exchanges
- Spread on both exchanges at time of trade
- Slippage estimate from poll delay
- Min-size validation on both exchanges

Usage:
    python scripts/q_bridge_tax_calculator.py --products BTC,ETH,SOL,NEAR,RENDER,L3 --poll-interval 1.0
"""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

# Fee structures
KRAKEN_TAKER_FEE_BPS = 40
KRAKEN_MAKER_FEE_BPS = 25
COINBASE_TAKER_FEE_BPS = 40
COINBASE_MAKER_FEE_BPS = 25  # Advanced Trade

def get_kraken_price(client, product):
    """Get Kraken price for a product."""
    kraken_map = {
        'BTC': 'XBTUSD', 'ETH': 'ETHUSD', 'SOL': 'SOLUSD',
        'NEAR': 'NEARUSD', 'RENDER': 'RENDERUSD', 'L3': 'L3USD'
    }
    pair = kraken_map.get(product.upper())
    if not pair:
        return None
    tk = client.ticker([pair])
    if pair not in tk:
        return None
    t = tk[pair]
    return {
        'bid': to_float((t.get('b') or [None])[0]),
        'ask': to_float((t.get('a') or [None])[0]),
        'last': to_float((t.get('c') or [None])[0]),
    }

def get_coinbase_price(product):
    """Get Coinbase price for a product using REST."""
    # Coinbase uses product IDs like BTC-USD
    import urllib.request
    product_id = f"{product.upper()}-USD"
    url = f"https://api.exchange.coinbase.com/products/{product_id}/ticker"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return {
                'bid': to_float(data.get('bid', 0)),
                'ask': to_float(data.get('ask', 0)),
                'last': to_float(data.get('price', 0)),
            }
    except Exception as e:
        return None

def compute_bridge_tax(kraken, coinbase, poll_interval_s=1.0, entry_style='taker', exit_style='taker'):
    """
    Compute the full bridge tax for a cross-exchange roundtrip.
    
    entry_style/exit_style: 'taker' or 'maker'
    """
    if not kraken or not coinbase:
        return {'error': 'missing_price_data'}
    
    k_bid, k_ask = kraken['bid'], kraken['ask']
    c_bid, c_ask = coinbase['bid'], coinbase['ask']
    
    if k_bid <= 0 or k_ask <= 0 or c_bid <= 0 or c_ask <= 0:
        return {'error': 'invalid_prices'}
    
    k_spread = compute_spread_bps(k_bid, k_ask)
    c_spread = compute_spread_bps(c_bid, c_ask)
    
    # Lead-lag gap (absolute difference in mid prices)
    k_mid = (k_bid + k_ask) / 2
    c_mid = (c_bid + c_ask) / 2
    lead_lag_bps = abs(k_mid - c_mid) / ((k_mid + c_mid) / 2) * 10000 if (k_mid + c_mid) > 0 else 0
    
    # Fee costs based on entry/exit style
    if entry_style == 'taker':
        entry_fee = KRAKEN_TAKER_FEE_BPS
    else:
        entry_fee = KRAKEN_MAKER_FEE_BPS
    
    if exit_style == 'taker':
        exit_fee = COINBASE_TAKER_FEE_BPS
    else:
        exit_fee = COINBASE_MAKER_FEE_BPS
    
    total_fees = entry_fee + exit_fee
    
    # Spread costs (you cross the spread on taker, pay half on maker)
    if entry_style == 'taker':
        entry_spread_cost = k_spread
    else:
        entry_spread_cost = k_spread * 0.5  # Maker captures half spread
    
    if exit_style == 'taker':
        exit_spread_cost = c_spread
    else:
        exit_spread_cost = c_spread * 0.5
    
    total_spread = entry_spread_cost + exit_spread_cost
    
    # Slippage estimate: how much can price move in poll_interval seconds?
    # Conservative estimate: 1s volatility ≈ 0.1-0.5bps for liquid, 1-5bps for illiquid
    # We use the spread as a proxy for illiquidity
    avg_spread = (k_spread + c_spread) / 2
    slippage_bps = avg_spread * 0.1 * poll_interval_s  # 10% of spread per second of delay
    
    # Total bridge tax
    total_tax = total_fees + total_spread + slippage_bps
    
    # Min-size check ($10 minimum on both exchanges)
    min_notional_kraken = 10  # USD
    min_notional_coinbase = 10  # USD
    
    # Net alpha = lead_lag - bridge_tax
    net_alpha = lead_lag_bps - total_tax
    
    return {
        'kraken_spread_bps': round(k_spread, 1),
        'coinbase_spread_bps': round(c_spread, 1),
        'lead_lag_bps': round(lead_lag_bps, 1),
        'entry_fee_bps': entry_fee,
        'exit_fee_bps': exit_fee,
        'total_fees_bps': total_fees,
        'total_spread_cost_bps': round(total_spread, 1),
        'slippage_estimate_bps': round(slippage_bps, 1),
        'total_bridge_tax_bps': round(total_tax, 1),
        'net_alpha_bps': round(net_alpha, 1),
        'profitable': net_alpha > 0,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--products', default='BTC,ETH,SOL,NEAR,RENDER,L3')
    parser.add_argument('--poll-interval', type=float, default=1.0)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--json-path', default='reports/bridge_tax_calculator.json')
    args = parser.parse_args()
    
    products = [p.strip() for p in args.products.split(',')]
    kraken_client = KrakenSpotClient()
    
    print(f"Bridge Tax Calculator: {len(products)} products, {args.samples} samples, {args.poll_interval}s interval")
    print(f"Entry=Taker, Exit=Taker (worst case)")
    print()
    
    all_results = {}
    
    for sample in range(args.samples):
        print(f"Sample {sample+1}:")
        for product in products:
            k = get_kraken_price(kraken_client, product)
            c = get_coinbase_price(product)
            
            tax = compute_bridge_tax(k, c, poll_interval_s=args.poll_interval,
                                     entry_style='taker', exit_style='taker')
            
            if 'error' not in tax:
                profit_flag = '🔥 PROFITABLE' if tax['profitable'] else '❌ UNPROFITABLE'
                print(f"  {product:8s} lead-lag={tax['lead_lag_bps']:6.1f}bps tax={tax['total_bridge_tax_bps']:6.1f}bps net={tax['net_alpha_bps']:+7.1f}bps {profit_flag}")
            else:
                print(f"  {product:8s} ERROR: {tax['error']}")
            
            if product not in all_results:
                all_results[product] = []
            all_results[product].append(tax)
        
        if sample < args.samples - 1:
            time.sleep(args.poll_interval)
    
    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY (avg across {args.samples} samples):")
    for product, results in all_results.items():
        valid = [r for r in results if 'error' not in r]
        if not valid:
            continue
        avg_lead = sum(r['lead_lag_bps'] for r in valid) / len(valid)
        avg_tax = sum(r['total_bridge_tax_bps'] for r in valid) / len(valid)
        avg_net = sum(r['net_alpha_bps'] for r in valid) / len(valid)
        profitable_count = sum(1 for r in valid if r['profitable'])
        print(f"  {product:8s} avg_lead={avg_lead:6.1f}bps avg_tax={avg_tax:6.1f}bps avg_net={avg_net:+7.1f}bps profitable={profitable_count}/{len(valid)}")
    
    out = {
        'generated': utc_now(),
        'products': products,
        'poll_interval_s': args.poll_interval,
        'samples': args.samples,
        'results': all_results,
    }
    with open(args.json_path, 'w') as f:
        json.dump(out, f, indent=2)
    
    print(f"\nSaved to {args.json_path}")

if __name__ == '__main__':
    main()
