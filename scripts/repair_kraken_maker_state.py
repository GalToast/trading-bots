import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_state.json"

if STATE_PATH.exists():
    with open(STATE_PATH, "r") as f:
        data = json.load(f)
    
    state = data.get("state", {})
    starting_cash = float(state.get("starting_cash_usd", 100.0))
    realized_net = float(state.get("realized_net_usd", 0.0))
    
    # Repair
    state["cash_usd"] = round(starting_cash + realized_net, 6)
    state["active_positions"] = {}
    state["reentry_blocks"] = {}
    
    data["state"] = state
    data["updated_at"] = "REPAIRED_BY_GEMINI_CLI"
    
    with open(STATE_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Repaired state: cash_usd={state['cash_usd']}, active_positions cleared.")
else:
    print("State file not found.")
