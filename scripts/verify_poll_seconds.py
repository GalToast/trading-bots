import json
d = json.load(open('configs/penetration_lattice_runner_registry.json'))
lanes = d['lanes']
issues = []
for lane in lanes:
    args = lane.get('restart_args', [])
    for j, a in enumerate(args):
        if a == '--poll-seconds' and j + 1 < len(args):
            val = args[j + 1]
            if not isinstance(val, str):
                issues.append('Lane %s: --poll-seconds=%s (type %s)' % (lane['name'], val, type(val).__name__))
if issues:
    print('ISSUES FOUND:')
    for x in issues:
        print('  ' + x)
else:
    print('All --poll-seconds values are strings in registry ✅')
