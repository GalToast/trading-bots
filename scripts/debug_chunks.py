import json
from pathlib import Path

REPORT_DIR = Path("reports")
for fname in ["parallel_scan_chunk_0_49.json", "parallel_scan_chunk_50_99.json"]:
    fpath = REPORT_DIR / fname
    with open(fpath, encoding="utf-8") as f:
        d = json.load(f)
    print(f"\n=== {fname} ===")
    for k, v in d.items():
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
            if v and isinstance(v[0], dict):
                print(f"    first keys: {list(v[0].keys())}")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)} keys]")
        else:
            print(f"  {k}: {v}")
