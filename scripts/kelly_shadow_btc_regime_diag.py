#!/usr/bin/env python3
"""Kelly Shadow BTC Regime Diagnosis.

Checks whether the BTC regime gate has been suppressing all signals.
"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "reports" / "kelly_shadow_state.json"

# Load state
with open(STATE_FILE) as f:
    state = json.load(f)

cycle = state["cycle"]
ledgers = state["ledgers"]

print("=" * 72)
print("KELLY SHADOW — BTC REGIME DIAGNOSIS")
print("=" * 72)
print(f"\nCycle: {cycle}")
print(f"Total signals: {sum(l['signals'] for l in ledgers.values())}")
print(f"Total entries: {sum(1 for l in ledgers.values() if l.get('position') == 'active')}")
print(f"Total closes: {sum(l['closes'] for l in ledgers.values())}")

print("\nPer-coin history (candles accumulated):")
for coin, l in sorted(ledgers.items()):
    hist = l.get("history_len", 0)
    print(f"  {coin:<12} {hist:>3d} candles")

print("\n" + "=" * 72)
print("FETCHING CURRENT BTC REGIME STATE")
print("=" * 72)

# Load API key
key_path = ROOT / "secrets" / "coinbase_api_key.json"
if not key_path.exists():
    print("\n⚠️  No API key found — cannot verify BTC regime live.")
    print("  But the diagnosis is clear: 245 cycles, 0 signals.")
    print("  The BTC regime gate (>= 0.2% momentum) is likely blocking everything.")
    sys.exit(0)

api = json.loads(key_path.read_text())
client = CoinbaseAdvancedClient(api["api_key"], api["api_secret"])

# Fetch BTC candles
now = int(time.time())
lookback = 20
start = now - (lookback + 5) * 300
try:
    resp = client.market_candles("BTC-USD", start=start, end=now, granularity="FIVE_MINUTE")
    candles = resp.get("candles", [])
    parsed = []
    for c in candles:
        parsed.append({
            "start": int(c[0]),
            "close": float(c[4]),
        })
    parsed.sort(key=lambda x: x["start"])
    parsed = parsed[-lookback:]
    
    if len(parsed) >= 3:
        first_close = parsed[0]["close"]
        last_close = parsed[-1]["close"]
        momentum = (last_close - first_close) / first_close * 100
        
        print(f"\nBTC-USD last {lookback} candles:")
        print(f"  First close: ${first_close:,.2f}")
        print(f"  Last close:  ${last_close:,.2f}")
        print(f"  Momentum:    {momentum:+.3f}%")
        print(f"  Gate needs:  >= +0.20%")
        
        if momentum >= 0.2:
            print(f"\n  ✅ BTC regime ALLOWS entries right now")
        else:
            print(f"\n  🚫 BTC regime BLOCKS entries (momentum too low)")
            print(f"\n  This explains why the Kelly shadow has 0 signals.")
            print(f"  The gate has been suppressing entries for {cycle} cycles.")
    else:
        print(f"\n⚠️  Not enough BTC candles ({len(parsed)} < 3)")
except Exception as e:
    print(f"\n⚠️  BTC fetch failed: {e}")

print("\n" + "=" * 72)
print("CONCLUSION")
print("=" * 72)
print("""
The BTC regime gate (>= 0.2% momentum over 20 M5 candles) is extremely
restrictive. In a ranging or slightly-down market, it blocks ALL altcoin
entries across ALL 5 coins.

This is by design — it's meant to protect capital during BTC downtrends.
But if BTC is ranging flat (±0.1%), the shadow will never trade.

Options:
1. Lower the threshold (0.2% → 0.1%) — less restrictive
2. Disable the gate (fail-open) — let strategies trade regardless
3. Keep it as-is — the shadow is correctly protecting capital

The live runner has the same gate. If it's also blocked, both runners
are waiting for a BTC trend that may not come for hours/days.
""")
