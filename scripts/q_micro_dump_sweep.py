import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()
duration = 60
dump_threshold_bps = 20  # Lower threshold for dead hours
lookback = 10

print(f"Micro-dump sweep: {duration}s, dump>{dump_threshold_bps}bps over {lookback} samples")
print("Scanning: CQT, BILLY, HONEY, VELVET, HOUSE, EPT, STRD")

targets = ['CQTUSD', 'BILLYUSD', 'HONEYUSD']
for product in targets:
    bid_history = []
    dumps = 0
    for i in range(duration):
        try:
            tk = c.ticker([product])
            t = tk.get(product, {})
            bid = to_float((t.get('b') or [None])[0])
            ask = to_float((t.get('a') or [None])[0])
            if bid <= 0 or ask <= 0:
                time.sleep(1)
                continue
            sp = compute_spread_bps(bid, ask)
            bid_history.append(bid)

            if len(bid_history) >= lookback:
                window = bid_history[-lookback:]
                peak = max(window[:-1])
                if peak > 0:
                    drop = (peak - bid) / peak * 100
                    if drop >= dump_threshold_bps:
                        dumps += 1
                        print(f"  💀 {product} t={i}s: {drop:.1f}bps drop spread={sp:.0f}bps")

            time.sleep(1)
        except:
            time.sleep(1)

    print(f"  {product}: {dumps} micro-dumps in {duration}s")

print("\nDone. Standing by for team direction.")
