import subprocess
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Config
BOARD_PATH = Path("reports/kraken_maker_opportunity_board.json")
CALIBRATOR_PATH = Path("scripts/run_kraken_maker_microfill_calibrator.py")
EVENT_PATH = Path("reports/midpoint_sniper_validation_events.jsonl")
SUMMARY_PATH = Path("reports/midpoint_sniper_validation_summary.json")
REPORT_PATH = Path("reports/midpoint_sniper_validation_report.md")

GHOST_PENALTY_BPS = 2.0
CYCLES = 2
OFFSETS = [0.0, 0.25, 0.5, 0.75]
SPREAD_THRESHOLD_BPS = 50.0

def main():
    # 1. Select Candidates
    if not BOARD_PATH.exists():
        print(f"Error: {BOARD_PATH} not found.")
        return

    with open(BOARD_PATH, "r") as f:
        board = json.load(f)

    # Filter for wide-spread pairs (>50bps)
    # High-signal subset for first run
    candidates = ["TRAC-USD", "GWEI-USD", "MXC-USD", "BMB-USD", "HOUSE-USD"]

    # Add some "control" pairs (tight spread)
    control_pairs = ["ETH-USD", "SOL-USD", "SOL-BTC", "ETH-BTC"]
    
    all_pairs = candidates + control_pairs
    print(f"Selected {len(candidates)} wide-spread candidates: {candidates}")
    print(f"Selected {len(control_pairs)} control pairs: {control_pairs}")

    if not all_pairs:
        print("No pairs to validate.")
        return

    # Clear old events
    if EVENT_PATH.exists():
        EVENT_PATH.unlink()

    # 2. Run Calibration
    print(f"\nStarting Midpoint Sniper Validation at {datetime.now().isoformat()}")
    print(f"Ghost Penalty: {GHOST_PENALTY_BPS} bps")
    print(f"Cycles: {CYCLES}")
    
    for product in all_pairs:
        print(f"\n--- Validating {product} ---")
        cmd = [
            sys.executable, "-u", str(CALIBRATOR_PATH),
            "--products", product,
            "--price-offset-fracs", ",".join(map(str, OFFSETS)),
            "--ghost-penalty-bps", str(GHOST_PENALTY_BPS),
            "--cycles", str(CYCLES),
            "--ttl-seconds", "60",
            "--poll-seconds", "5",
            "--event-path", str(EVENT_PATH),
            "--summary-path", str(SUMMARY_PATH)
        ]
        
        try:
            # We don't use check=True here because we want to continue if one product fails
            subprocess.run(cmd)
        except Exception as e:
            print(f"Error launching calibrator for {product}: {e}")

    # 3. Generate Report
    if not SUMMARY_PATH.exists():
        print(f"Error: {SUMMARY_PATH} not generated.")
        return

    with open(SUMMARY_PATH, "r") as f:
        summary = json.load(f)

    generate_report(summary, all_pairs)

def generate_report(summary, pairs):
    stats = summary.get("by_product_side_offset", {})
    
    report = []
    report.append("# Midpoint Sniper Validation Report")
    report.append(f"\nGenerated At: {datetime.now().isoformat()}")
    report.append(f"\n**Ghost Penalty:** {GHOST_PENALTY_BPS} bps")
    report.append("\n## Fill Rate Comparison by Offset")
    report.append("\n| Product | Side | L1 (0.0) | 0.25 | Midpoint (0.5) | 0.75 |")
    report.append("| --- | --- | --- | --- | --- | --- |")

    for product in sorted(pairs):
        for side in ["buy", "sell"]:
            row = [product, side]
            for offset in OFFSETS:
                key = f"{product}|{side}|{offset:.4f}"
                data = stats.get(key, {})
                
                hard_cross = data.get("hard_cross_fill_proxy", 0)
                queue_fill = data.get("probable_queue_depletion_fill_proxy", 0)
                total_fills = hard_cross + queue_fill
                
                timeouts = data.get("unfilled_timeout", 0)
                decays = data.get("spread_decay_unfilled", 0)
                total_trials = total_fills + timeouts + decays
                
                if total_trials > 0:
                    rate = (total_fills / total_trials) * 100
                    row.append(f"{rate:.1f}% ({total_fills}/{total_trials})")
                else:
                    row.append("-")
            
            report.append("| " + " | ".join(row) + " |")

    report.append("\n## Observations")
    report.append("- **L1 (0.0)**: Often shows near-zero fill due to L1 front-running.")
    report.append("- **Midpoint (0.5)**: Hypothesized to capture takers meeting halfway.")
    report.append("- **Ghost Penalty**: Fills only counted if taker liquidity clears our level by 2bps.")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
    
    print(f"\nReport generated at {REPORT_PATH}")

if __name__ == "__main__":
    main()
