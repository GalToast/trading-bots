import json
from pathlib import Path

lanes = [
    ('ratio50', 'reports/kraken_spot_maker_machinegun_cooldown_ratio50_ab_state.json', 'reports/kraken_spot_maker_machinegun_cooldown_ratio50_ab_events.jsonl'),
    ('size12', 'reports/kraken_spot_maker_machinegun_cooldown_size12_ab_state.json', 'reports/kraken_spot_maker_machinegun_cooldown_size12_ab_events.jsonl'),
    ('parallel_cooldown', 'reports/kraken_spot_maker_machinegun_parallel_cooldown_ab_state.json', 'reports/kraken_spot_maker_machinegun_parallel_cooldown_ab_events.jsonl'),
    ('cooldown_ab', 'reports/kraken_spot_maker_machinegun_cooldown_ab_state.json', 'reports/kraken_spot_maker_machinegun_cooldown_ab_events.jsonl'),
]

for name, state_path, events_path in lanes:
    sp = Path(state_path)
    ep = Path(events_path)
    print(f'\n=== {name.upper()} ===')
    if sp.exists():
        with open(sp) as f:
            state = json.load(f)
        sd = state.get('state', {})
        print(f'  Cash: ${sd.get("cash_usd", 0):.4f} (start ${sd.get("starting_cash_usd", 100):.4f})')
        print(f'  Closes: {sd.get("total_closes", 0)}')
        print(f'  Active: {len(sd.get("active_positions", []))}')
    else:
        print('  State: not found')
    if ep.exists():
        events = []
        with open(ep) as f:
            for line in f:
                try:
                    events.append(json.loads(line.strip()))
                except:
                    pass
        closes = [e for e in events if 'close' in e.get('action', '') and e.get('net_pct', 0) != 0]
        total = sum(e.get('net_pct', 0) for e in closes)
        wins = sum(1 for e in closes if e.get('net_pct', 0) > 0)
        losses = sum(1 for e in closes if e.get('net_pct', 0) <= 0)
        print(f'  Events: {len(events)}, Closes: {len(closes)}, {wins}W/{losses}L, net={total:+.4f}%')
    else:
        print('  Events: not found')
