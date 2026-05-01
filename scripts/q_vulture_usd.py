import json
d = json.load(open('reports/vulture_backtest_results.json'))
candidates = d['top20']

print("Vulture candidates filtered for realistic USD pairs (175 samples, 5-30% drop):")
for r in candidates:
    # Filter: USD quoted, 175 samples, realistic drop (not 99%)
    if 'USD' in r['product'] and r['tick_count'] == 175 and 5 <= r['max_drop_pct'] <= 30:
        print(f"  {r['product']:15s} drop={r['max_drop_pct']:6.1f}% recovery={r['recovery_pct']:6.1f}% net_est={r['net_bps_estimate']:+7.1f}bps")

print("\nAll 50 from full results (USD only, 175 samples):")
# Load full results
import os
if os.path.exists('reports/vulture_backtest_results.json'):
    d2 = json.load(open('reports/vulture_backtest_results.json'))
    # The full list is in top20 only, need to re-run with all results
    print("  (Need full scan - top20 only saved)")

# Quick re-scan for USD-only with realistic drops
import sys; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, parse_pair
c = KrakenSpotClient()
kraken = json.load(open('reports/cache/kraken_spot_live_radar_ticks.json'))

usd_vultures = []
for product, ticks in kraken['samples'].items():
    if 'USD' not in product.upper():
        continue
    if len(ticks) < 175:
        continue
    bids = [t['bid'] for t in ticks if isinstance(t, dict) and t.get('bid', 0) > 0]
    if len(bids) < 10:
        continue

    max_drop = 0
    for i in range(3, len(bids)):
        window = bids[max(0,i-3):i]
        if not window: continue
        peak = max(window)
        if peak <= 0: continue
        drop = (peak - bids[i]) / peak * 100
        if drop > max_drop:
            max_drop = drop

    # Recovery
    post = bids[-20:]  # last 20 samples
    if post and max_drop > 5:
        floor = min(post[:5])
        if floor > 0:
            recovery = (max(post) - floor) / floor * 100
        else:
            recovery = 0
    else:
        recovery = 0

    if max_drop >= 5 and recovery >= 3 and max_drop < 50:
        net = recovery * 100 - 50
        usd_vultures.append({
            'product': product,
            'max_drop_pct': round(max_drop, 1),
            'recovery_pct': round(recovery, 1),
            'net_bps': round(net, 1),
            'samples': len(bids)
        })

usd_vultures.sort(key=lambda x: x['net_bps'], reverse=True)
print(f"\nUSD vultures (5-50% drop, 3%+ recovery, 175 samples): {len(usd_vultures)}")
for r in usd_vultures[:15]:
    print(f"  {r['product']:15s} drop={r['max_drop_pct']:5.1f}% recovery={r['recovery_pct']:6.1f}% net={r['net_bps']:+7.1f}bps")
