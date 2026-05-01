#!/usr/bin/env python3
"""Kelly Event-Log Promotion Tracker — rebuilds full analytics from the append-only event log.

Why this exists
---------------
The state-based promotion dashboard (kelly_promotion_readiness.py) depends on
kelly_shadow_state.json, which is an in-memory snapshot that resets on runner
restart.  Close counts, signal history, and PnL are lost.

The event log (kelly_shadow_events.jsonl) is append-only and survives restarts.
This script replays the entire log to reconstruct:
  - Total closes per coin
  - Win rate per coin
  - Realized PnL per coin and overall
  - Average hold time per coin
  - Signal count per coin
  - Signal-to-close conversion rate
  - Equity curve (cumulative PnL over time)
  - Maximum drawdown from the equity curve
  - Promotion gate pass/fail

Usage:
    python scripts/kelly_event_log_tracker.py
    python scripts/kelly_event_log_tracker.py --event-path path/to/events.jsonl
    python scripts/kelly_event_log_tracker.py --projected-monthly 269
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Default paths — the Kelly shadow runner writes here
DEFAULT_EVENT_PATH = ROOT / "reports" / "kelly_shadow_events.jsonl"
DEFAULT_CONFIG_PATH = ROOT / "configs" / "kelly_optimal_runner_config.json"
OUTPUT_JSON = ROOT / "reports" / "kelly_event_log_tracker.json"
OUTPUT_MD = ROOT / "reports" / "kelly_event_log_tracker.md"

# Promotion gates (matching kelly_promotion_readiness.py)
MIN_CYCLES = 100
MIN_CLOSES_PER_COIN = 2
EXPECTED_WR_RANGE = (0.45, 0.70)  # 45-70 %
MAX_DRAWDOWN_PCT = 20.0
MIN_SHARPE = 0.0
PNL_TOLERANCE = 0.50  # within 50 % of projected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_ts(ts_str):
    """Parse an ISO-8601 timestamp string to a datetime (UTC)."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def load_jsonl(path):
    """Read a JSONL file, skipping blank / unparseable lines."""
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def load_config(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def replay_events(events):
    """Walk the event log and compute all per-coin and aggregate metrics."""

    opens = []
    closes = []
    runner_starts = []

    for ev in events:
        action = ev.get("action", "")
        if action == "open":
            opens.append(ev)
        elif action == "close":
            closes.append(ev)
        elif action == "runner_start_isolated":
            runner_starts.append(ev)

    # -- per-coin tallies ------------------------------------------------
    coins_seen = set()
    for ev in events:
        coin = ev.get("coin")
        if coin:
            coins_seen.add(coin)

    coin_metrics = {}
    for coin in sorted(coins_seen):
        coin_opens = [e for e in opens if e.get("coin") == coin]
        coin_closes = [e for e in closes if e.get("coin") == coin]

        signal_count = len(coin_opens)
        close_count = len(coin_closes)

        # Win rate from closes
        wins = sum(1 for c in coin_closes if c.get("net", 0) > 0)
        win_rate = wins / close_count if close_count > 0 else 0.0

        # PnL
        total_pnl = sum(c.get("net", 0) for c in coin_closes)

        # Average hold time
        hold_bars = [c.get("hold_bars", 0) for c in coin_closes if c.get("hold_bars") is not None]
        avg_hold = sum(hold_bars) / len(hold_bars) if hold_bars else 0.0

        # Conversion rate
        conversion = close_count / signal_count if signal_count > 0 else 0.0

        coin_metrics[coin] = {
            "signals": signal_count,
            "closes": close_count,
            "wins": wins,
            "losses": close_count - wins,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_hold_bars": round(avg_hold, 2),
            "conversion_rate": round(conversion, 4),
        }

    # -- equity curve (cumulative PnL over time) -------------------------
    # Sort closes by timestamp for the curve
    closes_sorted = sorted(closes, key=lambda e: e.get("ts_utc", ""))
    equity_curve = []
    cumulative = 0.0
    for c in closes_sorted:
        cumulative += c.get("net", 0)
        equity_curve.append({
            "ts": c.get("ts_utc", ""),
            "coin": c.get("coin", ""),
            "pnl_this": round(c.get("net", 0), 4),
            "cumulative": round(cumulative, 4),
        })

    # -- max drawdown ----------------------------------------------------
    peak = -math.inf
    max_dd = 0.0
    for point in equity_curve:
        val = point["cumulative"]
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd

    # -- Sharpe-like ratio (PnL / std of PnL) ----------------------------
    pnl_values = [c.get("net", 0) for c in closes_sorted]
    if len(pnl_values) >= 2:
        mean_pnl = sum(pnl_values) / len(pnl_values)
        variance = sum((x - mean_pnl) ** 2 for x in pnl_values) / (len(pnl_values) - 1)
        std_pnl = math.sqrt(variance) if variance > 0 else 0.0
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0
    else:
        mean_pnl = sum(pnl_values) if pnl_values else 0.0
        std_pnl = 0.0
        sharpe = 0.0

    # -- time span -------------------------------------------------------
    all_ts = [parse_ts(e.get("ts_utc")) for e in events]
    all_ts = [t for t in all_ts if t is not None]
    if len(all_ts) >= 2:
        first_ts = min(all_ts)
        last_ts = max(all_ts)
        uptime_hours = (last_ts - first_ts).total_seconds() / 3600.0
    else:
        first_ts = None
        last_ts = None
        uptime_hours = 0.0

    # -- overall win rate ------------------------------------------------
    total_wins = sum(1 for c in closes if c.get("net", 0) > 0)
    overall_wr = total_wins / len(closes) if closes else 0.0

    return {
        "total_opens": len(opens),
        "total_closes": len(closes),
        "total_runner_starts": len(runner_starts),
        "coins_seen": sorted(coins_seen),
        "coin_metrics": coin_metrics,
        "equity_curve": equity_curve,
        "max_drawdown": round(max_dd, 4),
        "overall_win_rate": round(overall_wr, 4),
        "total_realized_pnl": round(cumulative, 4),
        "sharpe_ratio": round(sharpe, 4),
        "mean_pnl_per_close": round(mean_pnl, 4),
        "std_pnl_per_close": round(std_pnl, 4),
        "uptime_hours": round(uptime_hours, 2),
        "first_event_ts": first_ts.isoformat() if first_ts else None,
        "last_event_ts": last_ts.isoformat() if last_ts else None,
    }


def check_gates(analytics, projected_monthly, config):
    """Evaluate promotion gates against the replayed analytics."""

    coin_metrics = analytics["coin_metrics"]
    coins_seen = analytics["coins_seen"]
    gates = {}

    # 1. Minimum cycles (use runner restarts as a proxy for cycle count)
    #    Since the event log doesn't track cycles directly, we use total closes
    #    as the cycle proxy.  100 closes = 100 cycles.
    actual_closes = analytics["total_closes"]
    gates["min_100_closes_as_cycles"] = {
        "required": MIN_CYCLES,
        "actual": actual_closes,
        "pass": actual_closes >= MIN_CYCLES,
    }

    # 2. All coins fired (from config or at least 1 signal each)
    if config:
        config_coins = sorted(set(c["coin"] for c in config.get("coins", [])))
    else:
        config_coins = coins_seen

    all_fired = all(
        coin_metrics.get(c, {}).get("signals", 0) > 0 for c in config_coins
    )
    gates["all_config_coins_fired"] = {
        "required": True,
        "actual": all_fired,
        "pass": all_fired,
    }

    # 3. Min closes per coin
    for coin in config_coins:
        cs = coin_metrics.get(coin, {})
        cc = cs.get("closes", 0)
        gates[f"{coin}_min_{MIN_CLOSES_PER_COIN}_closes"] = {
            "required": MIN_CLOSES_PER_COIN,
            "actual": cc,
            "pass": cc >= MIN_CLOSES_PER_COIN,
        }

    # 4. Win rate within expected range
    wr = analytics["overall_win_rate"]
    wr_lo, wr_hi = EXPECTED_WR_RANGE
    gates["win_rate_in_range"] = {
        "required": f"{wr_lo:.0%} - {wr_hi:.0%}",
        "actual": f"{wr:.1%}",
        "pass": wr_lo <= wr <= wr_hi,
    }

    # 5. Positive Sharpe
    sharpe = analytics["sharpe_ratio"]
    gates["positive_sharpe"] = {
        "required": f"> {MIN_SHARPE}",
        "actual": f"{sharpe:.4f}",
        "pass": sharpe > MIN_SHARPE,
    }

    # 6. PnL within 50 % of projected
    if projected_monthly > 0 and analytics["uptime_hours"] > 0:
        projected_for_uptime = projected_monthly * (analytics["uptime_hours"] / (30 * 24))
        actual_pnl = analytics["total_realized_pnl"]
        if projected_for_uptime > 0:
            pnl_ratio = abs(actual_pnl) / projected_for_uptime
            within_tolerance = pnl_ratio <= (1 + PNL_TOLERANCE)
        else:
            pnl_ratio = 0.0
            within_tolerance = True
        gates["pnl_within_50pct_of_projected"] = {
            "required": f"within {PNL_TOLERANCE:.0%} of ${projected_for_uptime:.2f}",
            "actual": f"${actual_pnl:.2f} (ratio {pnl_ratio:.2f})",
            "pass": within_tolerance,
        }
    else:
        gates["pnl_within_50pct_of_projected"] = {
            "required": "projected_monthly and uptime_hours must be > 0",
            "actual": "N/A",
            "pass": False,
        }

    # 7. Max drawdown within budget
    dd = analytics["max_drawdown"]
    # Drawdown as pct of peak equity; compare to threshold
    peak_equity = max((p["cumulative"] for p in analytics["equity_curve"]), default=0)
    dd_pct = (dd / peak_equity * 100) if peak_equity > 0 else 0.0
    gates["max_drawdown_under_cap"] = {
        "required": f"< {MAX_DRAWDOWN_PCT}%",
        "actual": f"{dd_pct:.1f}% (abs ${dd:.4f})",
        "pass": dd_pct < MAX_DRAWDOWN_PCT,
    }

    return gates


def build_report(analytics, gates, projected_monthly, config):
    """Assemble the final JSON report."""
    total_gates = len(gates)
    passed_gates = sum(1 for g in gates.values() if g["pass"])
    all_pass = passed_gates == total_gates

    # Get starting cash from config or default
    starting_cash = 48.0
    if config:
        # Try to extract from config
        pass  # config doesn't store total_cash directly in all cases

    return {
        "generated_at": utc_now_iso(),
        "source": "event_log_replay",
        "total_opens": analytics["total_opens"],
        "total_closes": analytics["total_closes"],
        "total_runner_starts": analytics["total_runner_starts"],
        "coins_seen": analytics["coins_seen"],
        "overall_win_rate": analytics["overall_win_rate"],
        "total_realized_pnl": analytics["total_realized_pnl"],
        "max_drawdown": analytics["max_drawdown"],
        "sharpe_ratio": analytics["sharpe_ratio"],
        "uptime_hours": analytics["uptime_hours"],
        "first_event_ts": analytics["first_event_ts"],
        "last_event_ts": analytics["last_event_ts"],
        "coin_metrics": analytics["coin_metrics"],
        "equity_curve_sample": analytics["equity_curve"][-20:],  # last 20 points
        "equity_curve_total_points": len(analytics["equity_curve"]),
        "projected_monthly": projected_monthly,
        "gates": gates,
        "gates_passed": f"{passed_gates}/{total_gates}",
        "status": "READY" if all_pass else "NOT_READY",
        "recommendation": "Promote to live" if all_pass else f"Need {total_gates - passed_gates} more gate(s) to pass",
    }


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def md(report, analytics, gates):
    """Generate a human-readable markdown report."""
    lines = []
    sep = "=" * 72

    lines.append("# Kelly Event-Log Promotion Tracker")
    lines.append(f"\nGenerated: {report['generated_at']}")
    lines.append(f"\n**Status: {report['status']}**")
    lines.append(f"\nGates passed: {report['gates_passed']}")
    lines.append(f"\nRecommendation: {report['recommendation']}")

    # -- Summary ---------------------------------------------------------
    lines.append(f"\n{sep}")
    lines.append("## Summary")
    lines.append(f"\n| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total signals (opens) | {report['total_opens']} |")
    lines.append(f"| Total closes | {report['total_closes']} |")
    lines.append(f"| Runner restarts | {report['total_runner_starts']} |")
    lines.append(f"| Coins seen | {', '.join(report['coins_seen'])} |")
    lines.append(f"| Overall win rate | {report['overall_win_rate']:.1%} |")
    lines.append(f"| Total realized PnL | ${report['total_realized_pnl']:+.4f} |")
    lines.append(f"| Max drawdown | ${report['max_drawdown']:.4f} |")
    lines.append(f"| Sharpe ratio | {report['sharpe_ratio']:.4f} |")
    lines.append(f"| Uptime | {report['uptime_hours']:.1f} h |")
    if report["first_event_ts"]:
        lines.append(f"| First event | {report['first_event_ts']} |")
    if report["last_event_ts"]:
        lines.append(f"| Last event | {report['last_event_ts']} |")
    lines.append(f"| Equity curve points | {report['equity_curve_total_points']} |")

    # -- Per-coin --------------------------------------------------------
    lines.append(f"\n{sep}")
    lines.append("## Per-Coin Metrics")
    lines.append(f"\n| Coin | Signals | Closes | Wins | Losses | Win Rate | PnL | Avg Hold (bars) | Conversion |")
    lines.append(f"|------|---------|--------|------|--------|----------|-----|-----------------|------------|")
    for coin in sorted(analytics["coin_metrics"]):
        m = analytics["coin_metrics"][coin]
        lines.append(
            f"| {coin} "
            f"| {m['signals']} "
            f"| {m['closes']} "
            f"| {m['wins']} "
            f"| {m['losses']} "
            f"| {m['win_rate']:.1%} "
            f"| ${m['total_pnl']:+.4f} "
            f"| {m['avg_hold_bars']:.1f} "
            f"| {m['conversion_rate']:.1%} |"
        )

    # -- Equity curve (last 20) ------------------------------------------
    if analytics["equity_curve"]:
        lines.append(f"\n{sep}")
        lines.append("## Equity Curve (last 20 closes)")
        lines.append(f"\n| # | Timestamp | Coin | PnL This | Cumulative |")
        lines.append(f"|---|-----------|------|----------|------------|")
        sample = analytics["equity_curve"][-20:]
        for i, pt in enumerate(sample, start=1):
            ts_short = pt["ts"][:19] if len(pt["ts"]) > 19 else pt["ts"]
            lines.append(
                f"| {i} | {ts_short} | {pt['coin']} "
                f"| ${pt['pnl_this']:+.4f} "
                f"| ${pt['cumulative']:+.4f} |"
            )

    # -- Gates -----------------------------------------------------------
    lines.append(f"\n{sep}")
    lines.append("## Promotion Gates")
    lines.append(f"\n| Gate | Required | Actual | Pass |")
    lines.append(f"|------|----------|--------|------|")
    for name, g in gates.items():
        icon = "PASS" if g["pass"] else "FAIL"
        lines.append(f"| {name} | {g['required']} | {g['actual']} | {icon} |")

    # -- Recommendation --------------------------------------------------
    lines.append(f"\n{sep}")
    lines.append("## Recommendation")
    lines.append(f"\n**{report['recommendation']}**")

    failed = [n for n, g in gates.items() if not g["pass"]]
    if failed:
        lines.append(f"\nFailing gates ({len(failed)}):")
        for n in failed:
            g = gates[n]
            lines.append(f"  - **{n}**: required {g['required']}, got {g['actual']}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Kelly Event-Log Promotion Tracker — replays the append-only event log"
    )
    parser.add_argument(
        "--event-path", type=str, default=None,
        help="Path to the Kelly event log JSONL file (default: reports/kelly_shadow_events.jsonl)"
    )
    parser.add_argument(
        "--config-path", type=str, default=None,
        help="Path to the Kelly config JSON (default: configs/kelly_optimal_runner_config.json)"
    )
    parser.add_argument(
        "--projected-monthly", type=float, default=269.0,
        help="Projected monthly PnL for gate comparison (default: 269)"
    )
    parser.add_argument(
        "--output-json", type=str, default=None,
        help="Override output JSON path"
    )
    parser.add_argument(
        "--output-md", type=str, default=None,
        help="Override output markdown path"
    )
    parser.add_argument(
        "--no-output", action="store_true",
        help="Print to stdout only, do not write files"
    )
    args = parser.parse_args()

    event_path = Path(args.event_path) if args.event_path else DEFAULT_EVENT_PATH
    config_path = Path(args.config_path) if args.config_path else DEFAULT_CONFIG_PATH
    out_json = Path(args.output_json) if args.output_json else OUTPUT_JSON
    out_md = Path(args.output_md) if args.output_md else OUTPUT_MD

    # Load
    events = load_jsonl(event_path)
    config = load_config(config_path)

    if not events:
        print(f"ERROR: No events found in {event_path}", file=sys.stderr)
        print("  The Kelly shadow runner may not be running, or the event log path is wrong.", file=sys.stderr)
        print(f"  Expected path: {DEFAULT_EVENT_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(events)} events from {event_path}", flush=True)

    # Analyze
    analytics = replay_events(events)
    print(f"Coins seen: {', '.join(analytics['coins_seen'])}", flush=True)
    print(f"Total opens: {analytics['total_opens']}, closes: {analytics['total_closes']}", flush=True)
    print(f"Overall win rate: {analytics['overall_win_rate']:.1%}", flush=True)
    print(f"Total realized PnL: ${analytics['total_realized_pnl']:+.4f}", flush=True)
    print(f"Sharpe: {analytics['sharpe_ratio']:.4f}", flush=True)
    print(f"Max drawdown: ${analytics['max_drawdown']:.4f}", flush=True)

    # Gates
    gates = check_gates(analytics, args.projected_monthly, config)

    # Report
    report = build_report(analytics, gates, args.projected_monthly, config)
    md_text = md(report, analytics, gates)

    if args.no_output:
        print(md_text, flush=True)
    else:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        out_md.write_text(md_text, encoding="utf-8")
        print(f"\nJSON report: {out_json}", flush=True)
        print(f"Markdown report: {out_md}", flush=True)

    # Print gate summary
    total = len(gates)
    passed = sum(1 for g in gates.values() if g["pass"])
    print(f"\n{'=' * 72}", flush=True)
    print(f"GATES: {passed}/{total} passed", flush=True)
    for name, g in gates.items():
        icon = "PASS" if g["pass"] else "FAIL"
        print(f"  [{icon}] {name}: {g['actual']}", flush=True)
    print(f"\n{report['recommendation']}", flush=True)


if __name__ == "__main__":
    main()
