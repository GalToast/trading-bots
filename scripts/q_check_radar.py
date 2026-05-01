import json
kraken = json.load(open('reports/cache/kraken_spot_live_radar_ticks.json'))
print(f'Kraken radar type: {type(kraken)}')
print(f'Keys: {list(kraken.keys())}')
for k, v in kraken.items():
    print(f'  {k}: type={type(v).__name__}, len={len(v) if hasattr(v, "__len__") else "?"}')
    if isinstance(v, dict):
        for k2 in list(v.keys())[:3]:
            v2 = v[k2]
            print(f'    {k2}: type={type(v2).__name__}, len={len(v2) if hasattr(v2, "__len__") else "?"}')
            if isinstance(v2, list) and v2:
                print(f'      first: {str(v2[0])[:200]}')
