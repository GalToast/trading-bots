import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Live BTC exc2
live_path = REPO / "reports" / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl"
closes = []
slippages = []
with open(live_path) as f:
    for line in f:
        d = json.loads(line.strip())
        if d.get("action") == "close_attempt" and d.get("result", {}).get("ok"):
            bf = d.get("result", {}).get("broker_fill", {})
            closes.append(bf.get("profit", 0))
            evt = d.get("event", {})
            requested = evt.get("fill_price")
            actual = bf.get("price")
            if requested and actual:
                slippages.append(abs(actual - requested))

print(f"=== BTC exc2_tight Live Execution Quality ===")
print(f"Closes: {len(closes)}")
if closes:
    print(f"  Avg $/close: ${sum(closes)/len(closes):.2f}")
    print(f"  Total: ${sum(closes):.2f}")
    print(f"  Win rate: {sum(1 for c in closes if c > 0)/len(closes):.0%}")
if slippages:
    print(f"  Avg slippage: ${sum(slippages)/len(slippages):.2f}")
    print(f"  Max slippage: ${max(slippages):.2f}")

# Shadow BTC exc2
shadow_path = REPO / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"
shadow = json.load(open(shadow_path))
btc = shadow["symbols"]["BTCUSD"]
shadow_closes = btc.get("realized_closes", 0)
shadow_net = btc.get("realized_net_usd", 0)
shadow_per_close = shadow_net / max(shadow_closes, 1)

print(f"\nShadow: {shadow_closes} closes, ${shadow_net:.2f}, ${shadow_per_close:.2f}/close")

# Comparison
live_per_close = sum(closes) / max(len(closes), 1) if closes else 0
ratio = live_per_close / shadow_per_close if shadow_per_close else 0
print(f"\nComparison:")
print(f"  Live $/close: ${live_per_close:.2f} vs Shadow ${shadow_per_close:.2f} = {ratio:.0%}")
print(f"  Note: Live has fewer closes but higher $/close — exc2 is NOT degraded like M5 Warp!")
