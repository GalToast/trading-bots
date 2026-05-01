import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHADOW = REPO / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl"

pnls = []
fees = 0.0
wins = 0
losses = 0

if not SHADOW.exists():
    print(f"Shadow log not found at {SHADOW}")
    exit(1)

with open(SHADOW, encoding="utf-8") as f:
    for line in f:
        try:
            d = json.loads(line.strip())
            if d.get("action") == "close_maker_shadow":
                net = d.get("net", 0.0)
                pnls.append(net)
                fees += d.get("entry_fee", 0.0) + d.get("exit_fee", 0.0)
                if net > 0:
                    wins += 1
                else:
                    losses += 1
        except Exception:
            continue

print(f"--- KRAKEN MAKER MACHINEGUN AUDIT ---")
print(f"Total Closes: {len(pnls)}")
if pnls:
    print(f"Win Rate:     {wins/len(pnls)*100:.1f}% ({wins}W / {losses}L)")
    print(f"Total Net:    ${sum(pnls):.4f}")
    print(f"Total Fees:   ${fees:.4f}")
    print(f"Avg PnL:      ${sum(pnls)/len(pnls):.4f}")
    print(f"Profit Factor: {sum([p for p in pnls if p > 0]) / abs(sum([p for p in pnls if p < 0]) or 1):.2f}")
    print(f"Min:          ${min(pnls):.4f}")
    print(f"Max:          ${max(pnls):.4f}")
