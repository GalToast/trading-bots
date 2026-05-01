import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHADOW = REPO / "reports" / "neural_harpoon_shadow_log.jsonl"

pnls = []
trigger_stats = {}

if not SHADOW.exists():
    print(f"Shadow log not found at {SHADOW}")
    exit(1)

with open(SHADOW, encoding="utf-8") as f:
    for line in f:
        try:
            d = json.loads(line.strip())
            # For now, Harpoon doesn't have "closes" in the log, only "triggers".
            # I'll check if there's any PnL modeling in the log.
            # Looking at the tail earlier: only action, trigger, prob, price.
            # I'll just count triggers by product and event.
            trigger = d.get("trigger_event", "unknown")
            pid = d.get("product_id", "unknown")
            
            if pid not in trigger_stats: trigger_stats[pid] = {}
            trigger_stats[pid][trigger] = trigger_stats[pid].get(trigger, 0) + 1
        except Exception:
            continue

print(f"--- NEURAL HARPOON V2 AUDIT (TRIGGERS) ---")
for pid, triggers in trigger_stats.items():
    print(f"Product: {pid}")
    for t, count in triggers.items():
        print(f"  {t}: {count}")
