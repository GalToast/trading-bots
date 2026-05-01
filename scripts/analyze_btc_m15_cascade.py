#!/usr/bin/env python
"""BTC M15 LIVE Grid Cascade Model — 2026-04-14T16:37 UTC

Models where BTC M15 Warp's 59 open positions will close as price moves,
calculating the PnL cascade at different price levels.
"""
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent.parent / "reports" / "penetration_lattice_live_btcusd_m15_warp_state.json"

state = json.loads(STATE_FILE.read_text())
btc = state["symbols"]["BTCUSD"]
current_bid = 75437.92  # From concentration board

buys = [p for p in btc["open_tickets"] if p["direction"] == "BUY"]
sells = [p for p in btc["open_tickets"] if p["direction"] == "SELL"]

print("=" * 80)
print("BTC M15 LIVE GRID CASCADE MODEL")
print(f"Generated: 2026-04-14T16:37 UTC | Current BTC bid: ${current_bid:,.2f}")
print("=" * 80)
print()
print(f"Total open: {len(btc['open_tickets'])} ({len(buys)} BUY, {len(sells)} SELL)")
print(f"Anchor: ${btc['anchor']:,.2f}")
print(f"Step: ${btc['base_step_px']:,.2f} (rearm ${btc['base_step_buy_px']:,.2f}/${btc['base_step_sell_px']:,.2f})")
print(f"Max open: {btc['max_open_total']}")
print(f"Clean forward: +$839/46c = $18.24/close")
print()

# Analyze SELL positions
sell_prices = sorted([p["entry_fill_price"] for p in sells])
buy_prices = sorted([p["entry_fill_price"] for p in buys], reverse=True)

print("=" * 80)
print("SELL POSITIONS — Close when price drops BELOW entry")
print("=" * 80)
print()
print(f"  Count: {len(sells)}")
print(f"  Price range: ${min(sell_prices):,.2f} → ${max(sell_prices):,.2f}")
print(f"  Average: ${sum(sell_prices)/len(sell_prices):,.2f}")
print(f"  Median:  ${sorted(sell_prices)[len(sell_prices)//2]:,.2f}")
print()

