#!/usr/bin/env python3
"""Analyze BTC SELL trigger missed open pattern.

Quantifies:
1. How many SELL triggers were missed per lane
2. Average price of missed triggers
3. Estimated alpha lost per miss
4. Total bleed across all lanes
5. Whether shared tick cache would have helped

Data source: trade-firing-guard switchboard messages
"""
import json
from datetime import datetime, timezone

# Missed SELL triggers from trade-firing-guard (01:07-02:22 UTC)
# All recovered, but quantifying the alpha gap
missed_sells = [
    # (lane, trigger_price, age_s, time_utc)
    ("live_btcusd_exc2_tight_941779", 74167.00, 77.2, "01:45"),
    ("live_btcusd_exc2_tight_941779", 74455.51, 77.2, "01:55"),
    ("live_btcusd_m5_warp_probation_941780", 74420.15, 87.0, "01:55"),
    ("live_btcusd_exc2_tight_941779", 74408.01, 165.9, "01:59"),
    ("live_btcusd_exc2_tight_941779", 74500.51, 122.0, "02:10"),
    ("live_btcusd_exc2_tight_941779", 74500.51, 182.1, "02:11"),
    ("live_btcusd_m5_warp_probation_941780", 74420.15, 120.0, "02:14"),
    ("live_btcusd_m5_warp_probation_941780", 74420.15, 180.0, "02:15"),
    ("live_btcusd_exc2_tight_941779", 74561.01, 84.5, "02:21"),
    # Shadow lanes (also affected)
    ("shadow_btcusd_h1_step30", 74376.51, 202.1, "01:56"),
    ("shadow_btcusd_h1_step30", 74376.51, 320.6, "01:58"),
    ("shadow_btcusd_h1_step30", 74436.51, 156.2, "02:13"),
    ("shadow_btcusd_h1_step30", 74436.51, 336.2, "02:16"),
]

# BUY triggers (for comparison - these were NOT missed systematically)
missed_buys = [
    ("live_btcusd_exc2_tight_941779", 74187.00, 119.5, "00:29"),
    ("live_btcusd_m5_warp_probation_941780", 74020.00, 76.3, "01:45"),
    ("shadow_usdjpy_gap2", 159.41, 197.6, "00:27"),
    ("shadow_usdjpy_shallow03", 159.41, 197.6, "00:27"),
]

print("=" * 70)
print("BTC SELL TRIGGER MISSED OPEN ANALYSIS")
print("=" * 70)
print()

# Group by lane
lanes = {}
for lane, price, age, time in missed_sells:
    if lane not in lanes:
        lanes[lane] = []
    lanes[lane].append((price, age, time))

print("MISSED SELL TRIGGERS BY LANE:")
print("-" * 70)
total_sells = 0
total_buy_sells = 0
for lane, triggers in sorted(lanes.items()):
    total_sells += len(triggers)
    prices = [t[0] for t in triggers]
    ages = [t[1] for t in triggers]
    avg_price = sum(prices) / len(prices)
    max_price = max(prices)
    min_price = min(prices)
    avg_age = sum(ages) / len(ages)
    
    short_name = lane.replace("live_btcusd_", "").replace("_probation_941780", "").replace("_941779", "")
    print(f"\n  {short_name}: {len(triggers)} missed SELLs")
    print(f"    Price range: ${min_price:.2f} - ${max_price:.2f} (avg ${avg_price:.2f})")
    print(f"    Avg age: {avg_age:.1f}s")
    print(f"    Times: {', '.join(t[2] for t in triggers)}")
    
    # Estimate alpha lost: each missed SELL is a grid level not captured
    # For exc2_tight (step=$45), each level = ~$45 potential
    # For M5 warp (step=$100), each level = ~$100 potential
    if "exc2" in lane:
        step = 45
    elif "m5_warp" in lane:
        step = 100
    elif "h1_step" in lane:
        step = 30
    else:
        step = 50
    
    estimated_lost = len(triggers) * step * 0.01  # 0.01 lots
    total_buy_sells += estimated_lost
    print(f"    Est. alpha lost: ~${estimated_lost:.2f} (step=${step}, {len(triggers)} levels)")

print()
print("=" * 70)
print(f"TOTAL MISSED SELL TRIGGERS: {total_sells}")
print(f"ESTIMATED TOTAL ALPHA BLEED: ~${total_buy_sells:.2f}")
print()

# Comparison with BUY triggers
print("BUY TRIGGERS (for comparison):")
print("-" * 70)
print(f"  Total missed BUYs: {len(missed_buys)}")
print(f"  (Mostly isolated incidents, not systematic)")
print()

# Pattern analysis
print("PATTERN ANALYSIS:")
print("-" * 70)
sell_prices = [t[1] for t in missed_sells]  # price is index 1 in tuple
print(f"  BTC rally range: ${min(sell_prices):.2f} -> ${max(sell_prices):.2f}")
print(f"  Rally magnitude: ${max(sell_prices) - min(sell_prices):.2f}")
print(f"  SELL triggers missed: {total_sells}")
print(f"  BUY triggers missed: {len(missed_buys)}")
print(f"  SELL:BUY ratio: {total_sells/len(missed_buys):.1f}x")
print()

# Root cause hypothesis
print("ROOT CAUSE HYPOTHESIS (@qwen-main):")
print("-" * 70)
print("  During strong rallies, SELL rearm tokens are depleted faster")
print("  than they regenerate. The fixed grid can't keep up with")
print("  price movement, causing systematic SELL misses.")
print()

# Would shared tick cache help?
print("WOULD SHARED TICK CACHE HELP?")
print("-" * 70)
print("  @codex-manual's bounded shared recent-tick history provides")
print("  fresh tick data to lanes. BUT the issue is rearm token")
print("  exhaustion, not stale prices. The shared cache would help")
print("  with polling latency but NOT with the fundamental grid")
print("  coverage gap during trends.")
print()
print("  Real fix: Dynamic rearm regeneration during trends, or")
print("  wider rearm spacing to reduce token exhaustion rate.")
print("=" * 70)
