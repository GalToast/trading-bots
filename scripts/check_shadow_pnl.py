import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHADOW = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_events.jsonl"

pnls = []
with open(SHADOW) as f:
    for line in f:
        d = json.loads(line.strip())
        if d.get("action") == "close_ticket":
            pnl = d.get("net_pnl_usd") or d.get("pnl_usd") or d.get("pnl")
            if pnl is not None:
                pnls.append(float(pnl))

print(f"Shadow PnL: {len(pnls)} closes")
if pnls:
    print(f"Avg:  ${sum(pnls)/len(pnls):.2f}")
    print(f"Total: ${sum(pnls):.2f}")
    print(f"Min:  ${min(pnls):.2f}")
    print(f"Max:  ${max(pnls):.2f}")