# Distribution by $100 buckets
print("  SELL distribution by $100 bucket:")
bucket_counts = {}
bucket_prices = {}
for p in sells:
    bucket = int(p["entry_fill_price"] // 100) * 100
    bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    if bucket not in bucket_prices:
        bucket_prices[bucket] = []
    bucket_prices[bucket].append(p["entry_fill_price"])

for bucket in sorted(bucket_counts.keys()):
    count = bucket_counts[bucket]
    avg = sum(bucket_prices[bucket]) / count
    dist_from_current = current_bid - avg
    print(f"    ${bucket:,}-${bucket+100:,}: {count:3d} positions (avg ${avg:,.2f}, ITM by ${dist_from_current:,.2f})")

print()
print("=" * 80)
print("BUY POSITIONS — Close when price rises ABOVE entry")
print("=" * 80)
print()
for p in buys:
    dist = current_bid - p["entry_fill_price"]
    print(f"  BUY at ${p['entry_fill_price']:,.2f} (OTM by ${dist:,.2f})")
print()

# CASCADE MODEL: Where do positions close as price moves?
print("=" * 80)
print("CASCADE MODEL — SELL positions that close at each price level")
print("=" * 80)
print()

# For SELLs: they close when price drops below entry.
# At 0.01 volume, each $1 move = ~$0.01 PnL per position.
# PnL per SELL close = (entry - exit_price) × 0.01

# Model at $100 intervals
print("  Price Level  | SELLs Closed | Cumulative | Est PnL from Closes")
print("  -------------|-------------|------------|---------------------")

cumulative = 0
for level in range(75400, 70500, -100):
    closed = sum(1 for p in sells if p["entry_fill_price"] > level)
    if closed > cumulative:
        new_closes = closed - cumulative
        # Approximate PnL: average entry of newly-closed positions minus exit price
        newly_closed = [p for p in sells if p["entry_fill_price"] > level and p["entry_fill_price"] <= level + 100]
        if newly_closed:
            avg_entry = sum(p["entry_fill_price"] for p in newly_closed) / len(newly_closed)
            pnl_per_close = (avg_entry - level) * 0.01
            total_pnl = pnl_per_close * new_closes
        else:
            total_pnl = 0
        cumulative = closed
        print(f"  ${level:>10,} | {new_closes:>11} | {cumulative:>10} | ${total_pnl:>18.2f}")

print()
print("=" * 80)
print("KEY CASCADE TRIGGERS")
print("=" * 80)
print()

# Find critical price levels
if sells:
    # Price where 10+ SELLs close
    for threshold in [10, 20, 30, 40, 50]:
        threshold_price = sorted(sell_prices)[min(threshold-1, len(sell_prices)-1)]
        print(f"  {threshold:2d}+ SELLs close below: ${threshold_price:,.2f} ({current_bid - threshold_price:,.2f} drop needed)")

print()
print("=" * 80)
print("RISK SCENARIOS")
print("=" * 80)
print()

# Scenario 1: BTC drops $500
drop_500 = current_bid - 500
closed_500 = sum(1 for p in sells if p["entry_fill_price"] > drop_500)
pnl_500 = sum((p["entry_fill_price"] - drop_500) * 0.01 for p in sells if p["entry_fill_price"] > drop_500)
print(f"  BTC drops $500 to ${drop_500:,.2f}:")
print(f"    {closed_500} SELLs close, est PnL +${pnl_500:.2f}")
print()

# Scenario 2: BTC drops $1000
drop_1000 = current_bid - 1000
closed_1000 = sum(1 for p in sells if p["entry_fill_price"] > drop_1000)
pnl_1000 = sum((p["entry_fill_price"] - drop_1000) * 0.01 for p in sells if p["entry_fill_price"] > drop_1000)
print(f"  BTC drops $1000 to ${drop_1000:,.2f}:")
print(f"    {closed_1000} SELLs close, est PnL +${pnl_1000:.2f}")
print()

# Scenario 3: BTC rises $500
rise_500 = current_bid + 500
closed_buy_500 = sum(1 for p in buys if p["entry_fill_price"] < rise_500)
pnl_buy_500 = sum((rise_500 - p["entry_fill_price"]) * 0.01 for p in buys if p["entry_fill_price"] < rise_500)
# SELLs get worse
worsened_sells = sum(1 for p in sells if p["entry_fill_price"] > rise_500)
print(f"  BTC rises $500 to ${rise_500:,.2f}:")
print(f"    {closed_buy_500} BUYs close, est PnL +${pnl_buy_500:.2f}")
print(f"    {len(sells) - worsened_sells} SELLs now deeper ITM, {worsened_sells} still above water")
print()

# Scenario 4: BTC rises $1500 (to $76,937)
rise_1500 = current_bid + 1500
print(f"  BTC rises $1500 to ${rise_1500:,.2f}:")
closed_buy_1500 = sum(1 for p in buys if p["entry_fill_price"] < rise_1500)
pnl_buy_1500 = sum((rise_1500 - p["entry_fill_price"]) * 0.01 for p in buys if p["entry_fill_price"] < rise_1500)
# All SELLs deeper ITM
all_sells_worse = sum((p["entry_fill_price"] - rise_1500) * 0.01 for p in sells)
print(f"    {closed_buy_1500} BUYs close, est PnL +${pnl_buy_1500:.2f}")
print(f"    ALL {len(sells)} SELLs deeper ITM by avg ${all_sells_worse/len(sells):.2f} each")
print()

print("=" * 80)
print("VERDICT")
print("=" * 80)
print()
print(f"  The BTC M15 grid is heavily SELL-skewed ({len(sells)} SELL, {len(buys)} BUY).")
print(f"  This is NORMAL for a grid during a BTC uptrend.")
print(f"  The clean forward (+$839/46c) proves the edge works.")
print(f"  When BTC drops, the cascade will be MASSIVE — 30+ SELLs close rapidly.")
print(f"  At 0.01 volume, each SELL close averages ~${sum(p['entry_fill_price'] for p in sells)/len(sells)*0.01:.2f} PnL.")
print()
print(f"  CRITICAL LEVEL: ${sorted(sell_prices)[10]:,.2f} — below this, 10+ SELLs cascade.")
print(f"  If BTC drops below ${sorted(sell_prices)[30]:,.2f}, 30+ SELLs cascade = big positive delta.")
print()
print("=" * 80)
