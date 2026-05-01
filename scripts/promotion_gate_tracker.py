#!/usr/bin/env python3
"""Shadow-to-Live Promotion Gate Tracker.

Tracks each shadow lane's progress through 5 promotion gates.
Updated automatically by monitoring scripts or manually by agents.

Usage:
    python scripts/promotion_gate_tracker.py                    # View current status
    python scripts/promotion_gate_tracker.py --update GBPUSD    # Update a gate
    python scripts/promotion_gate_tracker.py --report           # Full report
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER_PATH = ROOT / "reports" / "promotion_gates.json"

# === Gate Definitions ===
GATES = {
    "gate_1_profitability": {
        "name": "Profitability",
        "description": "Avg ≥ $0.10/close over 50+ closes",
        "criteria": {
            "min_avg_pnl_per_close": 0.10,
            "min_closes": 50,
        },
    },
    "gate_2_floating_risk": {
        "name": "Floating Risk",
        "description": "Max floating loss ≤ -$15 at any point",
        "criteria": {
            "max_floating_loss_usd": -15.0,
        },
    },
    "gate_3_reset_rate": {
        "name": "Reset Rate",
        "description": "≤ 2 resets per 100 closes",
        "criteria": {
            "max_resets_per_100_closes": 2.0,
        },
    },
    "gate_4_regime_resilience": {
        "name": "Regime Resilience",
        "description": "Survives at least 1 trend flip without catastrophic loss",
        "criteria": {
            "min_trend_flips_survived": 1,
            "max_loss_during_flip": -50.0,
        },
    },
    "gate_5_out_of_sample": {
        "name": "Out-of-Sample Validity",
        "description": "Test period performance ≥ 0.5× train period",
        "criteria": {
            "min_oos_ratio": 0.5,
        },
    },
}

# === Default Shadow Templates ===
DEFAULT_SHADOWS = {
    "GBPUSD_HH": {
        "symbol": "GBPUSD",
        "geometry": "SELL-tight (flag)",
        "timeframe": "M15",
        "shadow_since": "2026-04-14",
        "config": "hungry_hippo_gbpusd_deploy.json",
        "status": "running",
        "gates": {
            "gate_1_profitability": {"passed": False, "value": {"avg_pnl_per_close": 0.18, "closes": 44}, "note": "Positive but <50 closes"},
            "gate_2_floating_risk": {"passed": True, "value": {"max_floating_loss": -5.0}, "note": "Stable, no blowouts"},
            "gate_3_reset_rate": {"passed": True, "value": {"resets": 0, "closes": 44, "rate": 0.0}, "note": "Zero resets"},
            "gate_4_regime_resilience": {"passed": False, "value": None, "note": "Not yet tested — no trend flip observed"},
            "gate_5_out_of_sample": {"passed": False, "value": None, "note": "Not yet tested"},
        },
    },
    "EURUSD_HH": {
        "symbol": "EURUSD",
        "geometry": "Symmetric",
        "timeframe": "M15",
        "shadow_since": "2026-04-14",
        "config": "hungry_hippo_eurusd_live.json",
        "status": "running",
        "gates": {
            "gate_1_profitability": {"passed": False, "value": {"avg_pnl_per_close": 0.05, "closes": 89}, "note": "Positive but below $0.10 threshold"},
            "gate_2_floating_risk": {"passed": True, "value": {"max_floating_loss": -5.0}, "note": "Stable"},
            "gate_3_reset_rate": {"passed": True, "value": {"resets": 0, "closes": 89, "rate": 0.0}, "note": "Zero resets"},
            "gate_4_regime_resilience": {"passed": False, "value": None, "note": "Not yet tested"},
            "gate_5_out_of_sample": {"passed": False, "value": None, "note": "Not yet tested"},
        },
    },
    "US30_HH": {
        "symbol": "US30",
        "geometry": "BUY-tight",
        "timeframe": "M15",
        "shadow_since": "2026-04-15",
        "config": "hungry_hippo_us30_live.json",
        "status": "running",
        "gates": {
            "gate_1_profitability": {"passed": False, "value": {"avg_pnl_per_close": 1.13, "closes": 1}, "note": "Only 1 close — insufficient data"},
            "gate_2_floating_risk": {"passed": False, "value": None, "note": "Not enough data"},
            "gate_3_reset_rate": {"passed": False, "value": {"resets": 1, "closes": 1, "rate": 100.0}, "note": "1 reset in 1 close"},
            "gate_4_regime_resilience": {"passed": False, "value": None, "note": "Not yet tested"},
            "gate_5_out_of_sample": {"passed": False, "value": None, "note": "Not yet tested"},
        },
    },
    "ETH_M5_Step5": {
        "symbol": "ETHUSD",
        "geometry": "Symmetric step5",
        "timeframe": "M5",
        "shadow_since": None,
        "config": "hungry_hippo_ethusd_m5_step5_shadow.json",
        "status": "not_started",
        "note": "Config exists, enabled=false. Shadow rebuild showed +$157/20 closes but needs live shadow validation.",
        "gates": {
            "gate_1_profitability": {"passed": False, "value": None, "note": "Shadow probe positive but not running"},
            "gate_2_floating_risk": {"passed": False, "value": None, "note": "Not running"},
            "gate_3_reset_rate": {"passed": False, "value": None, "note": "Not running"},
            "gate_4_regime_resilience": {"passed": False, "value": None, "note": "Not running"},
            "gate_5_out_of_sample": {"passed": False, "value": None, "note": "Not running"},
        },
    },
    "NAS100_HH": {
        "symbol": "NAS100",
        "geometry": "BUY-tight (KILLED)",
        "timeframe": "M15",
        "shadow_since": "2026-04-15",
        "killed_at": "2026-04-15T01:40:00Z",
        "config": "hungry_hippo_nas100_live.json",
        "status": "killed",
        "note": "KILLED: 102 closes, -$265.74, 7 resets. Trend reversal destroyed BUY-tight geometry.",
        "gates": {
            "gate_1_profitability": {"passed": False, "value": {"avg_pnl_per_close": -2.61, "closes": 102}, "note": "Catastrophic loss"},
            "gate_2_floating_risk": {"passed": False, "value": {"max_floating_loss": -100.0}, "note": "Exceeded -$15 threshold"},
            "gate_3_reset_rate": {"passed": False, "value": {"resets": 7, "closes": 102, "rate": 6.9}, "note": "6.9 resets/100 closes > 2.0 threshold"},
            "gate_4_regime_resilience": {"passed": False, "value": {"trend_flips": 1, "loss_during_flip": -265.0}, "note": "Failed trend flip — lost $265"},
            "gate_5_out_of_sample": {"passed": False, "value": None, "note": "Not tested — failed earlier gates"},
        },
    },
    "USDJPY_gap2": {
        "symbol": "USDJPY",
        "geometry": "Symmetric gap2",
        "timeframe": "M15",
        "config": "unknown",
        "status": "quarantined",
        "note": "QUARANTINED: 528 closes, -$21.22, 237 resets. Chronic loser — should not be resumed.",
        "gates": {
            "gate_1_profitability": {"passed": False, "value": {"avg_pnl_per_close": -0.04, "closes": 528}, "note": "Consistently losing"},
            "gate_2_floating_risk": {"passed": False, "value": {"max_floating_loss": -25.0}, "note": "Exceeded threshold"},
            "gate_3_reset_rate": {"passed": False, "value": {"resets": 237, "closes": 528, "rate": 44.9}, "note": "44.9 resets/100 closes — catastrophic"},
            "gate_4_regime_resilience": {"passed": False, "value": None, "note": "Doesn't matter — already failed"},
            "gate_5_out_of_sample": {"passed": False, "value": None, "note": "Not tested"},
        },
    },
}


def load_tracker() -> dict:
    """Load tracker from file, or create from defaults."""
    if TRACKER_PATH.exists():
        with open(TRACKER_PATH) as f:
            return json.load(f)
    return {"shadows": DEFAULT_SHADOWS, "last_updated": datetime.now(timezone.utc).isoformat()}


def save_tracker(data: dict):
    """Save tracker to file."""
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_PATH, "w") as f:
        json.dump(data, f, indent=2)


def check_gate_passed(gate_name: str, value: dict) -> bool:
    """Check if a gate's value meets the criteria."""
    criteria = GATES[gate_name]["criteria"]
    if value is None:
        return False

    if gate_name == "gate_1_profitability":
        return (
            value.get("avg_pnl_per_close", 0) >= criteria["min_avg_pnl_per_close"]
            and value.get("closes", 0) >= criteria["min_closes"]
        )
    elif gate_name == "gate_2_floating_risk":
        return value.get("max_floating_loss", -999) >= criteria["max_floating_loss_usd"]
    elif gate_name == "gate_3_reset_rate":
        rate = value.get("rate", 999)
        return rate <= criteria["max_resets_per_100_closes"]
    elif gate_name == "gate_4_regime_resilience":
        return (
            value.get("trend_flips_survived", 0) >= criteria["min_trend_flips_survived"]
            and value.get("loss_during_flip", -999) >= criteria["max_loss_during_flip"]
        )
    elif gate_name == "gate_5_out_of_sample":
        return value.get("oos_ratio", 0) >= criteria["min_oos_ratio"]
    return False


