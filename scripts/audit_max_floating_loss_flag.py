#!/usr/bin/env python3
"""Audit all registry lanes for --max-floating-loss-usd flag coverage."""
import json
import os

CONFIGS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs")

d = json.load(open(os.path.join(CONFIGS, "penetration_lattice_runner_registry.json")))
entries = d.get("registry", d.get("lanes", d.get("entries", [])))

missing = []
has_it = []

for e in entries:
    name = e.get("name", "?")
    args = e.get("restart_args", [])
    has_flag = False
    flag_val = None
    for i, a in enumerate(args):
        if a == "--max-floating-loss-usd" and i + 1 < len(args):
            has_flag = True
            flag_val = args[i + 1]
    if has_flag:
        has_it.append((name, flag_val))
    else:
        missing.append(name)

print(f"TOTAL: {len(entries)} lanes")
print(f"HAS --max-floating-loss-usd: {len(has_it)}")
print(f"MISSING: {len(missing)}")
print()

if missing:
    print("=== MISSING (vulnerable to forced_unwind) ===")
    for n in missing:
        print(f"  MISSING: {n}")

if has_it:
    print()
    print("=== HAS FLAG ===")
    for n, v in has_it:
        print(f"  OK: {n} = {v}")
