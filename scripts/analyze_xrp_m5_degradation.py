#!/usr/bin/env python
"""XRP M5 Degradation Analysis — 2026-04-14T16:05 UTC

Investigates the XRP M5 Warp lane which showed positive edge (19c, +net)
but now shows negative clean forward after restart.
"""

# ============================================================
# XRP M5 WARP — DEGRADATION ANALYSIS
# Generated: 2026-04-14T16:05 UTC
# ============================================================

print("=" * 70)
print("XRP M5 WARP — DEGRADATION ANALYSIS")
print("=" * 70)
print()

# Pre-restart vs post-restart
pre_closes = 19
pre_net = 57.73  # Inferred: total $13 - (-$44.73) = $57.73
pre_per_close = pre_net / pre_closes

post_closes = 6
post_net = -44.73
post_per_close = post_net / post_closes

total_closes = 25
total_net = 13.00
total_per_close = total_net / total_closes

print("SAMPLE BREAKDOWN:")
print("-" * 70)
print(f"  Pre-restart (19 closes):  +${pre_net:>8.2f}  (${pre_per_close:>7.2f}/close)")
print(f"  Post-restart (6 closes):  ${post_net:>8.2f}  (${post_per_close:>7.2f}/close)")
print(f"  TOTAL (25 closes):        ${total_net:>8.2f}  (${total_per_close:>7.2f}/close)")
print()

# Comparison to other M5 lanes
print("COMPARISON TO OTHER M5 LANES:")
print("-" * 70)
m5_lanes = [
    ("BTC M5 LIVE", 41, 878.44, "S+"),
    ("BTC M5 Shadow", 69, 156.25, "S+"),
    ("ETH M5 $5", 17, 35.76, "A"),
    ("ETH M5 Wide", 15, 34.56, "A"),
    ("SOL M5", 9, 11.90, "B"),
    ("XRP M5 (pre-restart)", 19, 57.73, "was B"),
    ("XRP M5 (post-restart)", 6, -44.73, "DEGRADED"),
]
for name, c, net, tier in m5_lanes:
    pc = net / max(c, 1)
    print(f"  {name:25s} {c:>3}c  ${net:>8.2f}  ${pc:>7.2f}/c  [{tier}]")
print()

# Assessment
print("ASSESSMENT:")
print("-" * 70)
print(f"  1. Pre-restart XRP M5 was +${pre_per_close:.2f}/close (19c) — solid B-tier")
print(f"  2. Post-restart is -${abs(post_per_close):.2f}/close (6c) — concerning")
print(f"  3. BUT: 6 closes is VERY small sample. Std error ≈ ${abs(post_per_close)/6**0.5:.2f}")
print(f"  4. XRP M5 total is still +${total_per_close:.2f}/close (25c) — barely positive")
print()

# Possible causes
print("POSSIBLE CAUSES:")
print("-" * 70)
print("  1. Regime shift: XRP volatility changed post-restart")
print("  2. Small sample noise: 6 closes is not enough to declare edge dead")
print("  3. max_floating_loss_usd=$10 may be too tight for XRP's $0.0016 step")
print("     Each step = ~$0.16. $10 max = 62 steps adverse. Seems OK.")
print("  4. Close event gap (6) suggests events aren't being written properly")
print()

# Recommendation
print("RECOMMENDATION:")
print("-" * 70)
print("  ⚠️  WATCH — do NOT kill yet. 6 closes is too few to declare edge dead.")
print("  📊 Monitor for 20 more closes. If post-restart stays negative → kill.")
print("  🔧 Check if close_event_gap is a bug (events not being written).")
print("  📉 If XRP M5 turns net negative overall → demote from validated edges.")
print()
print("=" * 70)
