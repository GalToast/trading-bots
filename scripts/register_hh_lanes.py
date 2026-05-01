#!/usr/bin/env python3
"""Register ETH M5 HH and XAUUSD consolidation lanes in the runner registry."""
import json

REGISTRY = "configs/penetration_lattice_runner_registry.json"
WATCHDOG = "configs/watchdog_groups.json"

r = json.load(open(REGISTRY))
existing_names = {l['name'] for l in r['lanes']}
print(f"Existing lanes: {len(r['lanes'])}")

eth_name = 'shadow_ethusd_m5_hungry_hippo_step5'
xau_name = 'shadow_xauusd_m15_consolidation_vacuum'

print(f"ETH exists: {eth_name in existing_names}")
print(f"XAU exists: {xau_name in existing_names}")

if eth_name not in existing_names:
    eth_lane = {
        "name": eth_name,
        "kind": "shadow_crypto",
        "state_path": "reports/penetration_lattice_shadow_ethusd_m5_hh_step5_state.json",
        "event_path": "reports/penetration_lattice_shadow_ethusd_m5_hh_step5_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_shadow.py",
            "reports/penetration_lattice_shadow_ethusd_m5_hh_step5_state.json"
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_shadow.py",
            "--symbols", "ETHUSD",
            "--timeframe", "M5",
            "--step", "5",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc1",
            "--raw-rearm-cooldown-bars", "0",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--state-path", "reports/penetration_lattice_shadow_ethusd_m5_hh_step5_state.json",
            "--event-path", "reports/penetration_lattice_shadow_ethusd_m5_hh_step5_events.jsonl",
            "--poll-seconds", "30",
            "--shared-price-max-age-ms", "1000",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--escape-hatch",
            "--escape-max-bars", "15",
            "--escape-max-loss", "3.0",
            "--escape-cut-count", "1",
            "--escape-max-cut-loss", "5.0"
        ],
        "enabled": True
    }
    r['lanes'].append(eth_lane)
    print("Added ETH lane")

if xau_name not in existing_names:
    xau_lane = {
        "name": xau_name,
        "kind": "shadow_commodity",
        "state_path": "reports/penetration_lattice_shadow_xauusd_m15_consolidation_state.json",
        "event_path": "reports/penetration_lattice_shadow_xauusd_m15_consolidation_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "reports/penetration_lattice_shadow_xauusd_m15_consolidation_state.json"
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "XAUUSD",
            "--timeframe", "M15",
            "--step", "3.51",
            "--max-open-per-side", "15",
            "--raw-close-alpha", "0.2",
            "--raw-rearm-variant", "rearm_lvl2_exc1",
            "--raw-rearm-cooldown-bars", "1",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--state-path", "reports/penetration_lattice_shadow_xauusd_m15_consolidation_state.json",
            "--event-path", "reports/penetration_lattice_shadow_xauusd_m15_consolidation_events.jsonl",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--escape-hatch",
            "--escape-max-bars", "10",
            "--escape-max-loss", "3.0"
        ],
        "watchdog_group": "crypto_watchdog",
        "enabled": True
    }
    r['lanes'].append(xau_lane)
    print("Added XAU lane")

with open(REGISTRY, 'w') as f:
    json.dump(r, f, indent=4)

wd = json.load(open(WATCHDOG))
crypto_group = wd.setdefault('groups', {}).setdefault('crypto_watchdog', {}).setdefault('lanes', [])
if xau_name not in crypto_group:
    crypto_group.append(xau_name)
    print("Added XAU lane to crypto_watchdog group")

with open(WATCHDOG, 'w') as f:
    json.dump(wd, f, indent=4)

print(f"Total lanes now: {len(r['lanes'])}")
