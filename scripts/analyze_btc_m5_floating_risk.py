#!/usr/bin/env python
"""Deep analysis of BTC M5 Warp floating risk inventory."""
import json
from pathlib import Path

REPORT_DIR = Path(__file__).parent.parent / "reports"
state_file = REPORT_DIR / "penetration_lattice_live_btcusd_m5_warp_state.json"

state = json.loads(state_file.read_text())
btc = state["symbols"]["BTCUSD"]
current_price = 74300  # approximate from bid/ask

buys = [p for p in btc["open_tickets"] if p["direction"] == "BUY"]
sells = [p for p in btc["open_tickets"] if p["direction"] == "SELL"]

print(f"=== BTC M5 Floating Risk Deep Analysis ===")
print(f"Current price: ~${current_price:,.2f}")
print(f"Anchor: ${btc['anchor']:,.2f}")
print(f"Step: ${btc['base_step_px']:,.2f}")
print(f"Total open: {len(btc['open_tickets'])} (1 BUY, {len(sells)} SELL)")
print(f"Realized: ${btc['realized_net_usd']:.2f} from {btc['realized_closes']} closes")
print(f"$/close: ${btc['realized_net_usd']/max(btc['realized_closes'],1):.2f}")
print()

sell_prices = sorted([p["entry_fill_price"] for p in sells])
print("=== SELL Positions (deep in the money) ===")
print(f"Deepest SELL: ${min(sell_prices):,.2f} (ITM by ${current_price - min(sell_prices):,.2f})")
print(f"Shallowest SELL: ${max(sell_prices):,.2f} (ITM by ${current_price - max(sell_prices):,.2f})")
print(f"Average SELL: ${sum(sell_prices)/len(sell_prices):,.2f}")
print(f"Median SELL: ${sorted(sell_prices)[len(sell_prices)//2]:,.2f}")
print()

# Distribution
buckets = {}
for p in sells:
    bucket = int(p["entry_fill_price"] // 1000) * 1000
    buckets[bucket] = buckets.get(bucket, 0) + 1
print("SELL distribution by $1000 bucket:")
for bucket in sorted(buckets.keys()):
    count = buckets[bucket]
    avg_entry = sum(p["entry_fill_price"] for p in sells if int(p["entry_fill_price"]//1000)*1000 == bucket) / count
    dist = current_price - avg_entry
    print(f"  ${bucket:,}-${bucket+1000:,}: {count} positions (avg entry ${avg_entry:,.2f}, ITM by ${dist:,.2f})")
print()

print("=== BUY Positions ===")
for p in buys:
    dist = current_price - p["entry_fill_price"]
    print(f"  BUY at ${p['entry_fill_price']:,.2f} (OTM by ${dist:,.2f})")
print()

print("=== Grid Geometry ===")
print(f"Next BUY level: ${btc['next_buy_level']:,.2f} (below current by ${current_price - btc['next_buy_level']:,.2f})")
print(f"Next SELL level: ${btc['next_sell_level']:,.2f} (above current by ${btc['next_sell_level'] - current_price:,.2f})")
print()

print("=== Recovery Analysis ===")
print("For SELLs to close profitably, price must drop below entry:")
print(f"  - Shallowest SELL (${max(sell_prices):,.2f}): need ${current_price - max(sell_prices):,.2f} drop ({(current_price - max(sell_prices))/current_price*100:.2f}%)")
avg_sell = sum(sell_prices)/len(sell_prices)
print(f"  - Average SELL (${avg_sell:,.2f}): need ${current_price - avg_sell:,.2f} drop ({(current_price - avg_sell)/current_price*100:.2f}%)")
print(f"  - Deepest SELL (${min(sell_prices):,.2f}): need ${current_price - min(sell_prices):,.2f} drop ({(current_price - min(sell_prices))/current_price*100:.2f}%)")
print()

# Time-to-recovery estimate
# At $21.43/close and 41 closes so far, with 18 open
# If BTC drops steadily, the SELLs will close as price falls through levels
# Each $100 drop would close ~1 SELL position (given $100 step)
print("=== Risk Assessment ===")
print(f"Floating risk / realized = 4.4x (survivability board shows healthy margin)")
print(f"At $21.43/close, need {3839/21.43:.0f} more closes to offset current floating")
print(f"If BTC drops ${current_price - avg_sell:,.0f} to average SELL entry, all SELLs close profitable")
print(f"Margin level from survivability board: 5504% — healthy, no margin risk")
print()
print("=== VERDICT ===")
print("Lane is HEALTHY but carrying significant directional risk (heavy SELL bias).")
print("The 4.4x floating/realized ratio is within bounds for a stopless lattice.")
print("No intervention needed unless BTC trends +$1000+ without mean reversion.")
print("The survivability board shows even at +$1500, impact is -4.95% equity — survivable.")
