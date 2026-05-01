import json
from pathlib import Path

def check_cache(path, name):
    p = Path(path)
    if not p.exists():
        print(f"{name} not found")
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        samples = data.get("samples", {})
        print(f"\n--- {name} ---")
        print(f"Total Products: {len(samples)}")
        if samples:
            first_key = list(samples.keys())[0]
            ticks = samples[first_key]
            print(f"First Key: {first_key}")
            if ticks:
                print(f"Tick Count: {len(ticks)}")
                # Some files use 'ts' (epoch), some use 'ts_utc' (iso)
                ts_field = 'ts_utc' if 'ts_utc' in ticks[0] else 'ts'
                print(f"First TS: {ticks[0].get(ts_field)}")
                print(f"Last TS: {ticks[-1].get(ts_field)}")
    except Exception as e:
        print(f"Error checking {name}: {e}")

check_cache("reports/cache/coinbase_spot_live_radar_ticks.json", "Coinbase")
check_cache("reports/cache/kraken_spot_live_radar_ticks.json", "Kraken")

