#!/usr/bin/env python3
"""
FX M5 Warp Coefficient Sweep Tracker
=====================================
Polls all FX M5 Warp state files, compares 1.0x vs 1.5x ATR coefficients,
and produces a ranked table by $/close, reset rate, and spread efficiency.

Usage:
    python scripts/fx_m5_coefficient_sweep_tracker.py              # One-shot snapshot
    python scripts/fx_m5_coefficient_sweep_tracker.py --loop       # Poll every 3 min
    python scripts/fx_m5_coefficient_sweep_tracker.py --output reports/fx_m5_coefficient_sweep.md
"""
import json
import time
import argparse
import glob
import os
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent

# State file patterns for all FX M5 Warp lanes
STATE_PATTERNS = [
    # GBPUSD lanes
    "*gbpusd*m5*state.json",
    "*gbpusd*m5*1*state.json",
    # USDJPY lanes
    "*usdjpy*m5*state.json",
    "*usdjpy*m5*1*state.json",
    # Other FX
    "*audusd*m5*state.json",
    "*eurusd*m5*state.json",
    "*nzdusd*m5*state.json",
    "*usdcad*m5*state.json",
    # Indices/Gold
    "*xauusd*m5*state.json",
    "*nas100*m5*state.json",
    "*us30*m5*state.json",
]

# Coefficient mapping (known lane configs)
COEFF_MAP = {
    "1.0x": ["1x", "1.0", "1.0x"],
    "1.5x": ["1.5x", "1.5", ""],  # default is 1.5x if no coefficient suffix
}

def detect_coefficient(filename: str) -> str:
    """Detect coefficient from filename. 1.0x if '1x' or '1.0' present, else 1.5x."""
    fname = filename.lower()
    if "1x" in fname or "1.0" in fname:
        return "1.0x"
    return "1.5x"

def detect_symbol(filename: str) -> str:
    """Extract symbol from filename."""
    fname = filename.lower()
    for sym in ["gbpusd", "usdjpy", "audusd", "eurusd", "nzdusd", "usdcad", "xauusd", "nas100", "us30"]:
        if sym in fname:
            return sym.upper()
    return "UNKNOWN"

