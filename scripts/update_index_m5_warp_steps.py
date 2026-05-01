#!/usr/bin/env python3
"""Update XAUUSD, NAS100, US30 M5 Warp shadow lanes with corrected ATR step values.

The earlier configs used stale ATR data. Live verification on 2026-04-14 shows:
- XAUUSD: M5 ATR = $5.93, step = $9.19 (1.55x), spread/step = 0.0283
- NAS100: M5 ATR = $15.98, step = $24.77 (1.55x), spread/step = 0.0371
- US30: M5 ATR = $26.25, step = $40.69 (1.55x), spread/step = 0.0361

Also fixes:
- Variant: rearm_lvl2_exc1 -> rearm_lvl2_exc2 (validated champion)
- Adds --fresh-start for clean bootstrap
- Adds --max-lattice-window-bars 240 safety valve
- Normalizes poll to 30s (shadow doesn't need 1s polling)
"""
import json

REGISTRY_PATH = "configs/penetration_lattice_runner_registry.json"

UPDATES = {
    "shadow_xauusd_m5_warp": {
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "XAUUSD",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "9.19",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_xauusd_m5_warp_state.json",
            "--event-path", "reports/penetration_lattice_shadow_xauusd_m5_warp_events.jsonl",
        ],
        "poll_seconds": 30,
    },
    "shadow_nas100_m5_warp": {
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "NAS100",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "24.77",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_nas100_m5_warp_state.json",
            "--event-path", "reports/penetration_lattice_shadow_nas100_m5_warp_events.jsonl",
        ],
        "poll_seconds": 30,
    },
    "shadow_us30_m5_warp": {
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "US30",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "40.69",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_us30_m5_warp_state.json",
            "--event-path", "reports/penetration_lattice_shadow_us30_m5_warp_events.jsonl",
        ],
        "poll_seconds": 30,
    },
}


def main():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)

    for lane in registry["lanes"]:
        name = lane.get("name", "")
        if name in UPDATES:
            update = UPDATES[name]
            old_args = lane.get("restart_args", [])
            old_step = "?"
            for i, arg in enumerate(old_args):
                if arg == "--step" and i + 1 < len(old_args):
                    old_step = old_args[i + 1]
            new_args = update["restart_args"]
            new_step = "?"
            for i, arg in enumerate(new_args):
                if arg == "--step" and i + 1 < len(new_args):
                    new_step = new_args[i + 1]
            lane.update(update)
            print(f"  UPDATED {name}: step {old_step} -> {new_step}")

    with open(REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(registry, f, indent=2)
        f.write("\n")

    print("\nRegistry updated successfully.")


if __name__ == "__main__":
    main()