def update_gate(shadow_name: str, gate_name: str, value: dict, note: str = ""):
    """Update a gate for a shadow."""
    tracker = load_tracker()
    shadow = tracker["shadows"].get(shadow_name)
    if shadow is None:
        print(f"ERROR: Shadow '{shadow_name}' not found in tracker")
        return

    gate = shadow["gates"].get(gate_name)
    if gate is None:
        print(f"ERROR: Gate '{gate_name}' not found for shadow '{shadow_name}'")
        return

    gate["value"] = value
    gate["passed"] = check_gate_passed(gate_name, value)
    if note:
        gate["note"] = note

    save_tracker(tracker)
    print(f"Updated {shadow_name}.{gate_name}: passed={gate['passed']}, value={value}")


def status_report():
    """Print a full status report."""
    tracker = load_tracker()
    
    print("=" * 120)
    print(f"SHADOW-TO-LIVE PROMOTION GATE TRACKER — Last updated: {tracker.get('last_updated', 'Never')}")
    print("=" * 120)
    print()

    # Summary table
    print(f"{'Shadow':<15} {'Symbol':<10} {'Geometry':<25} {'Status':<15} {'Gates':<10} {'Blocked By'}")
    print("-" * 120)

    for name, shadow in tracker["shadows"].items():
        gates_passed = sum(1 for g in shadow["gates"].values() if g.get("passed", False))
        total_gates = len(shadow["gates"])
        blocked_by = [
            GATES[gname]["name"]
            for gname, g in shadow["gates"].items()
            if not g.get("passed", False)
        ]
        blocked_str = ", ".join(blocked_by[:2])
        if len(blocked_by) > 2:
            blocked_str += f" +{len(blocked_by)-2} more"

        status = shadow.get("status", "unknown")
        geometry = shadow.get("geometry", "unknown")
        symbol = shadow.get("symbol", "unknown")

        print(f"{name:<15} {symbol:<10} {geometry:<25} {status:<15} {gates_passed}/{total_gates:<5} {blocked_str}")

    print()

    # Detailed gate breakdown per shadow
    for name, shadow in tracker["shadows"].items():
        if shadow.get("status") == "killed" or shadow.get("status") == "quarantined":
            print(f"\n{'='*80}")
            print(f"  {name} [{shadow['status'].upper()}]")
            print(f"  Note: {shadow.get('note', 'N/A')}")
            print(f"{'='*80}")
            continue

        gates_passed = sum(1 for g in shadow["gates"].values() if g.get("passed", False))
        total_gates = len(shadow["gates"])

        print(f"\n{'─'*80}")
        print(f"  {name} ({shadow['symbol']}) — {gates_passed}/{total_gates} gates passed")
        print(f"{'─'*80}")

        for gname, g in shadow["gates"].items():
            status_icon = "✅" if g.get("passed") else "❌"
            gate_def = GATES[gname]
            val_str = json.dumps(g.get("value")) if g.get("value") else "N/A"
            print(f"  {status_icon} {gate_def['name']}: {gate_def['description']}")
            print(f"     Value: {val_str}")
            print(f"     Note: {g.get('note', 'N/A')}")
            print()

    # Verdict
    print(f"\n{'='*120}")
    promotable = [
        name for name, shadow in tracker["shadows"].items()
        if all(g.get("passed") for g in shadow["gates"].values())
        and shadow.get("status") not in ("killed", "quarantined")
    ]
    if promotable:
        print(f"  🚀 PROMOTABLE TO LIVE: {', '.join(promotable)}")
    else:
        print(f"  🚫 NO SHADOWS READY FOR LIVE PROMOTION")
        print(f"     All shadows blocked by one or more gates.")
    print(f"{'='*120}")


