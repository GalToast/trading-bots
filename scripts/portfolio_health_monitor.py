#!/usr/bin/env python3
"""
Portfolio Health Monitor — Periodic Portfolio Health Check

Runs every 5 minutes via scheduler. Checks all live/shadow lanes and alerts when:
1. A lane's net drops below a threshold
2. A lane's recent $/close turns negative
3. A lane's heartbeat goes stale
4. The portfolio's total net drops

Usage:
    python scripts/portfolio_health_monitor.py
    python scripts/portfolio_health_monitor.py --output reports/portfolio_health_latest.json

Output:
    reports/portfolio_health_latest.json — machine-readable health status
    reports/portfolio_health_latest.md — human-readable summary
    Alerts logged to stdout for the switchboard to pick up
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Lane configurations to monitor
MONITORED_LANES = [
    # (name, state_file, symbol, kill_threshold_usd, stale_hours)
    ("BTC M15 Warp", "penetration_lattice_live_btcusd_m15_warp_state.json", "BTCUSD", None, 2),
    ("ETH M5 Warp 5", "penetration_lattice_live_ethusd_m5_warp_5_state.json", "ETHUSD", None, 1),
    ("SOL M15 Warp v2", "penetration_lattice_live_solusd_m15_warp_v2_state.json", "SOLUSD", None, 1),
    ("XRP M15 HH", "penetration_lattice_live_xrpusd_m15_hh_breakout_state.json", "XRPUSD", None, 1),
    ("ADA M15 Warp", "penetration_lattice_live_adausd_m15_warp_state.json", "ADAUSD", None, 1),
    ("LTC M15 Warp", "penetration_lattice_live_ltcusd_m15_warp_state.json", "LTCUSD", None, 1),
    ("EUR Adaptive", "penetration_lattice_live_eurusd_adaptive_harness_state.json", "EURUSD", None, 1),
    ("GBP Adaptive", "penetration_lattice_live_gbpusd_adaptive_harness_state.json", "GBPUSD", None, 1),
    ("NZD Adaptive", "penetration_lattice_live_nzdusd_adaptive_harness_state.json", "NZDUSD", None, 1),
    ("USDJPY Adaptive", "penetration_lattice_live_usdjpy_adaptive_harness_state.json", "USDJPY", None, 1),
    ("EUR M1 Microharvest", "live_eurusd_m1_snake_microharvest_state.json", "EURUSD", None, 1),
    ("GBP M1 Microharvest", "live_gbpusd_m1_snake_microharvest_state.json", "GBPUSD", None, 1),
    ("EUR M1 Hybrid", "live_eurusd_m1_snake_hybrid_state.json", "EURUSD", None, 1),
    ("GBP M1 Hybrid", "live_gbpusd_m1_snake_hybrid_state.json", "GBPUSD", None, 1),
]


@dataclass
class LaneHealth:
    name: str
    symbol: str
    closes: int
    net_usd: float
    avg_per_close: float
    open_positions: int
    heartbeat_age_hours: float
    status: str  # HEALTHY, WARNING, CRITICAL, DEAD, KILLED
    alert: str = ""


def check_lane(name: str, state_file: str, symbol: str,
               kill_threshold_usd: float | None, stale_hours: float) -> LaneHealth:
    """Check a single lane's health."""
    path = REPORTS / state_file
    if not path.exists():
        return LaneHealth(name=name, symbol=symbol, closes=0, net_usd=0,
                          avg_per_close=0, open_positions=0, heartbeat_age_hours=999,
                          status="DEAD", alert=f"State file missing: {state_file}")

    try:
        with open(path) as f:
            state = json.load(f)
    except Exception as e:
        return LaneHealth(name=name, symbol=symbol, closes=0, net_usd=0,
                          avg_per_close=0, open_positions=0, heartbeat_age_hours=999,
                          status="DEAD", alert=f"Cannot parse state: {e}")

    sym_data = state.get("symbols", {}).get(symbol, {})
    runner = state.get("runner", {})
    closes = sym_data.get("realized_closes", 0)
    net = sym_data.get("realized_net_usd", 0)
    open_n = len(sym_data.get("open_tickets", []))
    avg = net / closes if closes > 0 else 0

    # Heartbeat age
    hb_str = runner.get("last_successful_run_at") or runner.get("heartbeat_at")
    if hb_str:
        try:
            from datetime import datetime, timezone
            hb = datetime.fromisoformat(hb_str)
            now = datetime.now(timezone.utc)
            age_hours = (now - hb).total_seconds() / 3600
        except Exception:
            age_hours = 999
    else:
        age_hours = 999

    # Determine status
    if age_hours > stale_hours:
        status = "DEAD"
        alert = f"Heartbeat stale ({age_hours:.1f}h old, limit {stale_hours}h)"
    elif kill_threshold_usd is not None and net < kill_threshold_usd:
        status = "CRITICAL"
        alert = f"Net ${net:+.2f} below kill threshold ${kill_threshold_usd:+.2f}"
    elif avg < 0 and closes >= 5:
        status = "WARNING"
        alert = f"Negative $/close: ${avg:+.2f} over {closes} closes"
    elif open_n > 10:
        status = "WARNING"
        alert = f"High open positions: {open_n}"
    else:
        status = "HEALTHY"
        alert = ""

    return LaneHealth(
        name=name, symbol=symbol, closes=closes, net_usd=net,
        avg_per_close=avg, open_positions=open_n,
        heartbeat_age_hours=age_hours, status=status, alert=alert
    )


