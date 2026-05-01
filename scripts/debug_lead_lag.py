#!/usr/bin/env python3
"""Debug: why are there zero trades in the lead-lag simulator?"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

LEADERS = ["BTC-USD", "ETH-USD"]
LAGGERS = ["RAVE-USD", "IOTX-USD", "BAL-USD"]

candles_data = {}
for pid in LEADERS + LAGGERS:
    candles = load_candles(pid, "ONE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
    if candles:
        candles_data[pid] = candles

# Check BTC spikes
btc_closes = [float(c["close"]) for c in candles_data["BTC-USD"]]
btc_returns = [(btc_closes[i] - btc_closes[i-1]) / btc_closes[i-1] * 100 for i in range(1, len(btc_closes))]

spikes = [(i, r) for i, r in enumerate(btc_returns) if abs(r) > 0.2]
print(f"BTC: {len(btc_returns)} returns, {len(spikes)} spikes (>0.2%)")
if spikes:
    print(f"  First spike at index {spikes[0][0]}: {spikes[0][1]:.3f}%")
    print(f"  Last spike at index {spikes[-1][0]}: {spikes[-1][1]:.3f}%")

# Check RAVE data at those spike indices
rave_candles = candles_data["RAVE-USD"]
rave_opens = [float(c["open"]) for c in rave_candles]
rave_closes = [float(c["close"]) for c in rave_candles]

print(f"\nRAVE: {len(rave_candles)} candles")

# For first 5 BTC spikes, check if we can enter RAVE
for spike_idx, spike_ret in spikes[:10]:
    entry_bar = spike_idx + 1  # next_bar mode
    if entry_bar >= len(rave_opens):
        print(f"  Spike {spike_idx}: entry_bar {entry_bar} >= RAVE len {len(rave_opens)} → SKIP")
        continue

    entry_price = rave_opens[entry_bar]
    if entry_price == 0:
        print(f"  Spike {spike_idx}: entry_bar {entry_bar} → RAVE open=0 → SKIP")
        continue

    # Check next 5 bars for exit
    has_valid_exit = False
    for b in range(1, 6):
        exit_idx = entry_bar + b
        if exit_idx >= len(rave_closes):
            break
        exit_price = rave_closes[exit_idx]
        if exit_price > 0:
            has_valid_exit = True
            break

    status = "OK" if has_valid_exit else "NO VALID EXIT"
    print(f"  Spike {spike_idx} (BTC {spike_ret:+.3f}%): entry_bar={entry_bar}, RAVE open=${entry_price:.4f} → {status}")

# Also check: how many RAVE bars have open=0?
zero_opens = sum(1 for o in rave_opens if o == 0)
print(f"\nRAVE zero opens: {zero_opens}/{len(rave_opens)} ({zero_opens/len(rave_opens)*100:.1f}%)")

# Check the alignment issue
min_len = min(len(candles_data[pid]) for pid in candles_data)
print(f"\nMin aligned length across all products: {min_len}")
print(f"  BTC: {len(candles_data['BTC-USD'])}")
print(f"  ETH: {len(candles_data['ETH-USD'])}")
print(f"  RAVE: {len(candles_data['RAVE-USD'])}")
print(f"  IOTX: {len(candles_data.get('IOTX-USD', []))}")
print(f"  BAL: {len(candles_data.get('BAL-USD', []))}")
