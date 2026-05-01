import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHADOW = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_events.jsonl"
out_path = REPO / "reports" / "shadow_close_sample.txt"

with open(SHADOW) as f:
    for line in f:
        d = json.loads(line.strip())
        if d.get("action") == "close_ticket":
            with open(out_path, "w") as out:
                out.write(json.dumps(d, indent=2)[:800])
            print(f"Wrote sample to {out_path}")
            break
