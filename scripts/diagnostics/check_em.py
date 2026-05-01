import json
d = json.load(open('reports/execution_monitor_report.json'))
print(f'Type: {type(d).__name__}')
if isinstance(d, dict):
    print(f'Keys: {list(d.keys())}')
    for k,v in d.items():
        if isinstance(v, list):
            print(f'  {k}: list, {len(v)} items')
            for item in v[:3]:
                lane = item.get('lane', item.get('name', '?'))
                print(f'    {lane}')
        else:
            print(f'  {k}: {type(v).__name__}')
