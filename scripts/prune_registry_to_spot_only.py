import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"

SPOT_LANES = {
    "shadow_kraken_maker_machinegun",
    "shadow_kraken_frontier_taker",
    "shadow_coinbase_strict_maker_rsi",
    "shadow_coinbase_arbusd_rsi7",
    "shadow_coinbase_lighterusd_rsi7",
    "shadow_coinbase_vvvusd_rsi7",
    "shadow_coinbase_prlusd_rsi7",
    "shadow_coinbase_fartcoinusd_rsi7",
    "shadow_coinbase_raveusd_rsi7",
    "shadow_coinbase_a8usd_rsi4",
    "shadow_coinbase_mogusd_rsi4_profit_only"
}

if REGISTRY_PATH.exists():
    with open(REGISTRY_PATH, "r") as f:
        data = json.load(f)
    
    lanes = data.get("lanes", [])
    pruned_count = 0
    
    for lane in lanes:
        name = lane.get("name")
        if name not in SPOT_LANES:
            lane["enabled"] = False
            pruned_count += 1
    
    with open(REGISTRY_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Pruned registry: Disabled {pruned_count} unrelated lanes. Kept {len(SPOT_LANES)} spot lanes enabled.")
else:
    print("Registry not found.")
