import json, time, sys
from pathlib import Path

for i in range(5):
    try:
        text = Path('reports/penetration_lattice_live_btcusd_m15_warp_state.json').read_text()
        st = json.loads(text)
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
    except (json.JSONDecodeError, PermissionError) as e:
        print(f"Attempt {i+1}: {e}")
        time.sleep(2)
