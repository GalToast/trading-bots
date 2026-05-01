#!/usr/bin/env python3
"""Kelly Shadow Early Results Tracker — Real-time dashboard.

Monitors the Kelly shadow runner (PID 17404) and reports progress against
the $269/mo decorrelated and $331/mo original projections.

Usage:
    python scripts/kelly_shadow_monitor.py          # Run as foreground daemon
    python scripts/kelly_shadow_monitor.py &         # Background on Unix
    start /b python scripts\kelly_shadow_monitor.py  # Background on Windows
    python scripts/kelly_shadow_monitor.py --once    # Single snapshot then exit

Features:
  - Reads reports/kelly_shadow_state.json every 60s
  - Reads reports/kelly_shadow_events.jsonl for recent activity
  - Tracks equity, per-coin allocation, signals, entries, closes, PnL
  - Compares against $269/mo (decorrelated) and $331/mo (original) projections
  - Calculates annualized return and cycles-to-significance
  - Alerts on equity loss, coin drawdown, stuck runner, risk concentration
  - Writes reports/kelly_shadow_monitor.json every cycle
  - Writes reports/kelly_shadow_monitor.txt human-readable report
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "reports" / "kelly_shadow_state.json"
EVENTS_FILE = ROOT / "reports" / "kelly_shadow_events.jsonl"
MONITOR_STATE = ROOT / "reports" / "kelly_shadow_monitor.json"
MONITOR_REPORT = ROOT / "reports" / "kelly_shadow_monitor.txt"

# Projections from the Kelly config
PROJECTION_DECORRELATED = 269.0   # $/mo after correlation adjustment
PROJECTION_ORIGINAL = 330.53      # $/mo raw Kelly-optimal
STARTING_CAPITAL = 48.0

# Per-coin monthly projections (from kelly_optimal_runner_config.json)
COIN_PROJECTIONS = {
    "NOM-USD": 165.53,
    "GHST-USD": 67.85,
    "SUP-USD": 39.07,
    "A8-USD": 25.87,
    "CFG-USD": 32.21,
}

# Per-coin cash weights (from config)
CASH_WEIGHTS = {
    "NOM-USD": 0.2277,
    "GHST-USD": 0.1386,
    "SUP-USD": 0.1089,
    "A8-USD": 0.2772,
    "CFG-USD": 0.2475,
}

# Alert thresholds
EQUITY_LOSS_THRESHOLD = STARTING_CAPITAL        # $48
COIN_DRAWDOWN_THRESHOLD = 0.20                   # 20% of allocation
NO_ENTRIES_MAX_CYCLES = 30                       # cycles without any entry
FLOATING_PNL_RATIO = 2.0                         # floating > 2x realized

# Cycles per hour (runner polls ~every 5min = 12/hr)
CYCLES_PER_HOUR = 12.0
CYCLES_PER_DAY = CYCLES_PER_HOUR * 24
CYCLES_PER_MONTH = CYCLES_PER_DAY * 30


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state():
    """Load the shadow runner state file."""
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def read_events(last_n=100):
    """Load recent events from the JSONL log."""
    events = []
    if not EVENTS_FILE.exists():
        return events
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-last_n:]:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return events


def compute_alerts(state, events, monitor_history):
    """Check all alert conditions and return list of alerts."""
    alerts = []

    if state is None:
        alerts.append({
            "severity": "critical",
            "type": "runner_dead",
            "message": "Shadow state file not found. Runner may have died."
        })
        return alerts

    equity = state.get("total_equity", STARTING_CAPITAL)
    cycle = state.get("cycle", 0)
    ledgers = state.get("ledgers", {})

    # 1. Equity below starting capital
    if equity < EQUITY_LOSS_THRESHOLD:
        loss = EQUITY_LOSS_THRESHOLD - equity
        alerts.append({
            "severity": "critical",
            "type": "equity_loss",
            "message": f"Equity ${equity:.2f} below starting ${EQUITY_LOSS_THRESHOLD:.2f} (-${loss:.2f})"
        })

    # 2. Per-coin drawdown > 20% of allocation
    for coin, ledger in ledgers.items():
        starting = ledger.get("starting_cash", 0)
        pnl = ledger.get("pnl", 0)
        if starting > 0:
            drawdown_pct = -pnl / starting if pnl < 0 else 0
            if drawdown_pct >= COIN_DRAWDOWN_THRESHOLD:
                alerts.append({
                    "severity": "warning",
                    "type": "coin_drawdown",
                    "coin": coin,
                    "message": f"{coin} down {drawdown_pct:.1%} of allocation (${starting:.2f} -> PnL ${pnl:+.2f})"
                })

    # 3. No entries after N cycles (runner stuck)
    total_signals = sum(l.get("signals", 0) for l in ledgers.values())
    total_entries = sum(1 for l in ledgers.values() if l.get("position") == "active")
    total_closes = sum(l.get("closes", 0) for l in ledgers.values())

    # Check recent events for entries
    open_events = [e for e in events if e.get("action") == "open"]
    if cycle > NO_ENTRIES_MAX_CYCLES and len(open_events) == 0:
        alerts.append({
            "severity": "warning",
            "type": "no_entries",
            "message": f"No entries after {cycle} cycles. Runner may be stuck (session gate? backfill?)"
        })

    # 4. Floating PnL exceeds 2x realized PnL (risk concentration)
    realized_pnl = 0
    floating_pnl = 0
    for coin, ledger in ledgers.items():
        pnl = ledger.get("pnl", 0)
        pos = ledger.get("position")
        if pos == "active":
            floating_pnl += pnl
        else:
            # Flat position means PnL is realized (through closes)
            pass

    # Estimate realized PnL from cumulative close events
    close_events = [e for e in events if e.get("action") == "close"]
    for ce in close_events:
        net = ce.get("net", 0)
        realized_pnl += net

    if realized_pnl > 0 and abs(floating_pnl) > FLOATING_PNL_RATIO * realized_pnl:
        alerts.append({
            "severity": "warning",
            "type": "risk_concentration",
            "message": f"Floating PnL ${floating_pnl:+.2f} exceeds {FLOATING_PNL_RATIO:.0f}x realized ${realized_pnl:+.2f}"
        })

    # 5. Equity trending down over monitor history
    if len(monitor_history) >= 5:
        recent_equities = [h.get("equity", STARTING_CAPITAL) for h in monitor_history[-10:]]
        if len(recent_equities) >= 2:
            trend = recent_equities[-1] - recent_equities[0]
            if trend < -1.0:  # Lost more than $1 over last 10 snapshots
                alerts.append({
                    "severity": "info",
                    "type": "equity_trend_down",
                    "message": f"Equity trending down: ${recent_equities[0]:.2f} -> ${recent_equities[-1]:.2f} (${trend:+.2f} over {len(recent_equities)} snapshots)"
                })

    return alerts


def compute_projections(state, monitor_history):
    """Calculate projected returns and cycles to statistical significance."""
    if state is None:
        return {
            "decorrelated_monthly": PROJECTION_DECORRELATED,
            "original_monthly": PROJECTION_ORIGINAL,
            "annualized_rate_pct": None,
            "cycles_to_significance": None,
            "projected_monthly_actual": None,
            "trajectory_pct_of_decorrelated": None,
            "trajectory_pct_of_original": None,
        }

    equity = state.get("total_equity", STARTING_CAPITAL)
    cycle = state.get("cycle", 0)
    pnl = equity - STARTING_CAPITAL

    # Annualized return rate
    # If we've run for N cycles, estimate monthly and annualize
    cycles_elapsed = cycle
    if cycles_elapsed > 0:
        return_pct = pnl / STARTING_CAPITAL
        months_elapsed = cycles_elapsed / CYCLES_PER_MONTH
        if months_elapsed > 0:
            monthly_rate = return_pct / months_elapsed
            annualized_rate = monthly_rate * 12
            projected_monthly = monthly_rate * STARTING_CAPITAL
        else:
            annualized_rate = None
            projected_monthly = None
    else:
        annualized_rate = None
        projected_monthly = None

    # Trajectory vs projections
    trajectory_decorrelated = None
    trajectory_original = None
    if projected_monthly is not None and projected_monthly > 0:
        trajectory_decorrelated = (projected_monthly / PROJECTION_DECORRELATED) * 100
        trajectory_original = (projected_monthly / PROJECTION_ORIGINAL) * 100

    # Cycles to statistical significance
    # We need enough closes to distinguish signal from noise.
    # Rule of thumb: need ~30 independent samples (closes) for CLT.
    # At current signal rate, how many cycles until 30 closes?
    ledgers = state.get("ledgers", {})
    total_closes = sum(l.get("closes", 0) for l in ledgers.values())
    total_signals = sum(l.get("signals", 0) for l in ledgers.values())

    cycles_to_significance = None
    if total_signals > 0 and total_closes > 0:
        close_rate = total_closes / cycles_elapsed if cycles_elapsed > 0 else 0
        closes_needed = 30 - total_closes
        if closes_needed > 0 and close_rate > 0:
            cycles_to_significance = int(math.ceil(closes_needed / close_rate))
        elif closes_needed <= 0:
            cycles_to_significance = 0
    elif total_signals == 0 and cycles_elapsed > 5:
        # No signals at all after several cycles — significance is undefined
        cycles_to_significance = -1  # sentinel: runner may be stuck

    return {
        "decorrelated_monthly": PROJECTION_DECORRELATED,
        "original_monthly": PROJECTION_ORIGINAL,
        "annualized_rate_pct": round(annualized_rate * 100, 2) if annualized_rate is not None else None,
        "cycles_to_significance": cycles_to_significance,
        "projected_monthly_actual": round(projected_monthly, 2) if projected_monthly is not None else None,
        "trajectory_pct_of_decorrelated": round(trajectory_decorrelated, 1) if trajectory_decorrelated is not None else None,
        "trajectory_pct_of_original": round(trajectory_original, 1) if trajectory_original is not None else None,
        "total_closes": total_closes,
        "closes_needed_for_significance": max(0, 30 - total_closes),
    }


def format_report(state, events, alerts, projections, monitor_history):
    """Format human-readable report."""
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append("=" * 72)
    lines.append("  KELLY SHADOW MONITOR")
    lines.append(f"  {now}")
    lines.append("=" * 72)

    if state is None:
        lines.append("")
        lines.append("  SHADOW STATE: NOT FOUND")
        lines.append(f"  Expected at: {STATE_FILE}")
        lines.append("")
        lines.append("  The runner may have just started or may not be running.")
        lines.append("  Check if PID 17404 is still alive:")
        lines.append("    tasklist | findstr python")
        lines.append("")
        lines.append("=" * 72)
        return "\n".join(lines)

    cycle = state.get("cycle", 0)
    equity = state.get("total_equity", STARTING_CAPITAL)
    pnl = state.get("total_pnl", 0)
    return_pct = state.get("return_pct", 0)
    updated = state.get("updated_at", "unknown")
    ledgers = state.get("ledgers", {})

    # Summary
    lines.append("")
    lines.append(f"  Cycle: {cycle}  |  Equity: ${equity:.2f}  |  PnL: ${pnl:+.2f}  |  Return: {return_pct:+.2f}%")
    lines.append(f"  Started: ${STARTING_CAPITAL:.2f}  |  Updated: {updated}")

    # Alert summary
    if alerts:
        critical = [a for a in alerts if a["severity"] == "critical"]
        warnings = [a for a in alerts if a["severity"] == "warning"]
        infos = [a for a in alerts if a["severity"] == "info"]
        if critical:
            lines.append(f"  ALERTS: {len(critical)} CRITICAL, {len(warnings)} WARN, {len(infos)} INFO")
        elif warnings:
            lines.append(f"  ALERTS: {len(warnings)} WARN, {len(infos)} INFO")
        else:
            lines.append(f"  ALERTS: {len(infos)} INFO")
    else:
        lines.append("  Status: OK (no alerts)")

    # Projection comparison
    lines.append("")
    lines.append("  PROJECTION COMPARISON")
    lines.append(f"  {'Metric':<30} {'Target':>12} {'Actual':>12} {'%':>8}")
    lines.append(f"  {'─' * 30} {'─' * 12} {'─' * 12} {'─' * 8}")

    proj = projections
    lines.append(f"  {'Monthly ($/mo)':<30} ${PROJECTION_DECORRELATED:>10.2f}  {format_val(proj.get('projected_monthly_actual'), '$', '.2f'):>12}  {format_val(proj.get('trajectory_pct_of_decorrelated'), '', '.1f'):>7}%")
    lines.append(f"  {'Monthly original Kelly':<30} ${PROJECTION_ORIGINAL:>10.2f}  {format_val(proj.get('projected_monthly_actual'), '$', '.2f'):>12}  {format_val(proj.get('trajectory_pct_of_original'), '', '.1f'):>7}%")
    lines.append(f"  {'Annualized return':<30} {'—':>12}  {format_val(proj.get('annualized_rate_pct'), '', '.1f') + '%':>12}")
    lines.append(f"  {'Closes for significance':<30} {'30':>12}  {proj.get('total_closes', 0):>12d}  ({proj.get('closes_needed_for_significance', 30)} remaining)")
    if proj.get("cycles_to_significance") is not None:
        cts = proj["cycles_to_significance"]
        cts_str = f"{cts} cycles" if cts >= 0 else "STUCK?"
        lines.append(f"  {'Est. cycles to significance':<30} {'—':>12}  {cts_str:>12}")

    # Per-coin detail
    lines.append("")
    lines.append("  PER-COIN BREAKDOWN")
    lines.append(f"  {'Coin':<12} {'Strat':<12} {'Equity':>9} {'PnL':>9} {'Ret%':>7} {'Sig':>4} {'Cls':>4} {'Pos':>6}")
    lines.append(f"  {'─' * 12} {'─' * 12} {'─' * 9} {'─' * 9} {'─' * 7} {'─' * 4} {'─' * 4} {'─' * 6}")

    total_signals = 0
    total_closes = 0
    total_positions = 0
    total_floating = 0
    total_realized = 0

    # Sort by cash weight (highest first)
    coin_order = sorted(ledgers.keys(), key=lambda c: CASH_WEIGHTS.get(c, 0), reverse=True)

    for coin in coin_order:
        l = ledgers[coin]
        equity_c = l.get("equity", 0)
        pnl_c = l.get("pnl", 0)
        ret_c = l.get("return_pct", 0)
        signals = l.get("signals", 0)
        closes = l.get("closes", 0)
        pos = l.get("position", "flat")
        strat = l.get("strategy", "?")

        total_signals += signals
        total_closes += closes
        if pos == "active":
            total_positions += 1
            total_floating += pnl_c
        else:
            total_realized += pnl_c

        pos_icon = "ACTIVE" if pos == "active" else "flat"
        lines.append(f"  {coin:<12} {strat:<12} ${equity_c:>7.2f} ${pnl_c:>+7.2f} {ret_c:>+6.1f}% {signals:>4d} {closes:>4d} {pos_icon:>6}")

    lines.append(f"  {'':<12} {'TOTAL':<12} ${equity:>7.2f} ${pnl:>+7.2f} {return_pct:>+6.1f}% {total_signals:>4d} {total_closes:>4d} {total_positions:>4d} pos")

    # Recent events
    open_events = [e for e in events if e.get("action") == "open"]
    close_events = [e for e in events if e.get("action") == "close"]
    signal_events = [e for e in events if e.get("action") == "signal"]

    if open_events or close_events:
        lines.append("")
        lines.append(f"  RECENT EVENTS (last {len(events)} in log)")
        for e in open_events[-5:]:
            ts = e.get("ts_utc", "?")
            coin = e.get("coin", "?")
            price = e.get("entry_price", "?")
            deploy = e.get("deploy", "?")
            lines.append(f"    OPEN  {coin:<12} @ ${price}  deploy=${deploy}  {ts}")
        for e in close_events[-5:]:
            ts = e.get("ts_utc", "?")
            coin = e.get("coin", "?")
            net = e.get("net", "?")
            reason = e.get("reason", "?")
            lines.append(f"    CLOSE {coin:<12}  net=${net}  reason={reason}  {ts}")

    # Alert details
    if alerts:
        lines.append("")
        lines.append("  ALERT DETAILS")
        for a in alerts:
            sev = a["severity"].upper()
            lines.append(f"    [{sev}] {a['message']}")

    # Footer
    lines.append("")
    lines.append(f"  Monitor state: {MONITOR_STATE}")
    lines.append(f"  Shadow state:  {STATE_FILE}")
    lines.append(f"  Shadow events: {EVENTS_FILE}")
    lines.append("=" * 72)

    return "\n".join(lines)


def format_val(v, prefix="", fmt=".2f"):
    """Format a value, handling None."""
    if v is None:
        return "—"
    try:
        return f"{prefix}{v:{fmt}}"
    except (TypeError, ValueError):
        return str(v)


def run_once(monitor_history):
    """Execute one monitoring cycle. Returns (state, report_text)."""
    state = read_state()
    events = read_events(100)

    # Compute alerts and projections
    alerts = compute_alerts(state, events, monitor_history)
    projections = compute_projections(state, monitor_history)

    # Build monitor state JSON
    monitor_state = {
        "timestamp": utc_now_iso(),
        "runner_alive": state is not None,
    }

    if state:
        equity = state.get("total_equity", STARTING_CAPITAL)
        pnl = state.get("total_pnl", 0)
        ledgers = state.get("ledgers", {})

        monitor_state["cycle"] = state.get("cycle", 0)
        monitor_state["equity"] = equity
        monitor_state["pnl"] = pnl
        monitor_state["return_pct"] = state.get("return_pct", 0)
        monitor_state["starting_capital"] = STARTING_CAPITAL

        # Per-coin summary
        coins = []
        for coin, l in ledgers.items():
            coins.append({
                "coin": coin,
                "strategy": l.get("strategy", "?"),
                "equity": l.get("equity", 0),
                "pnl": l.get("pnl", 0),
                "return_pct": l.get("return_pct", 0),
                "signals": l.get("signals", 0),
                "closes": l.get("closes", 0),
                "wins": l.get("wins", 0),
                "losses": l.get("losses", 0),
                "win_rate": l.get("win_rate", 0),
                "position": l.get("position", "flat"),
                "starting_cash": l.get("starting_cash", 0),
            })
        monitor_state["coins"] = coins

        # Totals
        monitor_state["total_signals"] = sum(l.get("signals", 0) for l in ledgers.values())
        monitor_state["total_closes"] = sum(l.get("closes", 0) for l in ledgers.values())
        monitor_state["active_positions"] = sum(1 for l in ledgers.values() if l.get("position") == "active")

        # Floating vs realized PnL
        floating_pnl = sum(l.get("pnl", 0) for l in ledgers.values() if l.get("position") == "active")
        realized_pnl = sum(l.get("pnl", 0) for l in ledgers.values() if l.get("position") != "active")
        monitor_state["floating_pnl"] = floating_pnl
        monitor_state["realized_pnl"] = realized_pnl

    # Merge projections and alerts
    monitor_state["projections"] = projections
    monitor_state["alerts"] = alerts

    # Format report
    report_text = format_report(state, events, alerts, projections, monitor_history)

    # Write monitor state JSON
    try:
        MONITOR_STATE.parent.mkdir(parents=True, exist_ok=True)
        with open(MONITOR_STATE, "w", encoding="utf-8") as f:
            json.dump(monitor_state, f, indent=2, sort_keys=True)
    except OSError as e:
        print(f"  [WARN] Failed to write monitor state: {e}", flush=True)

    # Write human-readable report
    try:
        MONITOR_REPORT.parent.mkdir(parents=True, exist_ok=True)
        with open(MONITOR_REPORT, "w", encoding="utf-8") as f:
            f.write(report_text)
    except OSError as e:
        print(f"  [WARN] Failed to write monitor report: {e}", flush=True)

    return state, report_text, monitor_state


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kelly Shadow Early Results Tracker")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between checks (default 60)")
    parser.add_argument("--once", action="store_true", help="Single snapshot then exit")
    args = parser.parse_args()

    # Load monitor history if exists
    monitor_history = []
    if MONITOR_STATE.exists():
        try:
            with open(MONITOR_STATE, encoding="utf-8") as f:
                history_data = json.load(f)
                # Keep last 20 snapshots for trend analysis
                if isinstance(history_data, dict):
                    monitor_history.append(history_data)
                elif isinstance(history_data, list):
                    monitor_history = history_data[-20:]
        except (json.JSONDecodeError, OSError):
            pass

    print(f"Kelly Shadow Monitor starting (interval={args.interval}s)", flush=True)
    print(f"  State:  {STATE_FILE}", flush=True)
    print(f"  Events: {EVENTS_FILE}", flush=True)
    print(f"  Output: {MONITOR_STATE}", flush=True)
    print(f"  Report: {MONITOR_REPORT}", flush=True)
    print()

    while True:
        state, report_text, monitor_state = run_once(monitor_history)

        # Print report to stdout
        print(report_text, flush=True)

        # Append to history
        if monitor_state:
            monitor_history.append(monitor_state)
            # Keep only last 20
            if len(monitor_history) > 20:
                monitor_history = monitor_history[-20:]

        # Print alert summary to stderr for visibility
        alerts = monitor_state.get("alerts", [])
        critical = [a for a in alerts if a["severity"] == "critical"]
        if critical:
            for a in critical:
                print(f"\n*** CRITICAL: {a['message']}", flush=True, file=sys.stderr)

        if args.once:
            break

        print(f"\n--- Next check in {args.interval}s ---\n", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