def load_state(filepath: str) -> dict:
    """Load a state file, returning dict or empty on error."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def extract_metrics(state: dict) -> dict:
    """Extract key metrics from a state file."""
    if not state:
        return {}
    
    # Get symbol data
    symbols = state.get("symbols", {})
    if not symbols:
        return {}
    
    sym_data = list(symbols.values())[0]
    
    realized_closes = sym_data.get("realized_closes", 0)
    realized_net = sym_data.get("realized_net_usd", 0.0)
    anchor_resets = sym_data.get("anchor_resets", 0)
    open_tickets = sym_data.get("open_tickets", [])
    base_step = sym_data.get("base_step_px", 0)
    anchor = sym_data.get("anchor", 0)
    timeframe = sym_data.get("timeframe", "?")
    
    # Heartbeat
    heartbeat = state.get("runner", {}).get("heartbeat_at", "unknown")
    
    # Derived metrics
    per_close = realized_net / realized_closes if realized_closes > 0 else 0
    reset_rate = anchor_resets / max(realized_closes, 1)
    
    return {
        "closes": realized_closes,
        "net": realized_net,
        "per_close": per_close,
        "resets": anchor_resets,
        "reset_rate": reset_rate,
        "open": len(open_tickets),
        "step": base_step,
        "anchor": anchor,
        "timeframe": timeframe,
        "heartbeat": heartbeat,
    }

def main():
    parser = argparse.ArgumentParser(description="FX M5 Warp Coefficient Sweep Tracker")
    parser.add_argument("--loop", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=int, default=180, help="Poll interval in seconds (default: 180)")
    parser.add_argument("--output", type=str, help="Output file path (default: stdout)")
    args = parser.parse_args()
    
    reports_dir = REPO / "reports"
    
    def run_snapshot():
        lanes = []
        
        for pattern in STATE_PATTERNS:
            matches = glob.glob(str(reports_dir / pattern))
            for filepath in matches:
                # Skip events/history files
                if "event" in filepath.lower() or "history" in filepath.lower():
                    continue
                if not filepath.endswith("_state.json"):
                    continue
                
                filename = os.path.basename(filepath)
                state = load_state(filepath)
                if not state:
                    continue
                
                metrics = extract_metrics(state)
                if not metrics:
                    continue
                
                symbol = detect_symbol(filename)
                coeff = detect_coefficient(filename)
                
                lanes.append({
                    "symbol": symbol,
                    "coefficient": coeff,
                    "filepath": os.path.basename(filepath),
                    **metrics,
                })
        
        # Sort by symbol, then coefficient
        lanes.sort(key=lambda x: (x["symbol"], x["coefficient"]))
        
        # Print table
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = []
        lines.append(f"# FX M5 Warp Coefficient Sweep — {now}\n")
        lines.append(f"| Symbol | Coeff | Step | Closes | Net $ | $/close | Open | Resets | Reset Rate | Status |")
        lines.append(f"|--------|-------|------|--------|-------|---------|------|--------|------------|--------|")
        
        for lane in lanes:
            status = "✅" if lane["closes"] > 0 and lane["net"] > 0 else "⏳"
            if lane["closes"] > 0 and lane["net"] < 0:
                status = "❌"
            if lane["open"] > 0 and lane["closes"] == 0:
                status = "🔄"
            
            lines.append(
                f"| {lane['symbol']} | {lane['coefficient']} | {lane['step']:.5f} | "
                f"{lane['closes']} | ${lane['net']:+.2f} | ${lane['per_close']:.2f} | "
                f"{lane['open']} | {lane['resets']} | {lane['reset_rate']:.2f} | {status} |"
            )
        
        # Summary: 1.0x vs 1.5x comparison
        lines.append("\n## Coefficient Comparison\n")
        coeff_1x = [l for l in lanes if l["coefficient"] == "1.0x"]
        coeff_15x = [l for l in lanes if l["coefficient"] == "1.5x"]
        
        closes_1x = sum(l["closes"] for l in coeff_1x)
        closes_15x = sum(l["closes"] for l in coeff_15x)
        net_1x = sum(l["net"] for l in coeff_1x)
        net_15x = sum(l["net"] for l in coeff_15x)
        opens_1x = sum(l["open"] for l in coeff_1x)
        opens_15x = sum(l["open"] for l in coeff_15x)
        
        lines.append(f"| Metric | 1.0× ATR | 1.5× ATR |")
        lines.append(f"|--------|----------|----------|")
        lines.append(f"| Lanes | {len(coeff_1x)} | {len(coeff_15x)} |")
        lines.append(f"| Total Closes | {closes_1x} | {closes_15x} |")
        lines.append(f"| Total Net $ | ${net_1x:+.2f} | ${net_15x:+.2f} |")
        avg_1x = f"${net_1x/closes_1x:.2f}" if closes_1x > 0 else "N/A"
        avg_15x = f"${net_15x/closes_15x:.2f}" if closes_15x > 0 else "N/A"
        lines.append(f"| Avg $/close | {avg_1x} | {avg_15x} |")
        lines.append(f"| Lanes with Opens | {sum(1 for l in coeff_1x if l['open'] > 0)} | {sum(1 for l in coeff_15x if l['open'] > 0)} |")
        
        if closes_1x > 0 and closes_15x > 0:
            lines.append(f"\n**Verdict:** {'1.0x ATR is better' if net_1x/closes_1x > net_15x/closes_15x else '1.5x ATR is better'} based on $/close")
        elif closes_1x > 0:
            lines.append(f"\n**Verdict:** 1.0x ATR is the only coefficient with closes so far")
        elif closes_15x > 0:
            lines.append(f"\n**Verdict:** 1.5x ATR is the only coefficient with closes so far")
        else:
            lines.append(f"\n**Verdict:** Awaiting first closes from both coefficients")
        
        output = "\n".join(lines)
        
        if args.output:
            output_path = REPO / args.output
            output_path.write_text(output, encoding='utf-8')
            print(f"Saved to {output_path}")
        else:
            print(output)
        
        return lanes
    
    # Run once
    lanes = run_snapshot()
    
    if args.loop:
        print(f"\nPolling every {args.interval}s...")
        while True:
            time.sleep(args.interval)
            run_snapshot()

if __name__ == "__main__":
    main()
