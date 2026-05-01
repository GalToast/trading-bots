#!/usr/bin/env python3
"""Live Risk Dashboard — broker-authoritative view of all lanes.

Reads the lane scoreboard CSV (the single source of truth) and produces:
- Per-lane health status (realized, floating, net, open count)
- Risk flags: negative net with deep floating, excessive open positions
- Net exposure per symbol across all lanes
- Stale lane detection (last update > threshold)

Usage:
    python scripts/live_risk_dashboard.py
    python scripts/live_risk_dashboard.py --stale-minutes 5
    python scripts/live_risk_dashboard.py --json
"""
import csv
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOREBOARD_PATH = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"

# Risk thresholds
FLOATING_DANGER_THRESHOLD = -500  # Flag if floating < this
OPEN_COUNT_DANGER = 20  # Flag if open positions > this
STALE_MINUTES_DEFAULT = 10


def load_scoreboard():
    if not SCOREBOARD_PATH.exists():
        print(f"ERROR: Scoreboard not found at {SCOREBOARD_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(SCOREBOARD_PATH) as f:
        return list(csv.DictReader(f))


def parse_updated_at(ts_str):
    """Parse ISO timestamp to datetime."""
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def check_stale(updated_at_str, stale_minutes):
    """Check if a lane is stale (last update > threshold)."""
    ts = parse_updated_at(updated_at_str)
    if ts is None:
        return True, "unknown timestamp"
    age = datetime.now(timezone.utc) - ts
    minutes = age.total_seconds() / 60
    if minutes > stale_minutes:
        return True, f"{minutes:.0f}min old"
    return False, f"{minutes:.0f}min"


def analyze_lanes(rows, stale_minutes):
    """Analyze all lanes and produce risk report."""
    now = datetime.now(timezone.utc)

    live_lanes = {}  # lane_id -> {symbol -> row}
    shadow_lanes = {}
    symbol_exposure = {}  # symbol -> {realized, floating, net, open_count}
    flags = []

    for row in rows:
        lane_id = row["lane_id"]
        lane_type = row["lane_type"]
        symbol = row["symbol"]
        is_total = symbol == "TOTAL"

        realized = float(row["realized_usd"])
        floating = float(row["floating_usd"])
        net = float(row["net_usd"])
        closes = int(row["closes"])
        open_count = int(row["open_count"])
        avg_per_close = float(row["avg_usd_per_close"])

        # Track per-symbol exposure (only non-TOTAL rows)
        if not is_total:
            if symbol not in symbol_exposure:
                symbol_exposure[symbol] = {"realized": 0, "floating": 0, "net": 0, "open_count": 0}
            symbol_exposure[symbol]["realized"] += realized
            symbol_exposure[symbol]["floating"] += floating
            symbol_exposure[symbol]["net"] += net
            symbol_exposure[symbol]["open_count"] += open_count

        # Group by lane
        if lane_type == "live":
            if lane_id not in live_lanes:
                live_lanes[lane_id] = {"symbols": {}, "total": None}
            if is_total:
                live_lanes[lane_id]["total"] = row
            else:
                live_lanes[lane_id]["symbols"][symbol] = row
        else:
            if lane_id not in shadow_lanes:
                shadow_lanes[lane_id] = {"symbols": {}, "total": None}
            if is_total:
                shadow_lanes[lane_id]["total"] = row
            else:
                shadow_lanes[lane_id]["symbols"][symbol] = row

        # Risk flags (only for non-TOTAL rows)
        if not is_total:
            stale, stale_info = check_stale(row["updated_at"], stale_minutes)
            if stale and lane_type == "live":
                flags.append({
                    "lane": lane_id,
                    "symbol": symbol,
                    "severity": "WARNING",
                    "message": f"Stale data ({stale_info})"
                })

    # Lane-level flags
    for lane_id, data in live_lanes.items():
        total = data.get("total")
        if total is None:
            continue
        floating = float(total["floating_usd"])
        net = float(total["net_usd"])
        open_count = int(total["open_count"])

        if floating < FLOATING_DANGER_THRESHOLD:
            flags.append({
                "lane": lane_id,
                "symbol": "TOTAL",
                "severity": "CRITICAL",
                "message": f"Deep floating loss: ${floating:,.2f}"
            })
        if open_count > OPEN_COUNT_DANGER:
            flags.append({
                "lane": lane_id,
                "symbol": "TOTAL",
                "severity": "WARNING",
                "message": f"Excessive open positions: {open_count}"
            })
        if net < 0 and float(total["realized_usd"]) > 0:
            flags.append({
                "lane": lane_id,
                "symbol": "TOTAL",
                "severity": "WARNING",
                "message": f"Realized +${float(total['realized_usd']):,.2f} but floating wipes it out → net ${net:,.2f}"
            })

    return live_lanes, shadow_lanes, symbol_exposure, flags


def print_dashboard(live_lanes, shadow_lanes, symbol_exposure, flags, stale_minutes):
    """Print the risk dashboard to stdout."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("=" * 80)
    print(f"  LIVE RISK DASHBOARD — {now}")
    print(f"  Source: {SCOREBOARD_PATH.name} (broker-authentic)")
    print(f"  Stale threshold: {stale_minutes} minutes")
    print("=" * 80)

    # Risk Flags
    print(f"\n{'='*80}")
    print(f"  RISK FLAGS ({len(flags)} total)")
    print(f"{'='*80}")
    if flags:
        for f in flags:
            icon = "🚨" if f["severity"] == "CRITICAL" else "⚠️"
            print(f"  {icon} [{f['severity']}] {f['lane']} / {f['symbol']}: {f['message']}")
    else:
        print("  ✅ No risk flags triggered")

    # Live Lanes
    print(f"\n{'='*80}")
    print(f"  LIVE LANES")
    print(f"{'='*80}")
    print(f"\n{'Lane':<40} {'Symbol':<8} {'Realized':>10} {'Floating':>10} {'Net':>10} {'Open':>5} {'Closes':>7}")
    print("-" * 92)

    for lane_id, data in sorted(live_lanes.items()):
        total = data.get("total")
        if total is None:
            continue

        # Lane header
        lane_name = lane_id.replace("live_", "")[:38]
        t_realized = float(total["realized_usd"])
        t_floating = float(total["floating_usd"])
        t_net = float(total["net_usd"])
        t_opens = int(total["open_count"])
        t_closes = int(total["closes"])

        # Color the net with sign
        net_str = f"${t_net:+,.2f}"
        icon = "✅" if t_net > 0 else "🔴" if t_net < -500 else "⚠️"
        print(f"  {lane_name:<40} {'TOTAL':<8} ${t_realized:>8,.2f} ${t_floating:>8,.2f} {net_str:>10} {t_opens:>5} {t_closes:>7} {icon}")

        # Per-symbol breakdown
        for symbol, row in sorted(data["symbols"].items()):
            s_realized = float(row["realized_usd"])
            s_floating = float(row["floating_usd"])
            s_net = float(row["net_usd"])
            s_opens = int(row["open_count"])
            s_closes = int(row["closes"])
            print(f"    {'':38} {symbol:<8} ${s_realized:>8,.2f} ${s_floating:>8,.2f} ${s_net:>+8,.2f} {s_opens:>5} {s_closes:>7}")

    # Shadow Lanes (summary only)
    print(f"\n{'='*80}")
    print(f"  SHADOW LANES (modeled, not broker-authentic)")
    print(f"{'='*80}")
    print(f"\n{'Lane':<40} {'Total Net':>10} {'Floating':>10} {'Open':>5} {'Closes':>7}")
    print("-" * 74)

    for lane_id, data in sorted(shadow_lanes.items()):
        total = data.get("total")
        if total is None:
            continue
        lane_name = lane_id.replace("shadow_", "")[:38]
        t_net = float(total["net_usd"])
        t_floating = float(total["floating_usd"])
        t_opens = int(total["open_count"])
        t_closes = int(total["closes"])
        icon = "✅" if t_net > 0 else "🔴" if t_net < -100 else "⚠️"
        print(f"  {lane_name:<40} ${t_net:>+8,.2f} ${t_floating:>8,.2f} {t_opens:>5} {t_closes:>7} {icon}")

    # Symbol Exposure
    print(f"\n{'='*80}")
    print(f"  NET EXPOSURE BY SYMBOL (all lanes combined)")
    print(f"{'='*80}")
    print(f"\n{'Symbol':<10} {'Realized':>10} {'Floating':>10} {'Net':>10} {'Total Open':>11}")
    print("-" * 53)

    for symbol, exp in sorted(symbol_exposure.items(), key=lambda x: x[1]["net"], reverse=True):
        icon = "✅" if exp["net"] > 0 else "🔴"
        print(f"  {symbol:<10} ${exp['realized']:>8,.2f} ${exp['floating']:>8,.2f} ${exp['net']:>+8,.2f} {exp['open_count']:>11} {icon}")

    # Summary
    total_live_net = sum(
        float(data["total"]["net_usd"])
        for data in live_lanes.values()
        if data.get("total")
    )
    total_live_realized = sum(
        float(data["total"]["realized_usd"])
        for data in live_lanes.values()
        if data.get("total")
    )
    total_live_floating = sum(
        float(data["total"]["floating_usd"])
        for data in live_lanes.values()
        if data.get("total")
    )
    total_live_opens = sum(
        int(data["total"]["open_count"])
        for data in live_lanes.values()
        if data.get("total")
    )

    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"  Total live realized:   ${total_live_realized:>10,.2f}")
    print(f"  Total live floating:   ${total_live_floating:>10,.2f}")
    print(f"  Total live net:        ${total_live_net:>+10,.2f}")
    print(f"  Total open positions:  {total_live_opens:>10}")
    print(f"  Active live lanes:     {len(live_lanes):>10}")
    print(f"  Risk flags:            {len(flags):>10}")
    print(f"{'='*80}")


def to_json(live_lanes, shadow_lanes, symbol_exposure, flags):
    """Export as JSON."""
    result = {
        "live_lanes": {},
        "shadow_lanes": {},
        "symbol_exposure": symbol_exposure,
        "flags": flags,
    }
    for lane_id, data in live_lanes.items():
        total = data.get("total")
        if total:
            result["live_lanes"][lane_id] = {
                "realized": float(total["realized_usd"]),
                "floating": float(total["floating_usd"]),
                "net": float(total["net_usd"]),
                "open_count": int(total["open_count"]),
                "closes": int(total["closes"]),
            }
    for lane_id, data in shadow_lanes.items():
        total = data.get("total")
        if total:
            result["shadow_lanes"][lane_id] = {
                "realized": float(total["realized_usd"]),
                "floating": float(total["floating_usd"]),
                "net": float(total["net_usd"]),
                "open_count": int(total["open_count"]),
                "closes": int(total["closes"]),
            }
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live Risk Dashboard")
    parser.add_argument("--stale-minutes", type=int, default=STALE_MINUTES_DEFAULT)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    rows = load_scoreboard()
    live_lanes, shadow_lanes, symbol_exposure, flags = analyze_lanes(rows, args.stale_minutes)

    if args.json:
        print(json.dumps(to_json(live_lanes, shadow_lanes, symbol_exposure, flags), indent=2))
    else:
        print_dashboard(live_lanes, shadow_lanes, symbol_exposure, flags, args.stale_minutes)


if __name__ == "__main__":
    main()
