"""
GBPUSD Tick-Forward Floating Risk Analysis
Analyzes the 41 SELL open positions and assesses risk levels.
"""
import json
from datetime import datetime, timezone

state = json.load(open('reports/shadow_gbpusd_tick_forward_state.json'))
gbp = state['symbols']['GBPUSD']

anchor = gbp['anchor']
opens = gbp['open_tickets']
closes = gbp['realized_closes']
net = gbp['realized_net_usd']

if isinstance(closes, int):
    close_count = closes
else:
    close_count = len(closes)

per_close = net / close_count if close_count > 0 else 0

# Analyze open positions
sell_levels = []
buy_levels = []
for t in opens:
    entry = t.get('entry_fill_price', t.get('entry_price', 0))
    direction = t.get('direction', '')
    if direction == 'SELL':
        sell_levels.append(entry)
    elif direction == 'BUY':
        buy_levels.append(entry)

sell_levels.sort()
buy_levels.sort()

# Calculate floating PnL
# For FX with 0.01 lots, each pip = $0.01 per position
# GBPUSD is quoted as 1.34748, so a 1.00 move = $1 per 0.01 lot
# Actually for micro lots (0.01), 1 pip (0.0001) = $0.001
# But the state file doesn't specify volume, so let's estimate from PnL

# Floating PnL for SELL positions: (entry - current) * volume_multiplier
# We know net floating is approximately -$108 (from execution monitor marked value)
# So: sum(entry - current) * multiplier = -$108
# sum(entry - 1.34748) * multiplier = -$108

sell_drift = sum(s - anchor for s in sell_levels)
buy_drift = sum(anchor - b for b in buy_levels) if buy_levels else 0
total_drift = sell_drift + buy_drift

# Estimate volume multiplier from known floating
# marked floating = -$108.35 (from execution monitor)
marked_floating = -108.35
if total_drift != 0:
    multiplier = marked_floating / total_drift
else:
    multiplier = 0

floating_pnl = total_drift * multiplier
floating_ratio = abs(floating_pnl) / abs(net) if net != 0 else float('inf')

# Breakeven calculation
# For SELL positions: breakeven when anchor = weighted average entry
if sell_levels:
    avg_sell_entry = sum(sell_levels) / len(sell_levels)
    breakeven_anchor = avg_sell_entry  # approximate
else:
    breakeven_anchor = None

# Max floating scenarios
scenarios = []
for move_pips in [-50, -30, -20, -10, 0, 10, 20, 30, 50, 100]:
    new_anchor = anchor + move_pips * 0.0001
    pnl = sum(s - new_anchor for s in sell_levels) * multiplier
    pnl += sum(new_anchor - b for b in buy_levels) * multiplier if buy_levels else 0
    net_with_floating = net + pnl
    scenarios.append((move_pips, new_anchor, pnl, net_with_floating))

# Report
print("=" * 70)
print(f"GBPUSD Tick-Forward Floating Risk Analysis")
print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("=" * 70)

print(f"\n--- Current State ---")
print(f"Anchor: {anchor:.5f}")
print(f"Realized: ${net:.2f} ({close_count} closes, ${per_close:.2f}/close)")
print(f"Open positions: {len(opens)} ({len(buy_levels)} BUY, {len(sell_levels)} SELL)")
print(f"Marked floating: ${marked_floating:.2f}")
print(f"Floating/Realized ratio: {floating_ratio:.1f}x")

print(f"\n--- SELL Position Levels ({len(sell_levels)} total) ---")
if sell_levels:
    print(f"  Lowest:  {min(sell_levels):.5f}")
    print(f"  Highest: {max(sell_levels):.5f}")
    print(f"  Average: {sum(sell_levels)/len(sell_levels):.5f}")
    print(f"  Levels: {sell_levels[:10]}{'...' if len(sell_levels) > 10 else ''}")

print(f"\n--- BUY Position Levels ({len(buy_levels)} total) ---")
if buy_levels:
    print(f"  Lowest:  {min(buy_levels):.5f}")
    print(f"  Highest: {max(buy_levels):.5f}")
else:
    print(f"  None")

print(f"\n--- Scenario Analysis (GBP price moves) ---")
print(f"  {'Move (pips)':<12} {'Anchor':<10} {'Floating PnL':<14} {'Net PnL':<12}")
print(f"  {'-'*12} {'-'*10} {'-'*14} {'-'*12}")
for pips, new_anchor, pnl, net_pnl in scenarios:
    print(f"  {pips:+11} {new_anchor:<10.5f} ${pnl:<13.2f} ${net_pnl:<11.2f}")

print(f"\n--- Risk Assessment ---")
if floating_ratio > 10:
    print(f"⚠️  CRITICAL: Floating/realized ratio {floating_ratio:.1f}x exceeds 10x threshold")
    print(f"   The lane has accumulated significant counter-trend inventory")
elif floating_ratio > 5:
    print(f"⚠️  WARNING: Floating/realized ratio {floating_ratio:.1f}x exceeds 5x threshold")
else:
    print(f"✅ OK: Floating/realized ratio {floating_ratio:.1f}x is within safe bounds")

if len(sell_levels) > 30:
    print(f"⚠️  HIGH EXPOSURE: {len(sell_levels)} SELL positions open")
    print(f"   The uptrend has filled many SELL levels without closing")

if breakeven_anchor:
    print(f"\n--- Breakeven Analysis ---")
    print(f"   Approximate breakeven anchor: {breakeven_anchor:.5f}")
    print(f"   Current anchor: {anchor:.5f}")
    print(f"   Distance to breakeven: {(breakeven_anchor - anchor) * 10000:.1f} pips")

print(f"\n--- Recommendations ---")
if floating_ratio > 10:
    print(f"1. MONITOR CLOSELY: The floating risk is large relative to realized gains")
    print(f"2. If GBPUSD continues up, floating will worsen linearly")
    print(f"3. If GBPUSD drops, the grid will close SELLs profitably")
    print(f"4. Consider: widening SELL step to reduce future accumulation")
    print(f"5. Set alert: if floating exceeds -$200, consider intervention")
else:
    print(f"1. Continue monitoring, current risk is manageable")
    print(f"2. The positive realized PnL provides buffer against floating losses")

# Save analysis
analysis = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'anchor': anchor,
    'realized_net': net,
    'close_count': close_count,
    'per_close': per_close,
    'open_count': len(opens),
    'sell_open': len(sell_levels),
    'buy_open': len(buy_levels),
    'marked_floating': marked_floating,
    'floating_ratio': floating_ratio,
    'sell_levels': sell_levels,
    'buy_levels': buy_levels,
    'scenarios': [
        {'move_pips': p, 'anchor': a, 'floating_pnl': f, 'net_pnl': n}
        for p, a, f, n in scenarios
    ],
    'recommendation': 'monitor' if floating_ratio < 10 else 'intervention_consider'
}

with open('reports/gbpusd_floating_risk_analysis.json', 'w') as f:
    json.dump(analysis, f, indent=2)

print(f"\n✅ Analysis saved to reports/gbpusd_floating_risk_analysis.json")
