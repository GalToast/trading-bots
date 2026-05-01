"""Check BTC M15 LIVE state - grid compression claim."""
import json, MetaTrader5 as mt5

mt5.initialize()

# Get live BTC M15 warp state
try:
    state = json.load(open('reports/penetration_lattice_live_btcusd_m15_warp_state.json'))
    print("=== BTC M15 LIVE State ===")
    print(f"  close_count: {state.get('close_count', 0)}")
    print(f"  anchor: {state.get('anchor_price', 0)}")
    open_pos = state.get('open_positions', {})
    total_open = sum(len(v) for v in open_pos.values())
    print(f"  open positions (state): {total_open}")
    for side, positions in open_pos.items():
        print(f"    {side}: {len(positions)} positions")
        if positions:
            prices = [p.get('open_price', p.get('price', '?')) for p in positions[:3]]
            print(f"      Sample: {prices}")
except Exception as e:
    print(f"State error: {e}")

# Get broker open positions for BTC M15 magic
print("\n=== BTC M15 LIVE Broker Positions ===")
positions = mt5.positions_get()
if positions:
    btc_positions = [p for p in positions if p.symbol == 'BTCUSD' and p.magic == 941781]
    print(f"  Total broker positions: {len(positions)}")
    print(f"  BTCUSD magic 941781: {len(btc_positions)}")
    for p in btc_positions[:5]:
        print(f"    {p.type} {p.volume} @ {p.price_open}, TP={p.tp}, SL={p.sl}")
    if len(btc_positions) > 5:
        print(f"    ... and {len(btc_positions) - 5} more")
else:
    print("  No positions")

mt5.shutdown()
