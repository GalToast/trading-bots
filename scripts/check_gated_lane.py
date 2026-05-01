import json
path = 'reports/penetration_lattice_shadow_fx_close_policy_mixed_session_gated_state.json'
d = json.load(open(path))
runner = d.get('runner', {})
meta = d.get('metadata', {})
print('Session-gated lane ALIVE!')
print('  session_gate in metadata:', meta.get('session_gate', False))
print('  session_gated in runner:', runner.get('session_gated', False))
print('  gated_hour:', runner.get('gated_hour', '?'))
print('  heartbeat:', runner.get('heartbeat_at', '?'))
symbols = d.get('symbols', {})
for sym, data in symbols.items():
    print('  %s: closes=%d, open=%d' % (sym, data.get('realized_closes', 0), len(data.get('open_tickets', []))))
