#!/usr/bin/env python3
"""Monitor spot crypto lanes and alert when promotion criteria are met."""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

def read_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def check_lane(name: str, state_path: Path, criteria: dict) -> dict:
    state = read_json(state_path)
    if not state:
        return {"name": name, "status": "NO STATE FILE", "ready": False}
    
    data = state.get("state", state)  # Handle both formats
    closes = int(data.get("realized_closes", 0))
    cash = float(data.get("cash_usd", 0))
    starting = float(data.get("starting_cash_usd", 100))
    net = cash - starting
    
    result = {
        "name": name,
        "closes": closes,
        "net_usd": round(net, 2),
        "cash_usd": round(cash, 2),
    }
    
    # Check criteria
    if "min_closes" in criteria:
        result["min_closes_met"] = closes >= criteria["min_closes"]
        result["closes_needed"] = max(0, criteria["min_closes"] - closes)
    
    if "max_losses" in criteria:
        # This would need event log analysis - simplified for now
        result["max_losses"] = criteria["max_losses"]
    
    # Check if lane is ready for promotion
    ready = True
    if "min_closes" in criteria:
        ready = ready and closes >= criteria["min_closes"]
    
    result["ready"] = ready
    return result

def main():
    print("=" * 60)
    print("SPOT CRYPTO LANE PROMOTION MONITOR")
    print(f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)
    
    lanes = [
        {
            "name": "Cooldown (20-30s, max=1)",
            "state": REPORTS / "kraken_spot_maker_machinegun_shadow_state.json",
            "criteria": {"min_closes": 20, "max_losses": 0},
        },
        {
            "name": "Frontier Shadow (main)",
            "state": REPORTS / "kraken_spot_frontier_shadow_state.json",
            "criteria": {"min_closes": 20, "max_losses": 0},
        },
        {
            "name": "Size12 ($12/pos, 20-30s)",
            "state": REPORTS / "kraken_spot_maker_machinegun_cooldown_size12_ab_state.json",
            "criteria": {"min_closes": 20, "max_losses": 0},
        },
        {
            "name": "Size40 ($40/pos, 20-30s) [NEW]",
            "state": REPORTS / "kraken_spot_maker_machinegun_size40_shadow_state.json",
            "criteria": {"min_closes": 20, "max_losses": 0},
        },
    ]
    
    for lane in lanes:
        result = check_lane(lane["name"], lane["state"], lane["criteria"])
        print(f"\n{result['name']}:")
        print(f"  Closes: {result.get('closes', 'N/A')}")
        print(f"  Net: ${result.get('net_usd', 'N/A')}")
        print(f"  Cash: ${result.get('cash_usd', 'N/A')}")
        
        if result.get("ready"):
            print("  [READY] **READY FOR PROMOTION!**")
        else:
            closes_needed = result.get("closes_needed", 0)
            if closes_needed > 0:
                print(f"  [WAITING] Need {closes_needed} more closes")
            else:
                print(f"  [CHECK] Not ready (check losses/ghost marks)")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
