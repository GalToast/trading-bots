#!/usr/bin/env python3
"""Composite earning-rate board for all live lanes.

Aggregates real-time $/hr from watchdog state + execution monitors.
Shows which lanes are actually earning vs burning capital.

Usage: python scripts/build_composite_earning_rate_board.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR = Path("reports")
STATE_FILES = {
    "live_btcusd_m15_warp_941781": "reports/penetration_lattice_live_btcusd_m15_warp_state.json",
    "live_gbpusd_adaptive_harness_941777": "reports/penetration_lattice_live_gbpusd_adaptive_harness_state.json",
    "live_eurusd_adaptive_harness_941885": "reports/penetration_lattice_live_eurusd_adaptive_harness_state.json",
    "live_nzdusd_adaptive_harness_941778": "reports/penetration_lattice_live_nzdusd_adaptive_harness_state.json",
    "live_usdjpy_adaptive_harness_941888": "reports/penetration_lattice_live_usdjpy_adaptive_harness_state.json",
    "live_ethusd_m5_warp_5_941890": "reports/penetration_lattice_live_ethusd_m5_warp_5_state.json",
    "live_solusd_m15_warp_v2_941891": "reports/penetration_lattice_live_solusd_m15_warp_v2_state.json",
    "live_xrpusd_m15_hh_breakout_941892": "reports/penetration_lattice_live_xrpusd_m15_hh_breakout_state.json",
    "live_adausd_m15_warp_941893": "reports/penetration_lattice_live_adausd_m15_warp_state.json",
    "live_ltcusd_m15_warp_941894": "reports/penetration_lattice_live_ltcusd_m15_warp_state.json",
}


def extract_lane_metrics(name: str, state_path: str) -> dict:
    """Extract earning metrics from a lane's state file."""
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        return {"name": name, "error": str(e)}

    runner = state.get("runner", {})
    symbols_data = state.get("symbols", {})
    first_sym = next(iter(symbols_data), None)
    sym = symbols_data.get(first_sym, {}) if first_sym else {}

    # Heartbeat age
    hb = runner.get("heartbeat_at", "")
    hb_age = "unknown"
    if hb:
        try:
            hb_dt = datetime.fromisoformat(hb.replace("+00:00", "+00:00"))
            now = datetime.now(timezone.utc)
            hb_age = max(0, (now - hb_dt).total_seconds())
        except Exception:
            pass

    # PnL
    realized = sym.get("realized_net_usd", 0.0)
    if isinstance(realized, (int, float)):
        realized = float(realized)

    # Open positions
    positions = sym.get("positions", {}) or {}
    if isinstance(positions, dict):
        open_count = len(positions)
    elif isinstance(positions, list):
        open_count = len(positions)
    else:
        open_count = 0

    # Close count
    close_count = sym.get("close_count", 0)

    # Session duration
    started = runner.get("started_at", "")
    session_hours = 0.0
    if started:
        try:
            start_dt = datetime.fromisoformat(started.replace("+00:00", "+00:00"))
            now = datetime.now(timezone.utc)
            session_hours = max(0.01, (now - start_dt).total_seconds() / 3600)
        except Exception:
            pass

    usd_per_hr = realized / session_hours if session_hours > 0 else 0.0

    return {
        "name": name,
        "realized_usd": realized,
        "open_count": open_count,
        "close_count": close_count,
        "session_hours": round(session_hours, 2),
        "usd_per_hour": round(usd_per_hr, 2),
        "heartbeat_age_s": round(hb_age, 1) if isinstance(hb_age, (int, float)) else hb_age,
        "error": None,
    }


def main():
    rows = []
    for name, path in STATE_FILES.items():
        metrics = extract_lane_metrics(name, path)
        rows.append(metrics)

    # Sort by $/hr descending
    rows.sort(key=lambda r: r.get("usd_per_hour", 0), reverse=True)

    # Markdown
    lines = [
        "# Composite Earning Rate Board",
        "",
        f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "| Lane | Realized USD | Opens | Closes | Session Hrs | $/hr | HB Age |",
        "|------|-------------|-------|--------|-------------|------|--------|",
    ]

    for r in rows:
        if r.get("error"):
            lines.append(f"| `{r['name']}` | ERROR: {r['error']} |")
        else:
            icon = "✅" if r["usd_per_hour"] > 0 else "⏸️" if r["open_count"] > 0 else "🔍"
            lines.append(
                f"| {icon} `{r['name']}` | "
                f"+{r['realized_usd']:.2f} | "
                f"{r['open_count']} | "
                f"{r['close_count']} | "
                f"{r['session_hours']:.1f} | "
                f"+{r['usd_per_hour']:.2f} | "
                f"{r['heartbeat_age_s']}s |"
            )

    total_hr = sum(r.get("usd_per_hour", 0) for r in rows if not r.get("error"))
    lines.append("")
    lines.append(f"## Summary")
    lines.append(f"- **Total composite $/hr: +${total_hr:.2f}**")
    earning = [r for r in rows if r.get("usd_per_hour", 0) > 0]
    idle = [r for r in rows if not r.get("error") and r.get("usd_per_hour", 0) == 0 and r.get("open_count", 0) > 0]
    probes = [r for r in rows if not r.get("error") and r.get("open_count", 0) == 0]
    lines.append(f"- Earning lanes: {len(earning)}")
    lines.append(f"- Trapped capital lanes: {len(idle)}")
    lines.append(f"- Probe lanes (0 fills): {len(probes)}")
    lines.append("")

    report = "\n".join(lines)
    report_path = REPORT_DIR / "composite_earning_rate_board.md"
    report_path.write_text(report, encoding="utf-8")

    json_path = REPORT_DIR / "composite_earning_rate_board.json"
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print(report)
    print(f"\nWrote {report_path}")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
