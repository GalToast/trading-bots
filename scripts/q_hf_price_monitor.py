import sys, time, json; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()

products = ['ALGOUSD', 'FARTCOINUSD', 'ALEOUSD', 'APTUSD', 'ATOMUSD']

print("High-frequency price monitor — 100ms poll, 60s duration")
print()

for product in products:
    print(f"Monitoring {product}...")
    
    prices = []
    bids = []
    asks = []
    timestamps = []
    
    for i in range(600):  # 60s at 100ms
        try:
            tk = c.ticker([product])
            if product not in tk:
                time.sleep(0.1)
                continue
            t = tk[product]
            last = to_float((t.get('c') or [None])[0])
            bid = to_float((t.get('b') or [None])[0])
            ask = to_float((t.get('a') or [None])[0])
            
            if last > 0:
                prices.append(last)
                bids.append(bid)
                asks.append(ask)
                timestamps.append(time.time())
        
        except:
            pass
        time.sleep(0.1)
    
    if len(prices) < 10:
        print(f"  Not enough data ({len(prices)} ticks)")
        continue
    
    # Calculate metrics
    total_time = timestamps[-1] - timestamps[0]
    tick_rate = len(prices) / max(total_time, 0.1)
    
    # Price changes
    max_up = 0
    max_down = 0
    max_up_idx = 0
    max_down_idx = 0
    
    for i in range(1, len(prices)):
        for j in range(max(0, i-50), i):  # Look back up to 50 ticks
            change = (prices[i] - prices[j]) / prices[j] * 10000
            if change > max_up:
                max_up = change
                max_up_idx = i
            if change < max_down:
                max_down = change
                max_down_idx = i
    
    # Volatility (std of 1-second returns)
    returns = []
    for i in range(10, len(prices)):
        r = abs(prices[i] - prices[i-10]) / prices[i-10] * 10000  # 1-second return
        returns.append(r)
    avg_vol = sum(returns) / len(returns) if returns else 0
    max_1s_vol = max(returns) if returns else 0
    
    # Spread stats
    spreads = [compute_spread_bps(b, a) for b, a in zip(bids, asks) if b > 0 and a > 0]
    avg_spread = sum(spreads) / len(spreads) if spreads else 0
    max_spread = max(spreads) if spreads else 0
    
    flag = '🔥' if max_up > 50 or max_down < -50 else '⏳' if max_up > 20 or max_down < -20 else '➡️'
    
    print(f"  {flag} {len(prices)} ticks in {total_time:.1f}s ({tick_rate:.1f}/s)")
    print(f"    Max up: +{max_up:.1f}bps, Max down: {max_down:.1f}bps")
    print(f"    Avg 1s vol: {avg_vol:.1f}bps, Max 1s vol: {max_1s_vol:.1f}bps")
    print(f"    Avg spread: {avg_spread:.0f}bps, Max spread: {max_spread:.0f}bps")
    print(f"    Price range: {min(prices):.8f} - {max(prices):.8f}")

print("\nDone.")
