"""Check LIVE SOL M5 status — PID 36456, magic 941783."""
import json, MetaTrader5 as mt5

mt5.initialize()

# Check broker positions for SOL M5 magic 941783
positions = mt5.positions_get()
if positions:
    sol_positions = [p for p in positions if p.symbol == 'SOLUSD' and p.magic == 941783]
    print(f"SOL M5 LIVE (magic 941783): {len(sol_positions)} positions")
    for p in sol_positions[:5]:
        print(f"  {p.type} {p.volume} @ {p.price_open}, floating=${p.profit:.2f}")
else:
    print("No broker positions")

# Check state file
try:
    state = json.load(open('reports/penetration_lattice_live_solusd_m5_warp_state.json'))
    print(f"\nSOL M5 LIVE state:")
    print(f"  close_count: {state.get('close_count', 0)}")
    print(f"  anchor: {state.get('anchor_price', 0)}")
    open_pos = state.get('open_positions', {})
    total_open = sum(len(v) for v in open_pos.values())
    print(f"  open positions: {total_open}")
except Exception as e:
    print(f"State error: {e}")

# Check process
import psutil
for p in psutil.process_iter(['pid', 'cmdline']):
    cmd = ' '.join(p.info['cmdline'] or [])
    if '36456' == str(p.info['pid']):
        print(f"\nSOL M5 LIVE process: PID {p.info['pid']}")
        print(f"  {cmd[:150]}")
        break
else:
    print("\nSOL M5 LIVE process NOT FOUND (PID 36456)")
    # Search for any SOL M5 live process
    for p in psutil.process_iter(['pid', 'cmdline']):
        cmd = ' '.join(p.info['cmdline'] or [])
        if 'solusd' in cmd.lower() and 'm5' in cmd.lower() and 'live' in cmd.lower():
            print(f"  Found: PID {p.info['pid']}: {cmd[:150]}")

mt5.shutdown()