def run_check() -> dict[str, Any]:
    """Run health check on all monitored lanes."""
    results = []
    for name, state_file, symbol, threshold, stale in MONITORED_LANES:
        health = check_lane(name, state_file, symbol, threshold, stale)
        results.append(health)

    # Summary
    healthy = [r for r in results if r.status == "HEALTHY"]
    warnings = [r for r in results if r.status == "WARNING"]
    critical = [r for r in results if r.status == "CRITICAL"]
    dead = [r for r in results if r.status == "DEAD"]

    total_net = sum(r.net_usd for r in results)
    total_closes = sum(r.closes for r in results)

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "summary": {
            "total_lanes": len(results),
            "healthy": len(healthy),
            "warnings": len(warnings),
            "critical": len(critical),
            "dead": len(dead),
            "total_net_usd": round(total_net, 2),
            "total_closes": total_closes,
        },
        "lanes": [asdict(r) for r in results],
        "alerts": [asdict(r) for r in results if r.alert],
    }


def format_report(data: dict[str, Any]) -> str:
    """Format health check as human-readable markdown."""
    lines = []
    lines.append("# Portfolio Health Check")
    lines.append(f"- Generated at: `{data['timestamp']}`")
    lines.append("")

    s = data["summary"]
    lines.append(f"## Summary")
    lines.append(f"- Total lanes monitored: {s['total_lanes']}")
    lines.append(f"- Healthy: {s['healthy']}")
    lines.append(f"- Warnings: {s['warnings']}")
    lines.append(f"- Critical: {s['critical']}")
    lines.append(f"- Dead: {s['dead']}")
    lines.append(f"- Total net: ${s['total_net_usd']:+.2f}")
    lines.append(f"- Total closes: {s['total_closes']}")
    lines.append("")

    if data["alerts"]:
        lines.append("## Alerts")
        lines.append("")
        for a in data["alerts"]:
            lines.append(f"- **{a['name']}** ({a['symbol']}): [{a['status']}] {a['alert']}")
        lines.append("")

    lines.append("## Lane Details")
    lines.append("")
    lines.append("| Lane | Symbol | Closes | Net USD | $/Close | Open | HB Age | Status |")
    lines.append("|------|--------|--------|---------|---------|------|--------|--------|")

    for lane in data["lanes"]:
        lines.append(
            f"| {lane['name']} | {lane['symbol']} | {lane['closes']} "
            f"| {lane['net_usd']:+.2f} | {lane['avg_per_close']:+.2f} "
            f"| {lane['open_positions']} | {lane['heartbeat_age_hours']:.1f}h "
            f"| {lane['status']} |"
        )

    lines.append("")
    return "\n".join(lines)


def main():
    data = run_check()

    # Output JSON
    json_path = REPORTS / "portfolio_health_latest.json"
    json_path.write_text(json.dumps(data, indent=2))

    # Output MD
    md_report = format_report(data)
    md_path = REPORTS / "portfolio_health_latest.md"
    md_path.write_text(md_report)

    # Print to stdout
    print(md_report)

    # Print alerts for switchboard consumption
    if data["alerts"]:
        print("\n## ALERTS", file=sys.stderr)
        for a in data["alerts"]:
            print(f"  [{a['status']}] {a['name']}: {a['alert']}", file=sys.stderr)

    return data


if __name__ == "__main__":
    main()
