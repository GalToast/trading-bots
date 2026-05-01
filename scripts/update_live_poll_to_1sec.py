import json

path = 'configs/penetration_lattice_runner_registry.json'
d = json.load(open(path, encoding='utf-8'))

lines = []
count = 0
for lane in d['lanes']:
    kind = lane.get('kind', '')
    if kind.startswith('live_'):
        name = lane['name']
        old_poll = lane.get('poll_seconds')
        lane['poll_seconds'] = 1
        args = lane.get('restart_args', [])
        for i, arg in enumerate(args):
            if arg == '--poll-seconds' and i+1 < len(args):
                args[i+1] = 1
        lines.append(f'Updated {name}: poll {old_poll} -> 1')
        count += 1

with open(path, 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)

with open('tmp_poll_update.txt', 'w') as f:
    f.write('\n'.join(lines) + '\n')
    f.write(f'Done: {count} live lanes updated\n')

print(f'Done: {count} live lanes updated')
