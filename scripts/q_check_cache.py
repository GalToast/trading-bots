import json, os
for name, path in [
    ('Kraken radar', 'reports/cache/kraken_spot_live_radar_ticks.json'),
    ('Coinbase radar', 'reports/cache/coinbase_spot_live_radar_ticks.json'),
    ('Kraken candles', 'reports/cache/kraken_spot_pulse_candles.json'),
    ('Coinbase candles', 'reports/cache/coinbase_spot_pulse_candles.json'),
]:
    if os.path.exists(path):
        sz = os.path.getsize(path)
        d = json.load(open(path))
        if isinstance(d, dict):
            print(f'{name}: {sz/1e6:.1f}MB, {len(d)} keys')
        elif isinstance(d, list):
            print(f'{name}: {sz/1e6:.1f}MB, {len(d)} items')
    else:
        print(f'{name}: NOT FOUND')
