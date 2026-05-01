#!/usr/bin/env python3
"""
Detector Feedback Tracker — Measure Which Detectors Actually Work

Runs every 15 minutes, reads HH lane outcomes and detector outputs,
tracks per-detector accuracy: "when this detector fired, did the
recommended geometry produce or lose money?"

This is the feedback loop that tells us which detectors to trust.

Architecture:
1. Read current HH lane states (from event logs or MT5 positions)
2. Read detector outputs (zone state, weakness scores, structure)
3. For each detector firing in the past window: measure outcome
4. Track rolling accuracy per detector per symbol
5. Write: reports/detector_accuracy.json

Usage:
    python scripts/detector_feedback_tracker.py [--window-minutes 15]
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Detector Outcome Evaluator ────────────────────────────────────────

def evaluate_detector_outcome(detector_name: str, symbol: str, recommendation: dict, outcome_pnl: float) -> dict:
    """
    Evaluate whether a detector's recommendation was correct.

    Args:
        detector_name: which detector fired (e.g., "price_zone_detector")
        symbol: which symbol
        recommendation: what the detector recommended (from geometry output)
        outcome_pnl: the PnL from following that recommendation (positive = good)

    Returns:
        {
            "detector": str,
            "symbol": str,
            "recommendation": str,
            "outcome_pnl": float,
            "correct": bool,
            "confidence_delta": float,
        }
    """
    action = recommendation.get("action", "HOLD")
    
    # A detector is "correct" if:
    # - It recommended directional geometry AND the lane made money
    # - It recommended HOLD/neutral AND the lane didn't lose much
    # - It recommended escape/defensive AND losses were limited

    if action in ("HOLD", "WAIT", "SYMMETRIC_MODERATE"):
        # Neutral recommendation — correct if PnL is within acceptable range
        correct = outcome_pnl > -5.0  # Accept up to $5 loss
    elif action in ("BUY_TIGHT", "TIGHTEN_BUY", "RIDE_BREAKOUT"):
        # Bullish recommendation — correct if PnL is positive or small loss
        correct = outcome_pnl > -2.0  # Tighter tolerance for directional bets
    elif action in ("SELL_TIGHT", "TIGHTEN_SELL", "FLIP_TO_SELL"):
        # Bearish recommendation — correct if PnL is positive or small loss
        correct = outcome_pnl > -2.0
    elif action in ("SYMMETRIC_TIGHT", "TIGHT_AT_ZONE"):
        # Harvest mode — correct if any profit
        correct = outcome_pnl > 0
    elif "ESCAPE" in action or "KILL" in action:
        # Defensive action — correct if losses were limited
        correct = outcome_pnl > -10.0
    else:
        correct = outcome_pnl >= 0

    return {
        "detector": detector_name,
        "symbol": symbol,
        "recommendation": action,
        "outcome_pnl": round(outcome_pnl, 2),
        "correct": correct,
        "timestamp": utc_now_iso(),
    }


# ── Read Detector Outputs ────────────────────────────────────────────

REPORTS_DIR = Path(__file__).parent.parent / "reports"

DETECTOR_FILES = {
    "price_zone_detector": "price_zone_state.json",
    "leading_regime_detector": "leading_regime_weakness.json",
    "micro_oscillation_detector": "micro_oscillation_state.json",
    "swing_structure_detector": "swing_structure_state.json",
}


def read_detector_outputs() -> dict:
    """Read the latest output from all detectors."""
    outputs = {}
    for detector_name, filename in DETECTOR_FILES.items():
        filepath = REPORTS_DIR / filename
        if filepath.exists():
            try:
                outputs[detector_name] = json.loads(filepath.read_text(encoding="utf-8"))
            except Exception:
                outputs[detector_name] = {"error": f"Failed to read {filename}"}
        else:
            outputs[detector_name] = {"error": "File not found"}
    return outputs


# ── Read HH Lane Outcomes ────────────────────────────────────────────

def read_lane_outcomes() -> dict:
    """
    Read current HH lane outcomes from event logs.

    Returns: {symbol: {"recent_pnl": float, "recent_closes": int, "recent_resets": int}}
    """
    # For now, read from event log files if available
    # This is a placeholder — in production, read from actual event logs
    outcomes = {}
    
    # Look for HH event logs
    for event_file in REPORTS_DIR.glob("*_events.jsonl"):
        symbol = event_file.stem.replace("_events", "")
        try:
            lines = event_file.read_text(encoding="utf-8").strip().split("\n")
            recent_pnl = 0.0
            recent_closes = 0
            for line in lines[-100:]:  # Last 100 events
                event = json.loads(line)
                if event.get("event_type") == "close":
                    recent_closes += 1
                    recent_pnl += event.get("pnl", 0.0)
            outcomes[symbol] = {
                "recent_pnl": round(recent_pnl, 2),
                "recent_closes": recent_closes,
            }
        except Exception:
            pass

    return outcomes


# ── Main Tracker ──────────────────────────────────────────────────────

def run_tracker(window_minutes: int = 15) -> dict:
    """
    Run the detector feedback tracker.

    Returns: accuracy report per detector per symbol.
    """
    # Read detector outputs
    detector_outputs = read_detector_outputs()
    
    # Read lane outcomes
    lane_outcomes = read_lane_outcomes()

    # Build accuracy report
    accuracy_report = {
        "timestamp": utc_now_iso(),
        "window_minutes": window_minutes,
        "detectors": {},
    }

    for detector_name, data in detector_outputs.items():
        if "error" in data:
            accuracy_report["detectors"][detector_name] = {"status": "error", "message": data["error"]}
            continue

        # Track per-symbol accuracy
        symbol_accuracies = {}
        for symbol, symbol_data in data.items():
            if "error" in symbol_data:
                symbol_accuracies[symbol] = {"status": "error"}
                continue

            # Get the recommendation
            if "recommended_geometry" in symbol_data:
                recommendation = symbol_data["recommended_geometry"]
            elif "behavior" in symbol_data and "recommended_geometry" in symbol_data["behavior"]:
                recommendation = symbol_data["behavior"]["recommended_geometry"]
            elif "micro_regime" in symbol_data:
                recommendation = {"action": symbol_data.get("recommended_action", "HOLD")}
            else:
                recommendation = {"action": "HOLD"}

            # Get the outcome
            outcome = lane_outcomes.get(symbol, {"recent_pnl": 0.0, "recent_closes": 0})

            # Evaluate
            evaluation = evaluate_detector_outcome(
                detector_name, symbol, recommendation, outcome["recent_pnl"]
            )

            symbol_accuracies[symbol] = {
                "recommendation": evaluation["recommendation"],
                "outcome_pnl": evaluation["outcome_pnl"],
                "correct": evaluation["correct"],
                "timestamp": evaluation["timestamp"],
            }

        # Aggregate accuracy for this detector
        correct_count = sum(1 for s in symbol_accuracies.values() if s.get("correct"))
        total_count = sum(1 for s in symbol_accuracies.values() if "status" not in s)

        accuracy_report["detectors"][detector_name] = {
            "status": "active",
            "symbols_tracked": total_count,
            "correct_predictions": correct_count,
            "accuracy_pct": round(correct_count / total_count * 100, 1) if total_count > 0 else 0,
            "per_symbol": symbol_accuracies,
        }

    # Write report
    report_path = REPORTS_DIR / "detector_accuracy.json"
    with open(report_path, "w") as f:
        json.dump(accuracy_report, f, indent=2)

    return accuracy_report


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-minutes", type=int, default=15)
    args = parser.parse_args()

    report = run_tracker(window_minutes=args.window_minutes)

    print(f"Detector Accuracy Report ({args.window_minutes}-minute window)")
    print(f"Timestamp: {report['timestamp']}")
    print()
    print(f"{'Detector':<35} {'Symbols':>8} {'Correct':>8} {'Accuracy':>10}")
    print("-" * 65)

    for detector_name, data in report["detectors"].items():
        if data.get("status") == "error":
            print(f"{detector_name:<35} {'ERROR':>8} {'N/A':>8} {data['message']:<10}")
            continue

        symbols = data["symbols_tracked"]
        correct = data["correct_predictions"]
        accuracy = data["accuracy_pct"]
        print(f"{detector_name:<35} {symbols:>8} {correct:>8} {accuracy:>9.1f}%")

    print(f"\nSaved to {REPORTS_DIR / 'detector_accuracy.json'}")


if __name__ == "__main__":
    main()
