#!/usr/bin/env python3
"""
Toxicity Window Correlation

Correlates microstructure events from the predatory shadow monitor with
RAVE RSI MR live V2 trade outcomes to determine whether toxic market
conditions predict worse trade performance.

Usage:
    python scripts/toxicity_window_correlation.py

Reads from:
    reports/rave_rsi_mr_live_v2_events.jsonl
    reports/predatory_shadow_monitor_events.jsonl

Writes:
    reports/toxicity_correlation_results.json
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Repo-aware: locate reports relative to the project root.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")

V2_EVENTS_PATH = os.path.join(REPORTS_DIR, "rave_rsi_mr_live_v2_events.jsonl")
PREDATORY_EVENTS_PATH = os.path.join(REPORTS_DIR, "predatory_shadow_monitor_events.jsonl")
OUTPUT_PATH = os.path.join(REPORTS_DIR, "toxicity_correlation_results.json")

# Toxicity window: +/- N minutes around trade entry
TOXICITY_WINDOW_MINUTES = 10

# Toxic event action prefixes (match on startswith for robustness)
TOXIC_ACTIONS = [
    "kraken_warp_flush",
    "magnetic_wall_touch",
    "fake_floor_pull",
]

NORMAL_ACTIONS = [
    "kraken_btc_snapshot",
    "iceberg_buy_reload",
    "iceberg_sell_reload",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_ts(ts_str: str) -> datetime:
    """Parse an ISO-8601 timestamp string to a timezone-aware datetime."""
    if not ts_str:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        # Python 3.7+ handles most ISO formats with fromisoformat
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def load_jsonl(path: str) -> list:
    """Load a JSONL file, skipping malformed lines gracefully."""
    records = []
    if not os.path.exists(path):
        print(f"WARNING: File not found: {path}")
        return records
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"WARNING: Skipping malformed line {i} in {os.path.basename(path)}: {exc}")
    return records


def classify_event(action: str) -> str:
    """Classify a predatory event as 'toxic', 'normal', or 'unknown'."""
    if not action:
        return "unknown"
    action_lower = action.lower()
    for prefix in TOXIC_ACTIONS:
        if action_lower.startswith(prefix):
            return "toxic"
    for prefix in NORMAL_ACTIONS:
        if action_lower.startswith(prefix):
            return "normal"
    return "unknown"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def pair_trades(v2_events: list) -> list:
    """
    Pair open/close events into complete trades.

    Returns a list of dicts:
      {
        "entry_ts": datetime,
        "exit_ts": datetime,
        "entry_price": float,
        "exit_price": float,
        "deploy": float,
        "net_pnl": float,
        "hold_bars": int,
        "reason": str,
        "rsi_at_entry": float,
      }
    """
    trades = []
    pending_open = None

    for event in v2_events:
        action = event.get("action", "")
        ts = parse_ts(event.get("ts_utc", ""))

        if action == "open":
            pending_open = event
        elif action == "close" and pending_open is not None:
            entry_ts = parse_ts(pending_open.get("ts_utc", ""))
            exit_ts = ts

            trade = {
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "entry_price": pending_open.get("entry_price", 0.0),
                "exit_price": event.get("exit_price", 0.0),
                "deploy": pending_open.get("deploy", 0.0),
                "net_pnl": event.get("net", 0.0),
                "hold_bars": event.get("hold_bars", 0),
                "reason": event.get("reason", ""),
                "rsi_at_entry": pending_open.get("rsi_at_entry", 0.0),
            }
            trades.append(trade)
            pending_open = None  # reset for next pair

    return trades


def find_toxic_events_near_entry(trades: list, predatory_events: list) -> list:
    """
    For each trade, check if any toxic event occurred within the toxicity window
    of the entry timestamp.

    Returns the trades list augmented with toxicity metadata.
    """
    window = timedelta(minutes=TOXICITY_WINDOW_MINUTES)

    # Pre-filter to only toxic events, sorted by time for potential optimisation
    toxic_events = []
    for ev in predatory_events:
        if classify_event(ev.get("action", "")) == "toxic":
            toxic_events.append({
                "action": ev.get("action", ""),
                "ts": parse_ts(ev.get("ts_utc", "")),
                "product_id": ev.get("product_id", ""),
                "price": ev.get("price", None),
                "move_usd": ev.get("move_usd", None),
                "mag_level": ev.get("mag_level", None),
            })

    toxic_events.sort(key=lambda e: e["ts"])

    for trade in trades:
        entry_ts = trade["entry_ts"]
        window_start = entry_ts - window
        window_end = entry_ts + window

        nearby = []
        for tev in toxic_events:
            if window_start <= tev["ts"] <= window_end:
                nearby.append(tev)

        trade["has_toxic_nearby"] = len(nearby) > 0
        trade["toxic_events"] = nearby
        trade["toxic_count"] = len(nearby)
        trade["toxic_types"] = list(set(e["action"] for e in nearby))
        trade["toxic_closest_minutes"] = (
            min(
                abs((e["ts"] - entry_ts).total_seconds()) / 60.0
                for e in nearby
            ) if nearby else None
        )

    return trades


def compute_statistics(trades: list) -> dict:
    """Compute comparative statistics for toxic-exposed vs clean trades."""
    toxic_trades = [t for t in trades if t.get("has_toxic_nearby")]
    clean_trades = [t for t in trades if not t.get("has_toxic_nearby")]

    def stats(group: list, label: str) -> dict:
        if not group:
            return {
                "label": label,
                "count": 0,
                "win_count": 0,
                "win_rate_pct": 0.0,
                "avg_net_pnl": 0.0,
                "total_net_pnl": 0.0,
                "avg_hold_bars": 0.0,
                "avg_rsi_at_entry": 0.0,
            }
        wins = [t for t in group if t["net_pnl"] > 0]
        return {
            "label": label,
            "count": len(group),
            "win_count": len(wins),
            "win_rate_pct": round(len(wins) / len(group) * 100, 1),
            "avg_net_pnl": round(sum(t["net_pnl"] for t in group) / len(group), 4),
            "total_net_pnl": round(sum(t["net_pnl"] for t in group), 4),
            "avg_hold_bars": round(sum(t["hold_bars"] for t in group) / len(group), 1),
            "avg_rsi_at_entry": round(sum(t["rsi_at_entry"] for t in group) / len(group), 2),
        }

    toxic_stats = stats(toxic_trades, "toxic_window")
    clean_stats = stats(clean_trades, "clean")

    # Delta: toxic minus clean
    delta = {
        "win_rate_pp": round(toxic_stats["win_rate_pct"] - clean_stats["win_rate_pct"], 1),
        "avg_net_pnl": round(toxic_stats["avg_net_pnl"] - clean_stats["avg_net_pnl"], 4),
        "avg_hold_bars": round(toxic_stats["avg_hold_bars"] - clean_stats["avg_hold_bars"], 1),
    }

    # Toxic event type breakdown
    type_breakdown = {}
    for t in toxic_trades:
        for etype in t.get("toxic_types", []):
            type_breakdown.setdefault(etype, {"trades": 0, "wins": 0, "total_pnl": 0.0})
            type_breakdown[etype]["trades"] += 1
            if t["net_pnl"] > 0:
                type_breakdown[etype]["wins"] += 1
            type_breakdown[etype]["total_pnl"] += t["net_pnl"]

    for v in type_breakdown.values():
        v["win_rate_pct"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0.0
        v["avg_pnl"] = round(v["total_pnl"] / v["trades"], 4) if v["trades"] else 0.0

    return {
        "toxic": toxic_stats,
        "clean": clean_stats,
        "delta": delta,
        "toxic_type_breakdown": type_breakdown,
    }


def print_summary(trades: list, stats: dict, predatory_summary: dict):
    """Print a human-readable summary table to stdout."""
    t = stats["toxic"]
    c = stats["clean"]
    d = stats["delta"]

    print("=" * 72)
    print("  TOXICITY WINDOW CORRELATION  (+/- {} min from entry)".format(TOXICITY_WINDOW_MINUTES))
    print("=" * 72)
    print()
    print("  Data overview")
    print(f"    Total V2 trades paired      : {len(trades)}")
    print(f"    Total predatory events      : {predatory_summary['total']}")
    print(f"    Toxic events                : {predatory_summary['toxic']}")
    print(f"    Normal events               : {predatory_summary['normal']}")
    print(f"    Unknown events              : {predatory_summary['unknown']}")
    print()
    print(f"  {'':>30s}  {'Toxic window':>14s}  {'Clean':>10s}")
    print(f"  {'':>30s}  {'─' * 14}  {'─' * 10}")
    print(f"  {'Trades':>30s}  {t['count']:>14d}  {c['count']:>10d}")
    print(f"  {'Win rate':>30s}  {t['win_rate_pct']:>13.1f}%  {c['win_rate_pct']:>9.1f}%")
    print(f"  {'Avg net PnL (USD)':>30s}  {t['avg_net_pnl']:>+14.4f}  {c['avg_net_pnl']:>+10.4f}")
    print(f"  {'Total net PnL (USD)':>30s}  {t['total_net_pnl']:>+14.4f}  {c['total_net_pnl']:>+10.4f}")
    print(f"  {'Avg hold (bars)':>30s}  {t['avg_hold_bars']:>14.1f}  {c['avg_hold_bars']:>10.1f}")
    print()
    print(f"  Delta (toxic - clean)")
    print(f"    Win rate       : {d['win_rate_pp']:>+6.1f} pp")
    print(f"    Avg net PnL    : {d['avg_net_pnl']:>+8.4f} USD")
    print(f"    Avg hold bars  : {d['avg_hold_bars']:>+8.1f}")
    print()

    # Toxic type breakdown
    breakdown = stats.get("toxic_type_breakdown", {})
    if breakdown:
        print("  Toxic event type breakdown (among exposed trades)")
        print(f"  {'Type':<40s}  {'Trades':>6s}  {'WR%':>6s}  {'Avg PnL':>10s}")
        print(f"  {'─' * 40}  {'─' * 6}  {'─' * 6}  {'─' * 10}")
        for etype, v in sorted(breakdown.items(), key=lambda x: x[1]["trades"], reverse=True):
            print(f"  {etype:<40s}  {v['trades']:>6d}  {v['win_rate_pct']:>5.1f}%  {v['avg_pnl']:>+10.4f}")
        print()

    # Verdict
    if t["count"] == 0 or c["count"] == 0:
        print("  VERDICT: Insufficient data in one or both groups for comparison.")
    elif abs(d["win_rate_pp"]) >= 10:
        direction = "WORSE" if d["win_rate_pp"] < 0 else "BETTER"
        print(f"  VERDICT: Toxic exposure is associated with a {direction} win rate "
              f"by {abs(d['win_rate_pp']):.1f} pp. "
              + ("Consider adding a toxicity filter to the live runner." if d["win_rate_pp"] < 0 else ""))
    elif abs(d["avg_net_pnl"]) >= 1.0:
        direction = "more negative" if d["avg_net_pnl"] < 0 else "more positive"
        print(f"  VERDICT: Toxic exposure shifts avg PnL {direction} by "
              f"${abs(d['avg_net_pnl']):.4f}/trade.")
    else:
        print("  VERDICT: No strong toxicity signal detected at this window size. "
              "Try adjusting TOXICITY_WINDOW_MINUTES or collecting more data.")
    print()
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading V2 trade events ...")
    v2_events = load_jsonl(V2_EVENTS_PATH)
    print(f"  -> {len(v2_events)} events loaded from {os.path.basename(V2_EVENTS_PATH)}")

    print("Loading predatory shadow monitor events ...")
    predatory_events = load_jsonl(PREDATORY_EVENTS_PATH)
    print(f"  -> {len(predatory_events)} events loaded from {os.path.basename(PREDATORY_EVENTS_PATH)}")

    # Pair trades
    trades = pair_trades(v2_events)
    print(f"  -> {len(trades)} complete trade pairs formed")

    if not trades:
        print("ERROR: No complete trade pairs found. Nothing to correlate.")
        sys.exit(1)

    # Classify predatory events
    toxic_count = 0
    normal_count = 0
    unknown_count = 0
    for ev in predatory_events:
        cat = classify_event(ev.get("action", ""))
        if cat == "toxic":
            toxic_count += 1
        elif cat == "normal":
            normal_count += 1
        else:
            unknown_count += 1

    predatory_summary = {
        "total": len(predatory_events),
        "toxic": toxic_count,
        "normal": normal_count,
        "unknown": unknown_count,
    }

    print(f"  -> Toxic: {toxic_count}, Normal: {normal_count}, Unknown: {unknown_count}")

    # Correlate
    print(f"Checking for toxic events within +/- {TOXICITY_WINDOW_MINUTES} min of entry ...")
    trades = find_toxic_events_near_entry(trades, predatory_events)

    exposed = [t for t in trades if t["has_toxic_nearby"]]
    print(f"  -> {len(exposed)} / {len(trades)} trades had a nearby toxic event "
          f"({len(exposed) / len(trades) * 100:.1f}%)")

    # Statistics
    stats = compute_statistics(trades)

    # Build output report
    # Serialise datetime objects for JSON
    def serialise_trade(t: dict) -> dict:
        out = dict(t)
        out["entry_ts"] = out["entry_ts"].isoformat() if isinstance(out["entry_ts"], datetime) else str(out["entry_ts"])
        out["exit_ts"] = out["exit_ts"].isoformat() if isinstance(out["exit_ts"], datetime) else str(out["exit_ts"])
        out["toxic_events"] = [
            {
                "action": e["action"],
                "ts_utc": e["ts"].isoformat() if isinstance(e["ts"], datetime) else str(e["ts"]),
                "product_id": e["product_id"],
                "price": e["price"],
                "move_usd": e["move_usd"],
                "mag_level": e["mag_level"],
            }
            for e in out["toxic_events"]
        ]
        return out

    report = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "toxicity_window_minutes": TOXICITY_WINDOW_MINUTES,
            "v2_events_file": os.path.basename(V2_EVENTS_PATH),
            "predatory_events_file": os.path.basename(PREDATORY_EVENTS_PATH),
        },
        "predatory_summary": predatory_summary,
        "statistics": stats,
        "trades": [serialise_trade(t) for t in trades],
    }

    # Write report
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nReport written to: {OUTPUT_PATH}")
    print()

    # Print summary table
    print_summary(trades, stats, predatory_summary)


if __name__ == "__main__":
    main()