def auto_update_from_state_files():
    """Auto-update gates from state files and running processes.
    
    This is called by the monitoring script to refresh gate status.
    """
    tracker = load_tracker()

    # Try to read HH state files
    for shadow_name, shadow in tracker["shadows"].items():
        symbol = shadow.get("symbol", "")
        if not symbol:
            continue

        # Look for state files
        state_path = ROOT / "states" / f"lattice_state_{shadow_name.lower()}.json"
        if state_path.exists():
            try:
                with open(state_path) as f:
                    state = json.load(f)

                # Update gate 1: profitability
                realized = state.get("realized_net_usd", 0)
                closes = state.get("realized_closes", 0)
                avg_pnl = realized / closes if closes > 0 else 0

                # Update gate 2: floating risk
                floating = state.get("max_floating_loss_usd", 0)

                # Update gate 3: resets
                resets = state.get("resets", 0)
                reset_rate = (resets / closes * 100) if closes > 0 else 0

                # Apply updates
                if closes > 0:
                    tracker["shadows"][shadow_name]["gates"]["gate_1_profitability"]["value"] = {
                        "avg_pnl_per_close": round(avg_pnl, 2),
                        "closes": closes,
                    }
                    tracker["shadows"][shadow_name]["gates"]["gate_1_profitability"]["passed"] = check_gate_passed(
                        "gate_1_profitability",
                        {"avg_pnl_per_close": avg_pnl, "closes": closes},
                    )

                    tracker["shadows"][shadow_name]["gates"]["gate_2_floating_risk"]["value"] = {
                        "max_floating_loss": round(floating, 2),
                    }
                    tracker["shadows"][shadow_name]["gates"]["gate_2_floating_risk"]["passed"] = check_gate_passed(
                        "gate_2_floating_risk",
                        {"max_floating_loss": floating},
                    )

                    tracker["shadows"][shadow_name]["gates"]["gate_3_reset_rate"]["value"] = {
                        "resets": resets,
                        "closes": closes,
                        "rate": round(reset_rate, 1),
                    }
                    tracker["shadows"][shadow_name]["gates"]["gate_3_reset_rate"]["passed"] = check_gate_passed(
                        "gate_3_reset_rate",
                        {"resets": resets, "closes": closes, "rate": reset_rate},
                    )
            except Exception as e:
                print(f"  Warning: Could not read state for {shadow_name}: {e}")

    save_tracker(tracker)
    print(f"  Auto-updated {len(tracker['shadows'])} shadows from state files")


def main():
    parser = argparse.ArgumentParser(description="Shadow-to-Live Promotion Gate Tracker")
    parser.add_argument("--update", type=str, help="Update a specific shadow (name)")
    parser.add_argument("--gate", type=str, help="Gate name to update (e.g. gate_1_profitability)")
    parser.add_argument("--value", type=str, help="JSON value for the gate")
    parser.add_argument("--note", type=str, default="", help="Note for the gate update")
    parser.add_argument("--report", action="store_true", help="Print full status report")
    parser.add_argument("--auto-update", action="store_true", help="Auto-update from state files")
    args = parser.parse_args()

    if args.update and args.gate and args.value:
        value = json.loads(args.value)
        update_gate(args.update, args.gate, value, args.note)
    elif args.report:
        status_report()
    elif args.auto_update:
        auto_update_from_state_files()
    else:
        status_report()


if __name__ == "__main__":
    main()
