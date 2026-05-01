"""Add GBPUSD M5 Warp shadow to registry and watchdog."""
import json

# Registry entry
gbpusd_m5_entry = {
    "name": "shadow_gbpusd_m5_warp",
    "kind": "shadow_fx",
    "state_path": "reports/penetration_lattice_shadow_gbpusd_m5_warp_state.json",
    "event_path": "reports/penetration_lattice_shadow_gbpusd_m5_warp_events.jsonl",
    "poll_seconds": 30,
    "stale_after_seconds": 180,
    "process_match_substrings": [
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "reports/penetration_lattice_shadow_gbpusd_m5_warp_state.json"
    ],
    "restart_args": [
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol", "GBPUSD",
        "--fresh-start",
        "--timeframe", "M5",
        "--step", "0.000337",
        "--max-open-per-side", "12",
        "--raw-close-alpha", "1.0",
        "--raw-rearm-variant", "rearm_lvl2_exc1",
        "--raw-sell-gap", "1",
        "--raw-buy-gap", "1",
        "--poll-seconds", "30",
        "--max-floating-loss-usd", "-15.0",
        "--max-lattice-window-bars", "240",
        "--state-path", "reports/penetration_lattice_shadow_gbpusd_m5_warp_state.json",
        "--event-path", "reports/penetration_lattice_shadow_gbpusd_m5_warp_events.jsonl"
    ]
}

# Add to registry
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
reg['lanes'].append(gbpusd_m5_entry)
json.dump(reg, open('configs/penetration_lattice_runner_registry.json', 'w'), indent=2)
print("✅ Added to registry")

# Add to fx_watchdog group
wg = json.load(open('configs/watchdog_groups.json'))
wg['groups']['fx_watchdog']['lanes'].append('shadow_gbpusd_m5_warp')
json.dump(wg, open('configs/watchdog_groups.json', 'w'), indent=2)
print("✅ Added to fx_watchdog group")

# Validate
json.load(open('configs/penetration_lattice_runner_registry.json'))
json.load(open('configs/watchdog_groups.json'))
print("✅ Both configs valid JSON")

print(f"\nLaunch command:")
print(f"python scripts/live_penetration_lattice_tick_crypto_shadow.py --symbol GBPUSD --fresh-start --timeframe M5 --step 0.000337 --max-open-per-side 12 --raw-close-alpha 1.0 --raw-rearm-variant rearm_lvl2_exc1 --raw-sell-gap 1 --raw-buy-gap 1 --poll-seconds 30 --max-floating-loss-usd -15.0 --max-lattice-window-bars 240 --state-path reports/penetration_lattice_shadow_gbpusd_m5_warp_state.json --event-path reports/penetration_lattice_shadow_gbpusd_m5_warp_events.jsonl")
