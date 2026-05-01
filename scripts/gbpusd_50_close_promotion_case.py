#!/usr/bin/env python3
"""GBPUSD tick-forward 50-close promotion pre-analysis.

Builds the case for whether GBPUSD 0.5/1.0 gap 1/3 should be promoted
to live FX trading based on its first 50 durable forward closes.

Usage: python scripts/gbpusd_50_close_promotion_case.py
"""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "reports" / "shadow_gbpusd_tick_forward_state.json"
EVENTS_FILE = ROOT / "reports" / "shadow_gbpusd_tick_forward_events.jsonl"

print("=" * 70)
print("GBPUSD TICK-FORWARD — 50-CLOSE PROMOTION ANALYSIS")
print("=" * 70)

# Load state
if not STATE_FILE.exists():
    print("\nERROR: State file not found")
    exit(1)

state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
dp = state.get("durable_proof", {})
runner = state.get("runner", {})

# Load events
closes = []
opens = []
if EVENTS_FILE.exists():
    with open(EVENTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("action") == "close":
                    closes.append(e)
                elif e.get("action") == "open":
                    opens.append(e)
            except:
                pass

dc = dp.get("durable_realized_closes", len(closes))
dn = dp.get("durable_realized_net_usd", sum(c.get("net_usd", 0) for c in closes))
doc = dp.get("durable_open_count", 0)

print(f"\nCurrent status: {dc}/50 closes ({dc/50*100:.0f}%)")
print(f"  Durable net: ${dn:+.2f}")
print(f"  Durable open count: {doc}")
print(f"  Event log closes: {len(closes)}")
print(f"  Event log opens: {len(opens)}")

if len(closes) < 3:
    print("\n  Need at least 3 closes for meaningful analysis.")
    print(f"  Have {len(closes)}. Waiting for more...")
    exit(0)

# Performance metrics
nets = [c.get("net_usd", 0) for c in closes]
winners = [n for n in nets if n > 0]
losers = [n for n in nets if n <= 0]
wr = len(winners) / len(nets) * 100 if nets else 0
avg_win = sum(winners) / len(winners) if winners else 0
avg_loss = sum(losers) / len(losers) if losers else 0
avg_close = sum(nets) / len(nets) if nets else 0
total_volume = sum(abs(n) for n in nets)

# Direction breakdown
buy_closes = [c for c in closes if c.get("direction") == "BUY"]
sell_closes = [c for c in closes if c.get("direction") == "SELL"]
buy_wr = len([c for c in buy_closes if c.get("net_usd", 0) > 0]) / len(buy_closes) * 100 if buy_closes else 0
sell_wr = len([c for c in sell_closes if c.get("net_usd", 0) > 0]) / len(sell_closes) * 100 if sell_closes else 0

# Time analysis
timestamps = []
for c in closes:
    ts = c.get("ts_utc", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            timestamps.append(dt)
        except:
            pass

closes_per_hour = len(closes) / ((max(timestamps) - min(timestamps)).total_seconds() / 3600) if len(timestamps) > 1 else 0

print("\n" + "-" * 70)
print("PERFORMANCE METRICS:")
print("-" * 70)
print(f"  Total closes: {len(closes)}")
print(f"  Win rate: {wr:.1f}%")
print(f"  Winners: {len(winners)} ({avg_win:+.4f} avg)")
print(f"  Losers: {len(losers)} ({avg_loss:+.4f} avg)")
print(f"  Avg close: ${avg_close:+.4f}")
print(f"  Total net: ${sum(nets):+.2f}")
print(f"  Total volume: ${total_volume:.2f}")

print(f"\n  Direction breakdown:")
print(f"    BUY closes: {len(buy_closes)}, WR={buy_wr:.1f}%")
print(f"    SELL closes: {len(sell_closes)}, WR={sell_wr:.1f}%")

print(f"\n  Timing:")
print(f"    First close: {timestamps[0].isoformat() if timestamps else 'N/A'}")
print(f"    Last close: {timestamps[-1].isoformat() if timestamps else 'N/A'}")
print(f"    Closes/hour: {closes_per_hour:.1f}")

# Open positions risk
inventory = dp.get("inventory_last_seen_at", "?")
open_count = dp.get("durable_open_count", 0)
current_open = len(state.get("symbols", {}).get("GBPUSD", {}).get("open_tickets", []))
print(f"\n  Open positions:")
print(f"    Durable open count: {open_count}")
print(f"    Current open tickets: {current_open}")
print(f"    Inventory last seen: {inventory}")

# Compare to backtest
print(f"\n" + "-" * 70)
print("BACKTEST COMPARISON:")
print("-" * 70)
print(f"  Backtest (historical):")
print(f"    GBPUSD gap1 alpha=1.0: ~$43K/60d")
print(f"    Per-trade avg: varies by spread regime")
print(f"  Forward (this proof):")
print(f"    Avg close: ${avg_close:+.4f}")
print(f"    Projected 60d (at {closes_per_hour:.1f} closes/hour): ${sum(nets)*24*60/closes_per_hour:+.2f}" if closes_per_hour > 0 else "    Projected 60d: N/A (insufficient timing data)")

# Recommendation
print(f"\n" + "-" * 70)
print("PROMOTION RECOMMENDATION:")
print("-" * 70)

if dc < 50:
    remaining = 50 - dc
    eta_hours = remaining / closes_per_hour if closes_per_hour > 0 else "?"
    print(f"  STATUS: {remaining} closes remaining to 50-close milestone")
    print(f"  ETA: ~{eta_hours:.1f} hours" if isinstance(eta_hours, float) else "  ETA: N/A")
    print(f"  Recommendation: WAIT for 50 closes before promotion decision")
elif wr >= 55 and sum(nets) > 0:
    print(f"  ✅ STRONG BUY — Promote to live FX probation")
    print(f"  WR {wr:.1f}% > 55%, net ${sum(nets):+.2f} > 0")
    print(f"  Recommend: launch live GBPUSD with same params, cap at 0.01 lots probation")
elif wr >= 50 and sum(nets) > 0:
    print(f"  ⚠️  CONDITIONAL — Extend to 100 closes for more evidence")
    print(f"  WR {wr:.1f}% >= 50%, net positive but borderline")
    print(f"  Need more closes to be confident in edge durability")
else:
    print(f"  ❌ REJECT — Edge not durable in forward proof")
    print(f"  WR {wr:.1f}%, net ${sum(nets):+.2f}")
    print(f"  Recommend: do not promote, investigate why forward differs from backtest")

print(f"\n{'=' * 70}")
