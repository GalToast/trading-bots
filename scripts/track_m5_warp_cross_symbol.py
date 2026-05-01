#!/usr/bin/env python3
"""
M5 Warp Cross-Symbol Live Tracker
==================================
Polls BTC, ETH, and SOL M5 Warp state files every 5 minutes,
tracks close counts, $/close, $/close/hour, open positions, and reset rates.

Usage:
    python scripts/track_m5_warp_cross_symbol.py              # One-shot snapshot
    python scripts/track_m5_warp_cross_symbol.py --loop       # Poll every 5 min
    python scripts/track_m5_warp_cross_symbol.py --loop --interval 120  # Custom interval
"""
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent

# State file paths for all M5 Warp lanes + BTC M15
# ATR values from qwen-main's MT5 pull at 06:55 UTC
# Average Range from qwen-main's 1000-bar MT5 analysis at 07:25 UTC
STATES = {
    "BTC M5 $100": {
        "state": REPO / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_live_btcusd_m5_warp_exec_events.jsonl",
        "symbol": "BTCUSD",
        "atr_m5": 64.50,
        "atr_m15": 123.86,
        "avg_range": 101.09,
        "price": 74000,
        "timeframe": "M5",
    },
    "BTC M15 $75": {
        "state": REPO / "reports" / "penetration_lattice_live_btcusd_m15_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_live_btcusd_m15_warp_exec_events.jsonl",
        "symbol": "BTCUSD",
        "atr_m5": 64.50,
        "atr_m15": 123.86,
        "avg_range": 221.87,
        "price": 74000,
        "timeframe": "M15",
    },
    "ETH M5 $3": {
        "state": REPO / "reports" / "penetration_lattice_shadow_ethusd_m5_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_shadow_ethusd_m5_warp_events.jsonl",
        "symbol": "ETHUSD",
        "atr_m5": 3.24,
        "atr_m15": 5.55,
        "avg_range": 4.58,
        "price": 2244,
        "timeframe": "M5",
    },
    "ETH M5 $5 (A/B)": {
        "state": REPO / "reports" / "penetration_lattice_shadow_ethusd_m5_warp_5_state.json",
        "events": REPO / "reports" / "penetration_lattice_shadow_ethusd_m5_warp_5_events.jsonl",
        "symbol": "ETHUSD",
        "atr_m5": 3.24,
        "atr_m15": 5.55,
        "avg_range": 4.58,
        "price": 2244,
        "timeframe": "M5",
    },
    "SOL M5 $0.12": {
        "state": REPO / "reports" / "penetration_lattice_shadow_solusd_m5_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_shadow_solusd_m5_warp_events.jsonl",
        "symbol": "SOLUSD",
        "atr_m5": 0.124,
        "atr_m15": None,
        "avg_range": 0.16,
        "price": 86,
        "timeframe": "M5",
    },
    "XRP M5 $0.0016": {
        "state": REPO / "reports" / "penetration_lattice_shadow_xrpusd_m5_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_shadow_xrpusd_m5_warp_events.jsonl",
        "symbol": "XRPUSD",
        "atr_m5": 0.0016,
        "atr_m15": None,
        "avg_range": None,  # Not provided by qwen-main
        "price": 1.37,
        "timeframe": "M5",
    },
    "LTC M5 $0.10": {
        "state": REPO / "reports" / "penetration_lattice_shadow_ltcusd_m5_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_shadow_ltcusd_m5_warp_events.jsonl",
        "symbol": "LTCUSD",
        "atr_m5": 0.059,
        "atr_m15": None,
        "avg_range": None,
        "price": 55,
        "timeframe": "M5",
    },
    "ADA M5 $0.0008": {
        "state": REPO / "reports" / "penetration_lattice_shadow_adausd_m5_warp_state.json",
        "events": REPO / "reports" / "penetration_lattice_shadow_adausd_m5_warp_events.jsonl",
        "symbol": "ADAUSD",
        "atr_m5": 0.00035,
        "atr_m15": None,
        "avg_range": None,
        "price": 0.25,
        "timeframe": "M5",
    },
    # FX M15 bar lanes — testing if Range formula works on FX!
    "GBPUSD M15 bar": {
        "state": REPO / "reports" / "shadow_fx_m15_micro_gbpusd_bar_state.json",
        "events": None,
        "symbol": "GBPUSD",
        "atr_m5": None,
        "atr_m15": None,
        "avg_range": 0.0012,
        "price": 1.09,
        "timeframe": "M15",
    },
    "EURUSD M15 bar": {
        "state": REPO / "reports" / "shadow_fx_m15_micro_eurusd_bar_state.json",
        "events": None,
        "symbol": "EURUSD",
        "atr_m5": None,
        "atr_m15": None,
        "avg_range": 0.0010,
        "price": 1.03,
        "timeframe": "M15",
    },
    "NZDUSD M15 bar": {
        "state": REPO / "reports" / "shadow_fx_m15_micro_nzdusd_bar_state.json",
        "events": None,
        "symbol": "NZDUSD",
        "atr_m5": None,
        "atr_m15": None,
        "avg_range": 0.0009,
        "price": 0.55,
        "timeframe": "M15",
    },
}

