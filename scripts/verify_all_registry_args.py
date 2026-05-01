import json
d = json.load(open('configs/penetration_lattice_runner_registry.json'))
lanes = d['lanes']
issues = []
for lane in lanes:
    args = lane.get('restart_args', [])
    for j, a in enumerate(args):
        if a.startswith('--') and j + 1 < len(args):
            val = args[j + 1]
            if isinstance(val, (int, float)):
                issues.append('Lane %s: %s=%s (type %s)' % (lane['name'], a, val, type(val).__name__))
if issues:
    print('POTENTIAL ISSUES (non-string arg values):')
    for x in issues:
        print('  ' + x)
else:
    print('All argument values after flags are strings ✅')
