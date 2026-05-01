import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl"

def find_near_death_trades():
    if not EVENTS_PATH.exists():
        print("Log not found.")
        return

    near_death = []
    with open(EVENTS_PATH, "r", encoding="utf-8-sig") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("action") == "close_maker_shadow":
                    mae = e.get("min_net_pct_on_cost", 0.0)
                    if mae < -1.0: # Any trade that dipped > 1%
                        near_death.append({
                            "pid": e.get("product_id"),
                            "net": e.get("net_pct"),
                            "mae": mae,
                            "reason": e.get("reason"),
                            "age": e.get("age_seconds")
                        })
            except:
                continue

    # Sort by the worst dip (MAE)
    near_death.sort(key=lambda x: x["mae"])

    print(f"=== THE 'DIRTY LAUNDRY' AUDIT: TOP 10 NEAR-MISSES ===")
    print(f"{'Product':<12} | {'Final Net':<10} | {'Worst Dip (MAE)':<15} | {'Reason'}")
    print("-" * 60)
    for t in near_death[:10]:
        print(f"{t['pid']:<12} | {t['net']:>+9.2f}% | {t['mae']:>+14.2f}% | {t['reason']}")

if __name__ == "__main__":
    find_near_death_trades()
