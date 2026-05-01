#!/usr/bin/env python3
"""
Update Velocity Monitor — Detects L2 book acceleration before sweeps.

Polls Kraken depth at 100ms intervals for target products.
Measures baseline update rate, detects 10x+ spikes.
Captures L2 depth snapshots around spikes for sweep detection.

Usage:
    python scripts/q_update_velocity.py --products L3USD,TRACUSD,RENDERUSD,NEARUSD,SOLUSD --duration 120
"""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--products', default='L3USD,TRACUSD,RENDERUSD,NEARUSD,SOLUSD')
parser.add_argument('--duration', type=int, default=120)
parser.add_argument('--poll-ms', type=int, default=100, help='Poll interval in milliseconds')
parser.add_argument('--spike-multiplier', type=float, default=5.0, help='Spike threshold (x baseline)')
args = parser.parse_args()

products = [p.strip() for p in args.products.split(',')]
poll_interval = args.poll_ms / 1000.0
spike_mult = args.spike_multiplier
duration = args.duration

print(f"Update Velocity Monitor: {len(products)} products, {duration}s, {args.poll_ms}ms poll")
print(f"Spike threshold: {spike_mult}x baseline rate")
print()

results = {}

for product in products:
    print(f"  Scanning {product}...")
    
    # Phase 1: Baseline measurement (10 seconds)
    baseline_updates = 0
    baseline_start = time.time()
    prev_hash = None
    
    for i in range(100):  # 10s at 100ms
        try:
            d = c.depth(product, count=10)
            book = d.get(product, d.get(product.replace('USD','/USD'), {}))
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            current_hash = hash(json.dumps(bids[:5] + asks[:5]))  # Hash top 5 levels
            
            if prev_hash is not None and current_hash != prev_hash:
                baseline_updates += 1
            prev_hash = current_hash
            
            time.sleep(poll_interval)
        except:
            time.sleep(poll_interval)
    
    baseline_seconds = time.time() - baseline_start
    baseline_rate = baseline_updates / max(baseline_seconds, 0.1)
    print(f"    Baseline: {baseline_updates} updates in {baseline_seconds:.1f}s = {baseline_rate:.1f} updates/sec")
    
    if baseline_rate < 0.5:
        print(f"    Book too slow (baseline < 0.5/s) — skipping")
        results[product] = {'status': 'too_slow', 'baseline_rate': baseline_rate}
        continue
    
    # Phase 2: Monitoring phase
    spike_threshold = baseline_rate * spike_mult
    spikes = []
    sweep_events = []
    
    monitoring_start = time.time()
    prev_hash2 = None
    update_count = 0
    window_start = time.time()
    window_updates = 0
    
    tick_count = 0
    max_ticks = int(duration / poll_interval)
    
    while tick_count < max_ticks:
        try:
            d = c.depth(product, count=10)
            book = d.get(product, d.get(product.replace('USD','/USD'), {}))
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            
            current_hash = hash(json.dumps(bids[:5] + asks[:5]))
            
            if prev_hash2 is not None and current_hash != prev_hash2:
                update_count += 1
                window_updates += 1
            
            prev_hash2 = current_hash
            tick_count += 1
            
            # Check rate every 1 second
            elapsed_window = time.time() - window_start
            if elapsed_window >= 1.0:
                current_rate = window_updates / elapsed_window
                
                if current_rate >= spike_threshold and current_rate > 2.0:
                    spike_event = {
                        'time_offset': round(time.time() - monitoring_start, 1),
                        'rate': round(current_rate, 1),
                        'baseline_rate': round(baseline_rate, 1),
                        'multiplier': round(current_rate / max(baseline_rate, 0.1), 1),
                        'bid_depth_usd': round(to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0, 1),
                        'ask_depth_usd': round(to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0, 1),
                        'spread_bps': round(compute_spread_bps(
                            to_float(bids[0][0]), to_float(asks[0][0])
                        ), 1),
                    }
                    spikes.append(spike_event)
                    print(f"    🔥 SPIKE at t+{spike_event['time_offset']}s: {current_rate:.1f}/s ({current_rate/baseline_rate:.1f}x baseline)")
                
                window_updates = 0
                window_start = time.time()
            
            time.sleep(poll_interval)
        except Exception as e:
            tick_count += 1
            time.sleep(poll_interval)
    
    monitoring_duration = time.time() - monitoring_start
    total_updates = update_count
    avg_rate = total_updates / max(monitoring_duration, 0.1)
    
    print(f"    Monitoring: {total_updates} updates in {monitoring_duration:.1f}s, avg {avg_rate:.1f}/s, {len(spikes)} spikes")
    
    results[product] = {
        'status': 'complete',
        'baseline_rate': round(baseline_rate, 1),
        'avg_monitoring_rate': round(avg_rate, 1),
        'total_updates': total_updates,
        'spike_count': len(spikes),
        'spikes': spikes[:10],  # Top 10
    }

# Summary
print(f"\n{'='*60}")
print(f"SUMMARY:")
for product, r in results.items():
    if r['status'] == 'too_slow':
        print(f"  {product:12s} TOO SLOW (baseline={r['baseline_rate']:.1f}/s)")
    else:
        flag = '🔥' if r['spike_count'] > 0 else '➡️'
        print(f"  {product:12s} {flag} baseline={r['baseline_rate']:.1f}/s avg={r['avg_monitoring_rate']:.1f}/s spikes={r['spike_count']}")

# Save
out = {
    'generated': utc_now(),
    'products': products,
    'poll_ms': args.poll_ms,
    'spike_multiplier': spike_mult,
    'duration_s': duration,
    'results': results,
}
with open('reports/update_velocity_monitor.json', 'w') as f:
    json.dump(out, f, indent=2)

print(f"\nSaved to reports/update_velocity_monitor.json")
