import sys; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient
c = KrakenSpotClient()
assets = c.asset_pairs()
print(f'Total assets: {len(assets)}')
suffixes = {}
for k, v in assets.items():
    if v.get('status') != 'online':
        continue
    ws = v.get('wsname', '')
    if '/' in ws:
        suffix = ws.split('/')[-1]
        suffixes[suffix] = suffixes.get(suffix, 0) + 1
for s, cnt in sorted(suffixes.items(), key=lambda x: -x[1]):
    print(f'{s}: {cnt} pairs')
