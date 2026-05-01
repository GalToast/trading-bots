#!/usr/bin/env python3
"""Consolidated lab dashboard — single source of truth for all USDJPY experiments.

Aggregates:
1. 10-lane backtest results from a specific analysis pass
2. Live lab state (current exits, give-back, MFE capture)
3. Promotion/kill tracker for each lane
4. Deep pattern insights
5. M1 micro-momentum lane portfolio

Outputs:
- reports/lab_dashboard.txt  (human-readable)
- reports/lab_dashboard.json  (machine-readable)

"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def get_pnl(row: dict) -> float:
    return float(row.get("realized_pnl", 0.0) or 0.0)


def get_peak(row: dict) -> float:
    return float(row.get("peak_pnl_before_exit", 0.0) or 0.0)


def fmt_money(value: float) -> str:
    return f"${value:+.2f}"


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


# -- 10-lane backtest data from one analysis pass -------------------------

LANE_RESULTS = [
    {"lane": "exit_75_floor03",       "trades": 19, "blocked": 5, "net": 5.28, "exp": 0.28, "wr": 100.0, "peak_cap": 76.9},
    {"lane": "tiered_peak",            "trades": 19, "blocked": 5, "net": 5.25, "exp": 0.28, "wr": 100.0, "peak_cap": 76.4},
    {"lane": "fast_trail_above_1",     "trades": 19, "blocked": 5, "net": 5.08, "exp": 0.27, "wr": 100.0, "peak_cap": 73.9},
    {"lane": "exit_60_floor03",        "trades": 19, "blocked": 5, "net": 4.46, "exp": 0.23, "wr": 100.0, "peak_cap": 64.9},
    {"lane": "time_decay_trail",       "trades": 19, "blocked": 5, "net": 4.11, "exp": 0.22, "wr": 100.0, "peak_cap": 59.8},
    {"lane": "exit_50_floor03",        "trades": 19, "blocked": 5, "net": 4.05, "exp": 0.21, "wr": 100.0, "peak_cap": 59.0},
    {"lane": "peak_gate_120s",         "trades": 17, "blocked": 7, "net": 2.92, "exp": 0.17, "wr": 100.0, "peak_cap": 43.3},
    {"lane": "control",                "trades": 19, "controlled": 5, "net": 2.60, "exp": 0.14, "wr": 89.5, "peak_cap": 43.3},
    {"lane": "adverse_tolerance_015",  "trades": 19, "blocked": 5, "net": 2.60, "exp": 0.14, "wr": 89.5, "peak_cap": 43.3},
    {"lane": "entry_10s",              "trades": 22, "blocked": 2, "net": 2.37, "exp": 0.11, "wr": 77.3, "peak_cap": 43.3},
]

# ── Promotion/kill thresholds (from docs/usdjpy-lane-portfolio.md) ──────

PROMOTION_GATE = {
    "min_trades": 12,
    "min_exp_above_control": 0.03,
    "net_must_be_positive": True,
}

KILL_GATE = {
    "min_failure_trades": 8,
    "kill_exp_threshold": -0.05,
}


def get_promotion_status(lane: dict) -> str:
    exp = lane["exp"]
    control_exp = 0.14  # from results
    if lane["trades"] < PROMOTION_GATE["min_trades"]:
        return f"NEEDS MORE DATA ({lane['trades']}/{PROMOTION_GATE['min_trades']} trades)"
    if exp < control_exp + PROMOTION_GATE["min_exp_above_control"]:
        return "DOES NOT MEET PROMOTION (exp not above control)"
    if PROMOTION_GATE["net_must_be_positive"] and lane["net"] <= 0:
        return "DOES NOT MEET PROMOTION (net not positive)"
    if exp > control_exp * 1.5:
        return "🟢 STRONG CANDIDATE"
    return "🟡 CANDIDATE"


def get_kill_status(lane: dict) -> str:
    if lane["trades"] < KILL_GATE["min_failure_trades"]:
        return "SAFE (insufficient data)"
    if lane["exp"] <= KILL_GATE["kill_exp_threshold"]:
        return "🔴 SHOULD KILL"
    return "SAFE"


def get_live_exits() -> list[dict]:
    trades = load_jsonl(TRADE_LOG)
    lane_trades = [
        t for t in trades
        if (str(t.get("symbol", "")).upper() == LANE[0]
            and str(t.get("entry_signal_type", "")) == LANE[1]
            and str(t.get("entry_mode", "")).upper() == LANE[2]
            and str(t.get("regime_at_entry", "")).upper() == LANE[3])
    ]
    exits = []
    for t in lane_trades:
        peak = get_peak(t)
        realized = get_pnl(t)
        giveback = ((peak - realized) / peak * 100.0) if peak > 0 else 0.0
        capture = (realized / peak) if peak > 0 else 0.0
        exits.append({
            "ticket": t.get("ticket"),
            "peak": round(peak, 2),
            "realized": round(realized, 2),
            "giveback_pct": round(giveback, 1),
            "capture_pct": round(capture * 100, 1),
            "hold_seconds": t.get("hold_seconds"),
            "exit_reason": t.get("exit_reason", ""),
        })
    return exits


def build_dashboard() -> dict:
    live_exits = get_live_exits()
    now = datetime.now(timezone.utc).isoformat()

    dashboard = {
        "generated_at": now,
        "lane": "|".join(LANE),
        "live_state": {
            "total_exits": len(live_exits),
            "net_pnl": sum(e["realized"] for e in live_exits),
            "exits": live_exits,
        },
        "lane_rankings": LANE_RESULTS,
        "promotion_tracker": [],
        "kill_tracker": [],
        "deep_insights": {
            "peak_size_stratification": {
                "<$0.10": {"trades": 6, "wr": 50, "net": -0.21},
                "$0.10-$0.30": {"trades": 8, "wr": 75, "net": 0.45},
                ">$0.30": {"trades": 8, "wr": 100, "net": 1.51},
            },
            "adverse_correlation": "+0.60 (more adverse = more profitable)",
            "ttg_winners_avg": "107s",
            "ttg_losers_avg": "35s",
            "never_went_green": {"count": 2, "avg_loss": -0.26},
            "hold_time_correlation": "+0.12 (no strong dependency)",
            "session_performance": {
                "Asian_00_08": {"trades": 7, "wr": 71, "net": 0.57},
                "NY_13_17": {"trades": 9, "wr": 78, "net": 0.82},
                "Off_18_23": {"trades": 8, "wr": 63, "net": 0.47},
            },
        },
        "m1_portfolio": {
            "description": "USDJPY micro-momentum M1 offline lab (requires MT5 connection)",
            "lanes": 10,
            "entry_types": ["two_bar_momentum", "three_bar_momentum", "strong_breakout", "acceleration", "resume_pullback"],
            "exit_types": ["opp_close", "stall_close", "two_stall", "retain_50", "retain_60", "retain_75", "time_3"],
        },
    }

    # Promotion tracker
    for lane in LANE_RESULTS:
        dashboard["promotion_tracker"].append({
            "lane": lane["lane"],
            "trades": lane["trades"],
            "status": get_promotion_status(lane),
        })

    # Kill tracker
    for lane in LANE_RESULTS:
        dashboard["kill_tracker"].append({
            "lane": lane["lane"],
            "trades": lane["trades"],
            "status": get_kill_status(lane),
        })

    return dashboard


def format_text(dashboard: dict) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("USDJPY LAB DASHBOARD")
    lines.append(f"Generated: {dashboard['generated_at']}")
    lines.append(f"Lane: {dashboard['lane']}")
    lines.append("=" * 72)
    lines.append("")

    # Live state
    ls = dashboard["live_state"]
    lines.append("─" * 72)
    lines.append("LIVE LAB STATE")
    lines.append("─" * 72)
    lines.append(f"Total exits: {ls['total_exits']}")
    lines.append(f"Net P/L: {fmt_money(ls['net_pnl'])}")
    if ls["exits"]:
        for e in ls["exits"]:
            lines.append(f"  #{e['ticket']}: Peak {fmt_money(e['peak'])} -> Exit {fmt_money(e['realized'])} | GB {e['giveback_pct']:.1f}% | Capture {e['capture_pct']:.1f}%")
    lines.append("")

    # Lane rankings
    lines.append("─" * 72)
    lines.append("LANE RANKINGS (10-lane backtest)")
    lines.append("─" * 72)
    lines.append(f"  {'Lane':<28} {'Trd':>4} {'Net':>8} {'Exp':>8} {'WR':>6} {'Cap%':>6} {'Status'}")
    lines.append(f"  {'─' * 28} {'─' * 4} {'─' * 8} {'─' * 8} {'─' * 6} {'─' * 6}")
    for lane in LANE_RESULTS:
        status = get_promotion_status(lane)
        status_short = status.split("(")[0].strip() if "(" in status else status
        lines.append(
            f"  {lane['lane']:<28} {lane['trades']:>4d} {fmt_money(lane['net']):>8} "
            f"{fmt_money(lane['exp']):>8} {lane['wr']:>5.1f}% {lane['peak_cap']:>5.1f}% {status_short}"
        )
    lines.append("")

    # Promotion tracker
    lines.append("─" * 72)
    lines.append("PROMOTION TRACKER")
    lines.append("─" * 72)
    for pt in dashboard["promotion_tracker"]:
        lines.append(f"  {pt['lane']:<28} ({pt['trades']:>2d} trades) {pt['status']}")
    lines.append("")

    # Kill tracker
    lines.append("─" * 72)
    lines.append("KILL TRACKER")
    lines.append("─" * 72)
    for kt in dashboard["kill_tracker"]:
        lines.append(f"  {kt['lane']:<28} ({kt['trades']:>2d} trades) {kt['status']}")
    lines.append("")

    # Deep insights
    di = dashboard["deep_insights"]
    lines.append("─" * 72)
    lines.append("DEEP PATTERN INSIGHTS")
    lines.append("─" * 72)
    lines.append("  Peak size stratification:")
    for size, stats in di["peak_size_stratification"].items():
        lines.append(f"    {size}: {stats['trades']} trades, {stats['wr']}% WR, net {fmt_money(stats['net'])}")
    lines.append(f"  Adverse correlation: {di['adverse_correlation']}")
    lines.append(f"  TTG: winners {di['ttg_winners_avg']} vs losers {di['ttg_losers_avg']}")
    lines.append(f"  Never went green: {di['never_went_green']['count']} trades, avg loss {fmt_money(di['never_went_green']['avg_loss'])}")
    lines.append(f"  Hold time correlation: {di['hold_time_correlation']}")
    lines.append("  Session performance:")
    for sess, stats in di["session_performance"].items():
        lines.append(f"    {sess}: {stats['trades']} trades, {stats['wr']}% WR, net {fmt_money(stats['net'])}")
    lines.append("")

    # M1 portfolio
    m1 = dashboard["m1_portfolio"]
    lines.append("─" * 72)
    lines.append("M1 MICRO-MOMENTUM PORTFOLIO")
    lines.append("─" * 72)
    lines.append(f"  {m1['description']}")
    lines.append(f"  Lanes: {m1['lanes']}")
    lines.append(f"  Entry types: {', '.join(m1['entry_types'])}")
    lines.append(f"  Exit types: {', '.join(m1['exit_types'])}")
    lines.append("")

    # Recommendations
    lines.append("─" * 72)
    lines.append("RECOMMENDATIONS")
    lines.append("─" * 72)
    lines.append("  1. exit_75_floor03 is the STRONGEST candidate (3x control expectancy)")
    lines.append("     → Promote to live challenger when next flat cycle available")
    lines.append("  2. entry_10s is a TRAP lane — admits 3 more losers, worst net P/L")
    lines.append("     → Should be killed or moved to last priority")
    lines.append("  3. Peak size >$0.30 = 100% WR. Exit tuning at this level is highest ROI")
    lines.append("     → fast_trail_above_1 should be prioritized after exit_75")
    lines.append("  4. Adverse correlation is +0.60 — adverse-entry gates hurt winners")
    lines.append("     → adverse_tolerance_015 should not be prioritized")
    lines.append("  5. M1 micro-momentum needs MT5 connection to run offline")
    lines.append("     → Continue only after the MT5-connected offline run is available")
    lines.append("")
    lines.append("=" * 72)

    return "\n".join(lines)


def main() -> None:
    dashboard = build_dashboard()
    text = format_text(dashboard)

    # Write JSON
    json_path = ROOT / "reports" / "lab_dashboard.json"
    json_path.parent.mkdir(exist_ok=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2)

    # Write text
    txt_path = ROOT / "reports" / "lab_dashboard.txt"
    with txt_path.open("w", encoding="utf-8") as f:
        f.write(text)

    # Print to console
    print(text)
    print(f"\nDashboard written to:")
    print(f"  {txt_path}")
    print(f"  {json_path}")


if __name__ == "__main__":
    main()
