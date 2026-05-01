import json
from pathlib import Path
import time
time.sleep(2)

sp = Path('reports/penetration_lattice_live_btcusd_m15_warp_state.json')
if sp.exists():
    # Retry if file is locked
    for i in range(5):
        try:
            st = json.loads(sp.read_text())
            btc = st['symbols']['BTCUSD']
            r = st['runner']
            print(f"Step: {btc['base_step_px']}")
            print(f"Anchor: {btc['anchor']}")
            print(f"Opens: {len(btc.get('open_tickets',[]))}")
            print(f"Closes: {btc.get('realized_closes', 0)}")
            print(f"Net: {btc.get('realized_net_usd', 0):.2f}")
            print(f"PID: {r.get('pid')}")
            print(f"HB: {r.get('heartbeat_at')}")
            break
        except:
            time.sleep(2)
else:
    print("State file not found")
    # List recent files
    for f in Path('reports').glob('*btcusd_m15_warp*state*'):
        print(f"  Found: {f.name}")
