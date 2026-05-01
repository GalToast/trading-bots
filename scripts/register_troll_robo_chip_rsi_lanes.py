#!/usr/bin/env python3
"""Add TROLL-USD and ROBO-USD RSI7 shadow lanes to the registry and watchdog.

CHIP-USD is excluded because it has no candle cache yet.
ROBO-USD has only 7d cache (partial validation possible).
TROLL-USD has full 30d cache — safest first launch.

This script:
1. Adds entries to configs/coinbase_rsi_bundle_shadow.json
2. Adds entries to configs/penetration_lattice_runner_registry.json  
3. Adds entries to configs/watchdog_groups.json under the RSI lane group
4. Regenerates the RSI scoreboard

Dry-run by default. Use --apply to actually write files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_PATH = ROOT / "configs" / "coinbase_rsi_bundle_shadow.json"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_PATH = ROOT / "configs" / "watchdog_groups.json"

CANDIDATES = [
    {
        "product": "TROLL-USD",
        "lane": "shadow_coinbase_trollusd_rsi7",
        "config_path": "configs/coinbase_rsi_shadow_trollusd_candidate.json",
        "state": "reports/coinbase_rsi_shadow_trollusd_state.json",
        "events": "reports/coinbase_rsi_shadow_trollusd_events.jsonl",
        "cache": "30d + 7d ✅",
    },
    {
        "product": "ROBO-USD",
        "lane": "shadow_coinbase_robousd_rsi7",
        "config_path": "configs/coinbase_rsi_shadow_robousd_candidate.json",
        "state": "reports/coinbase_rsi_shadow_robousd_state.json",
        "events": "reports/coinbase_rsi_shadow_robousd_events.jsonl",
        "cache": "7d only ⚠️",
    },
    # CHIP-USD excluded — no candle cache
]

RSI_BUNDLE_ENTRY_TEMPLATE = {
    "lane_name": "{lane}",
    "product_id": "{product}",
    "state_path": "{state}",
    "event_path": "{events}",
    "rsi_period": 7,
    "oversold": 30,
    "overbought": 70,
    "profit_target_pct": 0.02,
    "stop_loss_pct": 0.003,
    "max_hold_bars": 48,
    "maker_fee_bps": 120,
    "fee_model": "coinbase_spot_account_taker_intro1_120bps_per_side",
    "deploy_pct": 0.9,
    "starting_cash": 48,
    "granularity": "FIVE_MINUTE",
}

REGISTRY_ENTRY_TEMPLATE = {
    "name": "{lane}",
    "kind": "shadow_coinbase_spot",
    "state_path": "{state}",
    "event_path": "{events}",
    "poll_seconds": 30,
    "stale_after_seconds": 180,
    "startup_grace_seconds": 120,
    "restart_group": "shadow_coinbase_rsi_bundle_v1",
    "process_match_substrings": [
        "scripts/live_coinbase_rsi_bundle_shadow.py",
        "configs/coinbase_rsi_bundle_shadow.json",
    ],
    "restart_args": [
        "scripts/live_coinbase_rsi_bundle_shadow.py",
        "--config-path",
        "configs/coinbase_rsi_bundle_shadow.json",
    ],
}

WATCHDOG_GROUP_KEY = "coinbase_rsi_bundle"


def main():
    apply = "--apply" in sys.argv

    print("=== Coinbase RSI Shadow Lane Registration ===")
    print(f"Mode: {'APPLY (writing files)' if apply else 'DRY-RUN (no writes)'}")
    print()

    for c in CANDIDATES:
        print(f"--- {c['product']} ({c['lane']}) ---")
        print(f"  Cache: {c['cache']}")
        print(f"  Config: {c['config_path']}")
        print(f"  State: {c['state']}")
        print(f"  Events: {c['events']}")
        print()

    if apply:
        # Add to bundle
        bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
        for c in CANDIDATES:
            entry = RSI_BUNDLE_ENTRY_TEMPLATE.copy()
            entry["lane_name"] = c["lane"]
            entry["product_id"] = c["product"]
            entry["state_path"] = c["state"]
            entry["event_path"] = c["events"]
            bundle.append(entry)
        BUNDLE_PATH.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        print(f"✅ Wrote {BUNDLE_PATH} (+{len(CANDIDATES)} entries)")

        # Add to registry
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        for c in CANDIDATES:
            entry = REGISTRY_ENTRY_TEMPLATE.copy()
            for k, v in entry.items():
                if isinstance(v, str):
                    entry[k] = v.format(**c)
                elif isinstance(v, list):
                    entry[k] = [item.format(**c) if isinstance(item, str) else item for item in v]
            registry.append(entry)
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        print(f"✅ Wrote {REGISTRY_PATH} (+{len(CANDIDATES)} entries)")

        # Add to watchdog
        watchdog = json.loads(WATCHDOG_PATH.read_text(encoding="utf-8"))
        if WATCHDOG_GROUP_KEY not in watchdog.get("groups", {}):
            # Find the group that contains the existing RSI lanes
            for gk, gv in watchdog["groups"].items():
                if "shadow_coinbase_raveusd_rsi7" in gv.get("lanes", []):
                    WATCHDOG_GROUP_KEY = gk
                    break
        if WATCHDOG_GROUP_KEY in watchdog["groups"]:
            for c in CANDIDATES:
                if c["lane"] not in watchdog["groups"][WATCHDOG_GROUP_KEY]["lanes"]:
                    watchdog["groups"][WATCHDOG_GROUP_KEY]["lanes"].append(c["lane"])
            WATCHDOG_PATH.write_text(json.dumps(watchdog, indent=2) + "\n", encoding="utf-8")
            print(f"✅ Wrote {WATCHDOG_PATH} (+{len(CANDIDATES)} lanes to {WATCHDOG_GROUP_KEY})")
        else:
            print(f"⚠️ Could not find RSI watchdog group. Manual step needed.")

        print()
        print("Next steps:")
        print("  1. Restart the RSI bundle runner to pick up new lanes")
        print("  2. Run: python scripts/build_coinbase_spot_rsi_scoreboard.py")
        print("  3. Monitor: reports/coinbase_spot_rsi_scoreboard.md")
    else:
        print("Run with --apply to write changes.")


if __name__ == "__main__":
    main()
