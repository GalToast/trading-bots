import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl"

def audit_reality_cap(max_allowed_dip_pct: float = 3.0):
    if not EVENTS_PATH.exists():
        print("Log not found.")
        return

    total_closes = 0
    original_wins = 0
    reality_wins = 0
    
    with open(EVENTS_PATH, "r", encoding="utf-8-sig") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("action") == "close_maker_shadow":
                    total_closes += 1
                    net = float(e.get("net", 0.0))
                    mae = abs(float(e.get("min_net_pct_on_cost", 0.0)))
                    
                    if net > 0:
                        original_wins += 1
                        # If it was a win, but dipped too far, it's a 'Mental Loss'
                        if mae <= max_allowed_dip_pct:
                            reality_wins += 1
                    else:
                        # It was already a loss
                        pass
            except:
                continue

    print(f"=== THE 3% REALITY CAP AUDIT: {total_closes} Closes ===")
    print(f"Original Win Rate:  {(original_wins/total_closes*100):.1f}% ({original_wins}/{total_closes})")
    print(f"True Adjusted WR:   {(reality_wins/total_closes*100):.1f}% ({reality_wins}/{total_closes})")
    print(f"Miracle Recoveries: {original_wins - reality_wins} (Trades that dipped > {max_allowed_dip_pct}%)")
    
    if (reality_wins/total_closes) > 0.90:
        print("\n[VERDICT] FEASIBLE: The edge is structural, not lucky.")
    else:
        print("\n[VERDICT] CAUTION: The streak relies on holding through deep drawdowns.")

if __name__ == "__main__":
    audit_reality_cap(3.0)
