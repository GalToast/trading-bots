#!/usr/bin/env python3
"""
Vulture Backtest — Mode 1: Idiosyncratic dump detection on Kraken.

Scans the 19.9MB Kraken radar cache (1,485 products x 175 samples) for:
1. Sharp price drops (5%+ bid or ask decline within N samples)
2. Price recovery after the drop (mean reversion)
3. Simulated: buy at floor → sell at recovery

Output: reports/vulture_backtest_results.json
"""
import json
from datetime import datetime

kraken = json.load(open('reports/cache/kraken_spot_live_radar_ticks.json'))
samples = kraken['samples']
keep_seconds = kraken['keep_seconds']

print(f"Vulture Backtest: {len(samples)} products, {keep_seconds}s window")
print()

dump_threshold_pct = 5.0
min_samples_for_dump = 3  # drop must happen within 3 samples
recovery_threshold_pct = 3.0  # must recover 3%+

results = []
for product, ticks in samples.items():
    if len(ticks) < 10:
        continue

    # Get bid series
    bids = [t['bid'] for t in ticks if isinstance(t, dict) and t.get('bid', 0) > 0]
    asks = [t['ask'] for t in ticks if isinstance(t, dict) and t.get('ask', 0) > 0]

    if len(bids) < 10 or len(asks) < 10:
        continue

    # Scan for dumps (price drop > threshold within min_samples)
    max_drop = 0
    max_drop_idx = 0
    recovery_after_drop = 0

    for i in range(min_samples_for_dump, len(bids)):
        window = bids[max(0, i-min_samples_for_dump):i]
        if not window:
            continue
        peak = max(window)
        if peak <= 0:
            continue
        drop_pct = (peak - bids[i]) / peak * 100
        if drop_pct > max_drop:
            max_drop = drop_pct
            max_drop_idx = i

    if max_drop < dump_threshold_pct:
        continue

    # Check recovery after the drop
    post_drop_bids = bids[max_drop_idx:]
    if post_drop_bids:
        floor = min(post_drop_bids[:5])  # lowest point after drop
        if floor > 0:
            recovery = max(post_drop_bids)
            recovery_pct = (recovery - floor) / floor * 100
            recovery_after_drop = recovery_pct
        else:
            recovery_after_drop = 0
    else:
        recovery_after_drop = 0

    if recovery_after_drop >= recovery_threshold_pct:
        # Simulated roundtrip:
        # Buy at floor, sell at recovery
        # Net = recovery% - entry_cost - exit_cost (2x maker fees = 50bps)
        net_bps = recovery_after_drop * 100 - 50  # rough: recovery in bps minus fees

        results.append({
            'product': product,
            'max_drop_pct': round(max_drop, 2),
            'max_drop_sample': max_drop_idx,
            'recovery_pct': round(recovery_after_drop, 2),
            'net_bps_estimate': round(net_bps, 1),
            'tick_count': len(bids),
            'sample_bids': bids[:3],
        })

results.sort(key=lambda x: x['net_bps_estimate'], reverse=True)

print(f"Found {len(results)} products with {dump_threshold_pct}%+ dump and {recovery_threshold_pct}%+ recovery")
print()
print("Top 20 Vulture targets (sorted by net bps estimate):")
for r in results[:20]:
    print(f"  {r['product']:15s} drop={r['max_drop_pct']:6.1f}% recovery={r['recovery_pct']:6.1f}% net_est={r['net_bps_estimate']:+7.1f}bps samples={r['tick_count']}")

# Save
out = {
    'generated': datetime.utcnow().isoformat(),
    'dump_threshold_pct': dump_threshold_pct,
    'recovery_threshold_pct': recovery_threshold_pct,
    'total_products_scanned': len(samples),
    'vulture_candidates': len(results),
    'top20': results[:20],
}
with open('reports/vulture_backtest_results.json', 'w') as f:
    json.dump(out, f, indent=2)

print(f"\nSaved to reports/vulture_backtest_results.json")
