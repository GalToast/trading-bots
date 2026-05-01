#!/usr/bin/env python3
"""
FX M15 Retune Plan: Apply winning close-out pattern (alpha=0.5, mom=OFF)
Based on analysis showing FX rearm 941777 pattern is 2.3x better than momentum pattern.
"""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "penetration_lattice_runner_registry.json"

# Retune specs based on winning pattern
RETUNES = {
    "shadow_gbpusd_m15_warp": {
        "step": 0.00020,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
    "shadow_usdjpy_m15_warp": {
        "step": 0.035,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
    "shadow_audusd_m15_warp": {
        "step": 0.00020,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
    "shadow_eurusd_m15_warp": {
        "step": 0.00025,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
    "shadow_nzdusd_m15_warp": {
        "step": 0.00015,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
    "shadow_usdcad_m15_warp": {
        "step": 0.00020,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
    "shadow_xauusd_m15_warp": {
        "step": 8.0,
        "raw_close_alpha": 0.5,
        "raw_rearm_momentum_gate": False,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "max_open_per_side": 12,
    },
}

def main():
    with open(REGISTRY) as f:
        data = json.load(f)

    changes = []
    for lane in data.get("lanes", []):
        name = lane["name"]
        if name not in RETUNES:
            continue
        spec = RETUNES[name]
        before = {}
        # Update restart_args
        if "restart_args" in lane:
            args = lane["restart_args"]
            for i, arg in enumerate(args):
                if arg == "--step" and i + 1 < len(args):
                    old = float(args[i + 1])
                    new = spec["step"]
                    if old != new:
                        before["step"] = old
                        args[i + 1] = str(new)
                if arg == "--raw-close-alpha" and i + 1 < len(args):
                    old = float(args[i + 1])
                    new = spec["raw_close_alpha"]
                    if old != new:
                        before["raw_close_alpha"] = old
                        args[i + 1] = str(new)
                if arg == "--raw-rearm-variant" and i + 1 < len(args):
                    old = args[i + 1]
                    new = spec["raw_rearm_variant"]
                    if old != new:
                        before["variant"] = old
                        args[i + 1] = new
                if arg == "--max-open-per-side" and i + 1 < len(args):
                    old = int(args[i + 1])
                    new = spec["max_open_per_side"]
                    if old != new:
                        before["max_open"] = old
                        args[i + 1] = str(new)
            # Handle momentum gate flag
            has_mom_gate = "--raw-rearm-momentum-gate" in args
            want_mom_gate = spec["raw_rearm_momentum_gate"]
            if has_mom_gate != want_mom_gate:
                before["momentum_gate"] = has_mom_gate
                if want_mom_gate:
                    args.append("--raw-rearm-momentum-gate")
                else:
                    args.remove("--raw-rearm-momentum-gate")

        if before:
            changes.append({"lane": name, "before": before, "after": spec})

    if changes:
        print(json.dumps(changes, indent=2))
        print(f"\n{len(changes)} lanes would change.")
    else:
        print("No changes needed — all lanes already match target config.")

if __name__ == "__main__":
    main()
