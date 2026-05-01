import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "maker_fee_rsi_shadow_state.json"

if STATE_PATH.exists():
    with open(STATE_PATH, "r") as f:
        data = json.load(f)
    
    # Capital Restoration
    data["cash_usd"] = 100.0
    data["realized_net_usd"] = 0.0
    data["total_fees"] = 0.0
    data["open_positions"] = {}
    data["wins"] = 0
    data["losses"] = 0
    data["realized_closes"] = 0
    
    with open(STATE_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print("Strict Maker RSI state reset to $100.00. Ready for Nut Cracker harvests.")
else:
    print("State file not found.")
