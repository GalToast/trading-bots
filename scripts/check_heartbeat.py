import json, os
files = [
    'reports/penetration_lattice_live_source_state.json',
    'reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json',
    'reports/penetration_lattice_live_btcusd_m5_warp_state.json',
]
for f in files:
    if os.path.exists(f):
        d = json.load(open(f))
        runner = d.get('runner', {})
        hb = runner.get('heartbeat_at', '?')
        exc = runner.get('consecutive_exceptions', '?')
        print('%s: heartbeat=%s exceptions=%s' % (os.path.basename(f).replace('.json',''), hb, exc))
    else:
        print('%s: NOT FOUND' % os.path.basename(f))
