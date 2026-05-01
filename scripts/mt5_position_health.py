"""MT5 Live Position Health Dashboard — real-time snapshot of all open positions."""
import json
import os
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(__file__), "..", "reports")

LIVE_EXEC_STATES = [
    ("BTCUSD H1 (941779)", "penetration_lattice_live_btcusd_exc2_tight_exec_state.json"),
    ("BTCUSD M5 (941780)", "penetration_lattice_live_btcusd_m5_warp_exec_state.json"),
    ("FX Rearm (941777)", "penetration_lattice_live_mirror_state.json"),
    ("FX Momentum (941778)", "penetration_lattice_live_momentum_alpha50_exec_state.json"),
]

def analyze_exec_state(label, filepath):
    full_path = os.path.join(REPORTS, filepath)
    if not os.path.exists(full_path):
        print(f"\n❌ {label}: state file not found")
        return None
    
    with open(full_path) as f:
        state = json.load(f)
    
    # Extract position data
    open_tickets = state.get("open_tickets", [])
    realized_net = state.get("realized_net_usd", 0)
    anchor = state.get("anchor", None)
    symbol = state.get("symbol", "?")
    
    buys = [t for t in open_tickets if t.get("side") == "BUY" or t.get("type") == "BUY"]
    sells = [t for t in open_tickets if t.get("side") == "SELL" or t.get("type") == "SELL"]
    
    # Calculate floating exposure
    total_buy_notional = sum(t.get("price", 0) * t.get("volume", 0) for t in buys)
    total_sell_notional = sum(t.get("price", 0) * t.get("volume", 0) for t in sells)
    
    # Get price levels
    buy_prices = [t.get("price") for t in buys if t.get("price")]
    sell_prices = [t.get("price") for t in sells if t.get("price")]
    
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  Symbol: {symbol}")
    print(f"  Open positions: {len(open_tickets)} ({len(buys)} BUY, {len(sells)} SELL)")
    print(f"  Realized net: ${realized_net:+.2f}")
    print(f"  Anchor: {anchor}")
    
    if buy_prices:
        print(f"  BUY range: ${min(buy_prices):.2f} - ${max(buy_prices):.2f}")
    if sell_prices:
        print(f"  SELL range: ${min(sell_prices):.2f} - ${max(sell_prices):.2f}")
    
    # Analyze position spread
    if buy_prices and sell_prices:
        spread = min(sell_prices) - max(buy_prices)
        print(f"  BUY-SELL spread: ${spread:.2f} (negative = locked/crossed)")
    
    # Show deepest underwater positions
    if buys and sells:
        max_buy = max(buy_prices)
        min_sell = min(sell_prices)
        if min_sell < max_buy:
            print(f"  ⚠️  CROSSED BOOK: lowest SELL (${min_sell:.2f}) < highest BUY (${max_buy:.2f})")
            print(f"      Locked loss per unit: ${max_buy - min_sell:.2f}")
    
    # Check for deep OTM positions
    if buys and buy_prices:
        lowest_buy = min(buy_prices)
        highest_buy = max(buy_prices)
        if highest_buy > 0 and lowest_buy < highest_buy * 0.98:
            print(f"  🚨 Deep BUY position at ${lowest_buy:.2f} is >2% below top BUY at ${highest_buy:.2f}")
    
    if sells and sell_prices:
        highest_sell = max(sell_prices)
        lowest_sell = min(sell_prices)
        if lowest_sell > 0 and highest_sell > lowest_sell * 1.02:
            print(f"  🚨 Deep SELL position at ${highest_sell:.2f} is >2% above bottom SELL at ${lowest_sell:.2f}")
    
    return {
        "label": label,
        "symbol": symbol,
        "total_positions": len(open_tickets),
        "buys": len(buys),
        "sells": len(sells),
        "realized_net": realized_net,
    }

print(f"\n{'#'*70}")
print(f"#  MT5 LIVE POSITION HEALTH DASHBOARD")
print(f"#  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print(f"{'#'*70}")

results = []
for label, filename in LIVE_EXEC_STATES:
    result = analyze_exec_state(label, filename)
    if result:
        results.append(result)

print(f"\n{'='*70}")
print(f"  PORTFOLIO SUMMARY")
print(f"{'='*70}")

total_positions = sum(r["total_positions"] for r in results)
total_realized = sum(r["realized_net"] for r in results)
total_buys = sum(r["buys"] for r in results)
total_sells = sum(r["sells"] for r in results)

print(f"  Total open positions: {total_positions} ({total_buys} BUY, {total_sells} SELL)")
print(f"  Total realized net: ${total_realized:+.2f}")
print(f"  Lanes active: {len(results)}/{len(LIVE_EXEC_STATES)}")

for r in results:
    pct = r["realized_net"] / max(total_realized, 0.01) * 100 if total_realized != 0 else 0
    print(f"    {r['label']}: {r['total_positions']} pos, ${r['realized_net']:+.2f} realized")
