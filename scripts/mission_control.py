#!/usr/bin/env python3
"""Mission Control Dashboard — unified view of all live trading lanes.

Aggregates data from:
1. Lane scoreboard (FX rearm, FX momentum, BTC H1, BTC M5)
2. Kelly shadow runner state
3. Runner watchdog status
4. Experiment promotion readiness

Usage:
    python scripts/mission_control.py
    python scripts/mission_control.py --json  # machine-readable output
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOREBOARD_MD = ROOT / "reports" / "penetration_lattice_lane_scoreboard.md"
KELLY_STATE = ROOT / "reports" / "kelly_shadow_state.json"
KELLY_EVENTS = ROOT / "reports" / "kelly_shadow_events.jsonl"

STALE_THRESHOLD_SECONDS = 300  # 5 minutes


def parse_scoreboard():
    """Parse the lane scoreboard markdown into structured data."""
    lanes = {}
    if not SCOREBOARD_MD.exists():
        return lanes

    with open(SCOREBOARD_MD) as f:
        lines = f.readlines()

    for line in lines[3:]:  # Skip header rows
        line = line.strip()
        if not line or line.startswith("| ---"):
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 13:
            continue

        lane_id = parts[0].strip("`")
        lane_type = parts[1].strip("`")
        symbol = parts[2].strip("`")
        realized_usd = float(parts[5]) if parts[5] != "--" else 0.0
        floating_usd = float(parts[7]) if parts[7] != "--" else 0.0
        net_usd = float(parts[8]) if parts[8] != "--" else 0.0
        closes = int(parts[9]) if parts[9] != "--" else 0
        open_count = int(parts[10]) if parts[10] != "--" else 0
        avg_close = float(parts[11]) if parts[11] != "--" else 0.0
        updated_str = parts[12].strip("`") if len(parts) > 12 else ""

        if lane_id not in lanes:
            lanes[lane_id] = {
                "lane_id": lane_id,
                "lane_type": lane_type,
                "symbols": {},
                "total_realized": 0.0,
                "total_floating": 0.0,
                "total_net": 0.0,
                "total_closes": 0,
                "total_open": 0,
                "last_updated": "",
            }

        if symbol == "TOTAL":
            lanes[lane_id]["total_realized"] = realized_usd
            lanes[lane_id]["total_floating"] = floating_usd
            lanes[lane_id]["total_net"] = net_usd
            lanes[lane_id]["total_closes"] = closes
            lanes[lane_id]["total_open"] = open_count
            lanes[lane_id]["last_updated"] = updated_str
        else:
            lanes[lane_id]["symbols"][symbol] = {
                "realized": realized_usd,
                "floating": floating_usd,
                "net": net_usd,
                "closes": closes,
                "open": open_count,
                "avg_close": avg_close,
            }

    return lanes


def parse_kelly_state():
    """Parse Kelly shadow runner state."""
    if not KELLY_STATE.exists():
        return None

    with open(KELLY_STATE) as f:
        return json.load(f)


def check_lane_health(lanes, kelly_state):
    """Generate health flags for all lanes."""
    flags = []
    now = time.time()

    for lane_id, lane in lanes.items():
        updated_str = lane.get("last_updated", "")
        if updated_str:
            try:
                updated_dt = datetime.fromisoformat(updated_str)
                age = now - updated_dt.timestamp()
                if age > STALE_THRESHOLD_SECONDS:
                    flags.append({
                        "lane": lane_id,
                        "severity": "warning",
                        "message": f"State is {age:.0f}s old (threshold: {STALE_THRESHOLD_SECONDS}s)",
                    })
            except Exception:
                pass

        # Excessive open positions
        if lane["total_open"] > 100:
            flags.append({
                "lane": lane_id,
                "severity": "warning",
                "message": f"{lane['total_open']} open positions (excessive)",
            })

        # Negative net with high floating
        if lane["total_net"] < -500:
            flags.append({
                "lane": lane_id,
                "severity": "critical",
                "message": f"Net loss ${lane['total_net']:.0f} with ${lane['total_floating']:.0f} floating",
            })

        # Per-symbol negative performance
        for symbol, data in lane["symbols"].items():
            if data["net"] < -100 and data["closes"] > 500:
                flags.append({
                    "lane": lane_id,
                    "symbol": symbol,
                    "severity": "warning",
                    "message": f"{symbol}: ${data['net']:.0f} net across {data['closes']} closes (${data['avg_close']:.3f}/close)",
                })

    # Kelly health
    if kelly_state:
        updated_str = kelly_state.get("updated_at", "")
        if updated_str:
            try:
                updated_dt = datetime.fromisoformat(updated_str)
                age = now - updated_dt.timestamp()
                if age > STALE_THRESHOLD_SECONDS:
                    flags.append({
                        "lane": "kelly_shadow",
                        "severity": "warning",
                        "message": f"Kelly state is {age:.0f}s old",
                    })
            except Exception:
                pass

        active = [k for k, v in kelly_state.get("ledgers", {}).items() if v.get("position") == "active"]
        if not active:
            flags.append({
                "lane": "kelly_shadow",
                "severity": "info",
                "message": f"No active positions (cycle {kelly_state.get('cycle', '?')})",
            })

    return flags


def print_dashboard(lanes, kelly_state, flags):
    """Print the unified mission control dashboard."""
    print("=" * 80)
    print("  MISSION CONTROL — ALL LIVE LANES")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    # Lane summary
    print(f"\n{'Lane':<35} {'Realized':>10} {'Floating':>10} {'Net':>10} {'Closes':>7} {'Open':>5}")
    print("-" * 80)

    grand_realized = 0.0
    grand_floating = 0.0
    grand_net = 0.0
    grand_closes = 0
    grand_open = 0

    for lane_id, lane in lanes.items():
        short_id = lane_id[:35]
        print(f"{short_id:<35} ${lane['total_realized']:>9.2f} ${lane['total_floating']:>9.2f} ${lane['total_net']:>9.2f} {lane['total_closes']:>7d} {lane['total_open']:>5d}")
        grand_realized += lane["total_realized"]
        grand_floating += lane["total_floating"]
        grand_net += lane["total_net"]
        grand_closes += lane["total_closes"]
        grand_open += lane["total_open"]

    # Kelly shadow
    if kelly_state:
        kelly_active = [k for k, v in kelly_state.get("ledgers", {}).items() if v.get("position") == "active"]
        kelly_equity = kelly_state.get("total_equity", 0)
        kelly_pnl = kelly_state.get("total_pnl", 0)
        print(f"{'kelly_shadow (NOM/GHST/SUP/A8/CFG)':<35} ${kelly_pnl:>9.2f} ${0:>9.2f} ${kelly_pnl:>9.2f} {'0':>7} {len(kelly_active):>5d}")
        grand_net += kelly_pnl

    print("-" * 80)
    print(f"{'TOTAL':<35} ${grand_realized:>9.2f} ${grand_floating:>9.2f} ${grand_net:>9.2f} {grand_closes:>7d} {grand_open:>5d}")

    # Health flags
    if flags:
        print(f"\n{'─' * 80}")
        print(f"  HEALTH FLAGS ({len(flags)}):")
        for flag in flags:
            severity = flag["severity"].upper()
            lane = flag.get("symbol", flag.get("lane", "?"))
            msg = flag["message"]
            icon = "🚨" if severity == "CRITICAL" else ("⚠️" if severity == "WARNING" else "ℹ️")
            print(f"    {icon} [{severity}] {lane}: {msg}")

    # Kelly detail
    if kelly_state:
        print(f"\n{'─' * 80}")
        print(f"  KELLY SHADOW DETAIL (cycle {kelly_state.get('cycle', '?')}):")
        ledgers = kelly_state.get("ledgers", {})
        for coin, ledger in ledgers.items():
            pos = ledger.get("position", "flat")
            signals = ledger.get("signals", 0)
            closes = ledger.get("closes", 0)
            hist = ledger.get("history_len", 0)
            cash = ledger.get("cash", 0)
            icon = "🔴" if pos == "active" else ("🔵" if signals > 0 else "⚪")
            print(f"    {icon} {coin:<12s} {pos:<8s} signals={signals} closes={closes} hist={hist} cash=${cash:.2f}")

            if pos == "active":
                entry = ledger.get("position_entry", 0)
                tp = ledger.get("position_tp", 0)
                sl = ledger.get("position_sl", 0)
                hold = ledger.get("position_hold", 0)
                max_hold = ledger.get("position_max_hold", 0)
                print(f"         entry=${entry:.4f} tp=${tp:.4f} sl=${sl:.4f} hold={hold}/{max_hold}")

    print(f"\n{'=' * 80}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mission Control Dashboard")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of formatted text")
    args = parser.parse_args()

    lanes = parse_scoreboard()
    kelly_state = parse_kelly_state()
    flags = check_lane_health(lanes, kelly_state)

    if args.json:
        output = {
            "lanes": lanes,
            "kelly": kelly_state,
            "flags": flags,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(output, indent=2))
    else:
        print_dashboard(lanes, kelly_state, flags)


if __name__ == "__main__":
    main()
