#!/usr/bin/env python3
"""Deep admission analysis — find multi-factor rules."""
import json
from pathlib import Path

OPPORTUNITY_PATH = Path("reports/kraken_maker_opportunity_board.json")
EVENT_LOG = Path("reports/kraken_spot_maker_machinegun_shadow_events.jsonl")

def load_events():
    events = []
    with open(EVENT_LOG) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except:
                pass
    return events

def main():
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    board = {r["product_id"]: r for r in opp_board.get("rows", [])}
    
    events = load_events()
    closes = [e for e in events if "close" in e.get("action", "")]
    
    # Winners and losers with their close data
    winners = [
        {"prod": "HOUSE-USD", "net": 6.67, "reason": "maker_rent_harvest"},
        {"prod": "BTR-USD", "net": 2.07, "reason": "maker_rent_harvest"},
        {"prod": "FOLKS-USD", "net": 0.75, "reason": "maker_rent_harvest"},
        {"prod": "BANANAS31-USD", "net": 0.18, "reason": "maker_min_profit_harvest"},
    ]
    
    losers = [
        {"prod": "GRASS-USD", "net": -0.96, "reason": "maker_no_mfe_adverse_stop"},
        {"prod": "KSM-USD", "net": -0.85, "reason": "maker_no_mfe_adverse_stop"},
        {"prod": "AUD-USD", "net": -0.65, "reason": "maker_no_mfe_adverse_stop"},
    ]
    
    print("=" * 80)
    print("MULTI-FACTOR ADMISSION RULE SEARCH")
    print("=" * 80)
    
    print(f"\n{'Product':<14} {'MER':>6} {'Tail':>7} {'FG':>9} {'Spread?':>8} {'Result':>7}")
    print("-" * 80)
    
    all_products = [(w["prod"], "WIN", w["net"]) for w in winners] + \
                   [(l["prod"], "LOSE", l["net"]) for l in losers]
    
    for prod, label, net in all_products:
        r = board.get(prod, {})
        mer = r.get("mer", 0)
        tail = r.get("tail_prob", 0)
        fg = r.get("fast_green_prob", 0)
        # Check if board has spread data
        spread = r.get("spread_bps", r.get("spread", "N/A"))
        print(f"{prod:<14} {mer:>6.2f} {tail:>7.4f} {fg:>9.6f} {str(spread):>8} {label:>5} {net:+.2f}%")
    
    # Check ALL columns in the board
    print(f"\n{'='*80}")
    print(f"AVAILABLE FIELDS IN OPPORTUNITY BOARD:")
    print(f"{'='*80}")
    if opp_board.get("rows"):
        sample = opp_board["rows"][0]
        for key in sorted(sample.keys()):
            print(f"  {key}: {sample[key]}")
    
    # Key insight: what distinguishes HOUSE (win) from GRASS (lose)?
    # Both have high MER, both have tail=0.89, both have fg=0.00004x
    # The difference must be in something ELSE
    
    print(f"\n{'='*80}")
    print(f"HOUSE vs GRASS HEAD-TO-HEAD:")
    print(f"{'='*80}")
    house = board.get("HOUSE-USD", {})
    grass = board.get("GRASS-USD", {})
    for key in sorted(set(list(house.keys()) + list(grass.keys()))):
        h_val = house.get(key, "N/A")
        g_val = grass.get(key, "N/A")
        diff = "✓" if h_val != g_val else " "
        print(f"  {diff} {key:<25} HOUSE={str(h_val):>15}  GRASS={str(g_val):>15}")

if __name__ == "__main__":
    main()