HISTORY_PATH = REPO / "reports" / "m5_warp_cross_symbol_history.jsonl"
OUTPUT_MD = REPO / "reports" / "m5_warp_cross_symbol_tracker.md"


def load_state(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def count_events(path, since=None):
    """Count close events from the events log."""
    if not path.exists():
        return 0
    count = 0
    try:
        for line in path.read_text().strip().split('\n'):
            if not line.strip():
                continue
            evt = json.loads(line)
            if evt.get('event_type') == 'close':
                if since is None or evt.get('timestamp', 0) >= since:
                    count += 1
    except Exception:
        pass
    return count


def extract_lane(label, config):
    """Extract metrics from a single M5 Warp lane."""
    state = load_state(config["state"])
    if state is None:
        return {
            "label": label,
            "status": "MISSING",
            "closes": 0, "realized_usd": 0, "opens": 0, "anchor": 0, "step": 0,
            "resets": 0, "start_time": 0, "uptime_hours": 0,
            "dollar_per_close": 0, "dollar_per_hour": 0, "closes_per_hour": 0,
            "atr_m5": 0, "atr_multiple": 0, "step_pct": 0, "atr_pct": 0, "timeframe": config.get("timeframe", "M5"),
            "avg_range": 0, "range_coeff": 0, "optimal_step": 0,
        }

    # Check if flat structure (FX M15 bar lanes) or nested structure (crypto lanes)
    if "symbols" not in state and "realized_closes" in state:
        # Flat structure — FX M15 bar lane
        closes = state.get("realized_closes", 0)
        realized = state.get("realized_net_usd", 0)
        opens = len(state.get("open_tickets", []))
        anchor = state.get("anchor", 0)
        step = state.get("step", config.get("step", 0))
        resets = state.get("reset_count", 0)
    elif "symbols" in state:
        # Nested structure — crypto lanes
        symbols = state.get("symbols", {})
        sym = None
        for key in symbols:
            sym = symbols[key]
            break  # First symbol

        if sym is None:
            return {
                "label": label,
                "status": "EMPTY",
                "closes": 0, "realized_usd": 0, "opens": 0, "anchor": 0, "step": 0,
                "resets": 0, "start_time": 0, "uptime_hours": 0,
                "dollar_per_close": 0, "dollar_per_hour": 0, "closes_per_hour": 0,
                "atr_m5": 0, "atr_multiple": 0, "step_pct": 0, "atr_pct": 0, "timeframe": config.get("timeframe", "M5"),
                "avg_range": 0, "range_coeff": 0, "optimal_step": 0,
            }
        closes = sym.get("realized_closes", 0)
        realized = sym.get("realized_net_usd", 0)
        opens = len(sym.get("open_tickets", []))
        anchor = sym.get("anchor", 0)
        # Step may be in symbol or in metadata
        step = sym.get("step", state.get("metadata", {}).get("step", 0))
        resets = sym.get("reset_count", 0)
    else:
        return {
            "label": label,
            "status": "EMPTY",
            "closes": 0, "realized_usd": 0, "opens": 0, "anchor": 0, "step": 0,
            "resets": 0, "start_time": 0, "uptime_hours": 0,
            "dollar_per_close": 0, "dollar_per_hour": 0, "closes_per_hour": 0,
            "atr_m5": 0, "atr_multiple": 0, "step_pct": 0, "atr_pct": 0, "timeframe": config.get("timeframe", "M5"),
            "avg_range": 0, "range_coeff": 0, "optimal_step": 0,
        }

    # Use state file mtime as lane start (state files get overwritten, so mtime ≈ last write,
    # but for rate calculation we need first-event time. Use earliest of: lattice_started_time,
    # state file ctime, or events log first entry).
    start_time = sym.get("lattice_started_time", sym.get("start_time", state.get("start_time", 0)))
    if isinstance(start_time, str):
        try:
            from datetime import datetime
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00")).timestamp()
        except Exception:
            start_time = 0

    # Fallback: use file creation time if state has no start time
    if not start_time or start_time == 0:
        try:
            start_time = config["state"].stat().st_ctime
        except Exception:
            start_time = 0

    # Override: if closes > 0, estimate start_time from realized/close rate
    # A safe lower bound: lane has been running at least closes * 3 minutes
    if closes > 0 and start_time:
        now = time.time()
        min_runtime = closes * 180  # minimum 3 min per close
        max_start = now - min_runtime
        if start_time > max_start:
            start_time = max_start  # push start earlier if needed

    now = time.time()
    uptime_hours = (now - start_time) / 3600 if start_time > 0 else 0

    dollar_per_close = realized / closes if closes > 0 else 0
    dollar_per_hour = realized / uptime_hours if uptime_hours > 0 else 0
    closes_per_hour = closes / uptime_hours if uptime_hours > 0 else 0

    # ATR analysis — use the ATR for the lane's timeframe
    timeframe = config.get("timeframe", "M5")
    atr_m5 = config.get("atr_m5")
    atr_m15 = config.get("atr_m15")
    atr = atr_m15 if timeframe == "M15" and atr_m15 else atr_m5
    atr_multiple = step / atr if atr and atr > 0 else 0
    # Also compute cross-timeframe ATR for comparison
    atr_m5_mult = step / atr_m5 if atr_m5 and atr_m5 > 0 else 0
    atr_m15_mult = step / atr_m15 if atr_m15 and atr_m15 > 0 else 0
    price = config.get("price", 0)
    step_pct = (step / price * 100) if price > 0 else 0
    atr_pct = (atr / price * 100) if atr and price > 0 else 0

    # Range analysis — from qwen-main's 1000-bar MT5 data
    avg_range = config.get("avg_range")
    range_coeff = step / avg_range if avg_range and avg_range > 0 else 0
    # Optimal step from team's best guess: 0.80x Range for M5, 0.61x for M15
    if timeframe == "M15":
        optimal_step = avg_range * 0.61 if avg_range else 0
    else:
        optimal_step = avg_range * 0.80 if avg_range else 0

    return {
        "label": label,
        "status": "OK",
        "closes": closes,
        "realized_usd": realized,
        "opens": opens,
        "anchor": anchor,
        "step": step,
        "resets": resets,
        "start_time": start_time,
        "uptime_hours": uptime_hours,
        "dollar_per_close": dollar_per_close,
        "dollar_per_hour": dollar_per_hour,
        "closes_per_hour": closes_per_hour,
        "atr_m5": atr,
        "atr_multiple": atr_multiple,
        "step_pct": step_pct,
        "atr_pct": atr_pct,
        "timeframe": timeframe,
        "avg_range": avg_range or 0,
        "range_coeff": range_coeff,
        "optimal_step": optimal_step,
    }


def snapshot():
    """Take a snapshot of all M5 Warp lanes."""
    lanes = {}
    for label, config in STATES.items():
        lanes[label] = extract_lane(label, config)
    return lanes


def format_table(lanes):
    """Format lanes as a markdown table."""
    lines = [
        "## M5/M15 Warp Cross-Symbol Tracker",
        f"*Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*",
        "",
        "| Lane | TF | Closes | Net $ | $/close | Open | ATRx | Range-x | Optimal | Diff |",
        "|------|----|--------|-------|---------|------|------|---------|---------|------|",
    ]

    for label in lanes:
        l = lanes[label]
        tf = l.get("timeframe", "M5")
        if l["status"] == "MISSING":
            lines.append(f"| {label} | {tf} | - | - | - | - | - | - | - | - |")
        elif l["status"] == "EMPTY":
            lines.append(f"| {label} | {tf} | - | - | - | - | - | - | - | - |")
        else:
            optimal = l.get("optimal_step", 0)
            actual = l["step"]
            if optimal > 0:
                diff_pct = (actual - optimal) / optimal * 100
                diff_str = f"{diff_pct:+.0f}%"
                optimal_str = f"${optimal:.4g}"
            else:
                diff_str = "N/A"
                optimal_str = "N/A"
            lines.append(
                f"| {label} "
                f"| {tf} "
                f"| {l['closes']} "
                f"| ${l['realized_usd']:.2f} "
                f"| ${l['dollar_per_close']:.2f} "
                f"| {l['opens']} "
                f"| {l['atr_multiple']:.2f}x "
                f"| {l['range_coeff']:.2f}x "
                f"| {optimal_str} "
                f"| {diff_str} |"
            )

    return "\n".join(lines)


def save_history(lanes):
    """Append snapshot to history file."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lanes": {
            label: {
                "closes": l["closes"],
                "realized_usd": l["realized_usd"],
                "opens": l["opens"],
                "resets": l["resets"],
                "dollar_per_close": l["dollar_per_close"],
                "dollar_per_hour": l["dollar_per_hour"],
                "closes_per_hour": l["closes_per_hour"],
            }
            for label, l in lanes.items()
        }
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def main():
    parser = argparse.ArgumentParser(description="M5 Warp Cross-Symbol Tracker")
    parser.add_argument("--loop", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=int, default=300, help="Poll interval in seconds (default: 300)")
    args = parser.parse_args()

    print(f"M5 Warp Cross-Symbol Tracker — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Polling: {', '.join(STATES.keys())}")
    print(f"History: {HISTORY_PATH}")
    print(f"Output: {OUTPUT_MD}")
    print()

    if args.loop:
        print(f"Polling every {args.interval}s. Press Ctrl+C to stop.")
        print()

    try:
        while True:
            lanes = snapshot()
            table = format_table(lanes)
            save_history(lanes)

            # Write markdown
            OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_MD.write_text(table + "\n")

            # Print to console
            print(table)
            print()

            # Print key rates
            for label, l in lanes.items():
                if l["status"] == "OK":
                    print(f"  {label}: {l['closes']} closes, ${l['realized_usd']:.2f} net, ${l['dollar_per_close']:.2f}/close, ${l['dollar_per_hour']:.2f}/hr")

            print()
            print(f"Snapshot saved to {OUTPUT_MD}")
            print()

            if not args.loop:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nTracker stopped.")


if __name__ == "__main__":
    main()
