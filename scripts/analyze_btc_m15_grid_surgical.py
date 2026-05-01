import json
from pathlib import Path
from datetime import datetime, timezone

def analyze_m15_grid():
    state_path = Path("reports/penetration_lattice_live_btcusd_m15_warp_state.json")
    if not state_path.exists():
        print(f"State file not found: {state_path}")
        return

    with open(state_path) as f:
        state = json.load(f)

    symbol_data = state.get("symbols", {}).get("BTCUSD", {})
    open_tickets = symbol_data.get("open_tickets", [])
    anchor = symbol_data.get("anchor", 0)
    realized_net = symbol_data.get("realized_net_usd", 0)
    
    if not open_tickets:
        print("No open tickets in BTC M15 LIVE.")
        return

    sells = [t for t in open_tickets if t["direction"] == "SELL"]
    buys = [t for t in open_tickets if t["direction"] == "BUY"]

    print(f"=== BTC M15 LIVE Grid Analysis ({datetime.now(timezone.utc).isoformat()}) ===")
    print(f"Anchor: ${anchor:,.2f}")
    print(f"Realized Net: ${realized_net:,.2f}")
    print(f"Total Open: {len(open_tickets)} ({len(buys)} BUY, {len(sells)} SELL)")

    if sells:
        avg_sell = sum(t["fill_price"] for t in sells) / len(sells)
        deepest_sell = min(t["fill_price"] for t in sells)
        shallowest_sell = max(t["fill_price"] for t in sells)
        print(f"\nSELL Positions:")
        print(f"  Count: {len(sells)}")
        print(f"  Average Entry: ${avg_sell:,.2f}")
        print(f"  Deepest Entry: ${deepest_sell:,.2f}")
        print(f"  Shallowest Entry: ${shallowest_sell:,.2f}")

    if buys:
        avg_buy = sum(t["fill_price"] for t in buys) / len(buys)
        deepest_buy = min(t["fill_price"] for t in buys)
        shallowest_buy = max(t["fill_price"] for t in buys)
        print(f"\nBUY Positions:")
        print(f"  Count: {len(buys)}")
        print(f"  Average Entry: ${avg_buy:,.2f}")
        print(f"  Deepest Entry: ${deepest_buy:,.2f}")
        print(f"  Shallowest Entry: ${shallowest_buy:,.2f}")

    # Calculate distance to profitable close for the whole SELL grid
    # Assuming close_alpha = 1.0 means we need to drop below entry
    # But often close_alpha < 1.0. Let's check metadata.
    metadata = state.get("metadata", {})
    close_alpha = metadata.get("raw_close_alpha", 1.0)
    step = metadata.get("step", 75)
    
    print(f"\nConfiguration:")
    print(f"  Step: ${step}")
    print(f"  Close Alpha: {close_alpha}")
    
    # Simple estimate of target price to clear average SELL
    # Target = Average Entry - (Step * Close Alpha)
    target_clearance = avg_sell - (step * close_alpha)
    print(f"\nTarget price to clear average SELL: ${target_clearance:,.2f}")

if __name__ == "__main__":
    analyze_m15_grid()
