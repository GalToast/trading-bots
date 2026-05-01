#!/usr/bin/env python3
"""BTC H1 Step30 vs Step50 Shadow Comparison Analysis.

Compares the two BTC H1 step shadow runners to answer:
1. Is step30's higher anchor-reset frequency eating into its edge?
2. Which step has better PnL efficiency (per close, per reset, per open position)?
3. Which has healthier floating inventory geometry?
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent

def load_state(step):
    path = ROOT / "reports" / f"penetration_lattice_shadow_btcusd_h1_step{step}_state.json"
    with open(path) as f:
        return json.load(f)

def analyze(state, step):
    sym = state["symbols"]["BTCUSD"]
    closes = sym["realized_closes"]
    net = sym["realized_net_usd"]
    opens = len(sym["open_tickets"])
    resets = sym["anchor_resets"]
    
    # Floating PnL estimate from open positions
    floating_pnl = 0.0
    for pos in sym["open_tickets"]:
        entry = pos["entry_fill_price"]
        trigger = pos["trigger_level"]
        if pos["direction"] == "SELL":
            floating_pnl += (entry - trigger)  # profit if price drops to trigger
        else:
            floating_pnl += (trigger - entry)
    
    pnl_per_close = net / closes if closes > 0 else 0
    pnl_per_reset = net / resets if resets > 0 else 0
    net_per_open = net / opens if opens > 0 else 0
    
    # Inventory concentration
    if opens > 0:
        entries = [p["entry_fill_price"] for p in sym["open_tickets"]]
        triggers = [p["trigger_level"] for p in sym["open_tickets"]]
        avg_entry = sum(entries) / len(entries)
        avg_trigger = sum(triggers) / len(triggers)
        max_entry = max(entries)
        min_entry = min(entries)
        spread = max_entry - min_entry
    else:
        avg_entry = avg_trigger = max_entry = min_entry = spread = 0
    
    return {
        "step": step,
        "closes": closes,
        "net_usd": net,
        "open_positions": opens,
        "anchor_resets": resets,
        "pnl_per_close": pnl_per_close,
        "pnl_per_reset": pnl_per_reset,
        "net_per_open": net_per_open,
        "floating_pnl_estimate": floating_pnl,
        "avg_entry": avg_entry,
        "avg_trigger": avg_trigger,
        "max_entry": max_entry,
        "min_entry": min_entry,
        "inventory_spread": spread,
        "max_open_total": sym["max_open_total"],
    }

def main():
    s30 = load_state(30)
    s50 = load_state(50)
    
    a30 = analyze(s30, 30)
    a50 = analyze(s50, 50)
    
    print(f"{'='*70}")
    print(f"{'Metric':<30} {'Step30':>12} {'Step50':>12}")
    print(f"{'='*70}")
    
    for key in ["closes", "net_usd", "open_positions", "anchor_resets",
                "pnl_per_close", "pnl_per_reset", "net_per_open",
                "floating_pnl_estimate", "inventory_spread", "max_open_total"]:
        v30 = a30[key]
        v50 = a50[key]
        if isinstance(v30, float):
            print(f"{key:<30} ${v30:>10.2f} ${v50:>10.2f}")
        else:
            print(f"{key:<30} {v30:>12} {v50:>12}")
    
    print(f"{'='*70}")
    print(f"\nKey insights:", flush=True)
    
    # Reset efficiency
    print(f"  PnL per anchor reset: Step30=${a30['pnl_per_reset']:+.2f}, Step50=${a50['pnl_per_reset']:+.2f}")
    if a30['pnl_per_reset'] > a50['pnl_per_reset']:
        print(f"  → Step30 is MORE efficient per reset (despite resetting 2x more)")
    else:
        print(f"  → Step50 is MORE efficient per reset (fewer resets, more PnL each)")
    
    # Close efficiency
    print(f"  PnL per close: Step30=${a30['pnl_per_close']:+.2f}, Step50=${a50['pnl_per_close']:+.2f}")
    
    # Inventory risk
    print(f"  Inventory spread: Step30=${a30['inventory_spread']:.2f}, Step50=${a50['inventory_spread']:.2f}")
    if a30['inventory_spread'] < a50['inventory_spread']:
        print(f"  → Step30 has TIGHTER inventory clustering (lower drift risk)")
    else:
        print(f"  → Step50 has TIGHTER inventory clustering (lower drift risk)")
    
    # Open position load
    print(f"  Open positions: Step30={a30['open_positions']}, Step50={a50['open_positions']}")
    if a30['open_positions'] > a50['open_positions']:
        print(f"  → Step30 has {a30['open_positions'] - a50['open_positions']} MORE open positions (higher floating risk)")
    else:
        print(f"  → Step50 has {a50['open_positions'] - a30['open_positions']} MORE open positions (higher floating risk)")
    
    # Verdict
    print(f"\n{'='*70}")
    print(f"VERDICT:")
    net_diff = a30['net_usd'] - a50['net_usd']
    if net_diff > 0:
        print(f"  Step30 leads by ${net_diff:+.2f} net PnL")
        print(f"  But with {a30['anchor_resets'] - a50['anchor_resets']} more anchor resets ({a30['anchor_resets']} vs {a50['anchor_resets']})")
        print(f"  And {a30['open_positions'] - a50['open_positions']} more open positions ({a30['open_positions']} vs {a50['open_positions']})")
    else:
        print(f"  Step50 leads by ${-net_diff:+.2f} net PnL")
    
    print(f"\n  The question is whether the extra PnL justifies the extra inventory risk.")
    print(f"  Step30: ${a30['net_usd']:.2f} / {a30['open_positions']} opens = ${a30['net_per_open']:.2f} per open position")
    print(f"  Step50: ${a50['net_usd']:.2f} / {a50['open_positions']} opens = ${a50['net_per_open']:.2f} per open position")
    
    if a30['net_per_open'] > a50['net_per_open']:
        print(f"  → Step30 is MORE capital-efficient per open position")
    else:
        print(f"  → Step50 is MORE capital-efficient per open position")

if __name__ == "__main__":
    main()
