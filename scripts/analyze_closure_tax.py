#!/usr/bin/env python3
"""
Closure Tax / Bucket-Split Analysis

Diagnoses WHY lanes go negative even when close signals are good.
Categorizes each exit into buckets:
  - harvest: normal TP close (close_ticket action)
  - offensive: escape_tier{0,1,2,3}_offensive (escape-driven exits)
  - forced_unwind: forced_unwind (full lattice kill/reset)
  - other: anything else with realized_pnl

Produces a clear table showing which bucket dominates the PnL.

Usage:
    python scripts/analyze_closure_tax.py reports/shadow_gbpusd_tick_forward_events.jsonl
    python scripts/analyze_closure_tax.py reports/penetration_lattice_live_btcusd_m15_warp_events.jsonl
    python scripts/analyze_closure_tax.py --all   # analyze all known event logs
"""
import json
import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Bucket classification rules
ESCAPE_PATTERN = re.compile(r"^escape_tier[0-3]_offensive$")


def classify_action(action: str) -> str:
    if action == "close_ticket":
        return "harvest"
    elif ESCAPE_PATTERN.match(action):
        return "offensive"
    elif action == "forced_unwind":
        return "forced_unwind"
    else:
        return "other"


def analyze_event_log(filepath: str):
    """Read a JSONL event log and bucket-split the PnL."""
    path = Path(filepath)
    if not path.exists():
        print(f"ERROR: File not found: {filepath}", flush=True)
        return None

    buckets = {
        "harvest": {"count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "pnls": []},
        "offensive": {"count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "pnls": []},
        "forced_unwind": {"count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "pnls": []},
        "other": {"count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "pnls": []},
    }

    total_events = 0
    parse_errors = 0
    first_ts = None
    last_ts = None
    symbol = None
    mode = None

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_events += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            action = event.get("action", "unknown")
            realized_pnl = event.get("realized_pnl")
            ts = event.get("ts_utc")

            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if event.get("symbol") and symbol is None:
                symbol = event["symbol"]
            if event.get("mode") and mode is None:
                mode = event["mode"]

            # Only bucket events with realized PnL
            if realized_pnl is None:
                continue

            bucket = classify_action(action)
            buckets[bucket]["count"] += 1
            buckets[bucket]["net_pnl"] += realized_pnl
            buckets[bucket]["pnls"].append(realized_pnl)
            if realized_pnl >= 0:
                buckets[bucket]["wins"] += 1
            else:
                buckets[bucket]["losses"] += 1

    # Compute derived stats
    for bucket_name, b in buckets.items():
        pnls = b["pnls"]
        b["avg_pnl"] = sum(pnls) / len(pnls) if pnls else 0.0
        b["win_rate"] = b["wins"] / max(b["count"], 1) * 100
        b["min_pnl"] = min(pnls) if pnls else 0.0
        b["max_pnl"] = max(pnls) if pnls else 0.0
        b["median_pnl"] = sorted(pnls)[len(pnls) // 2] if pnls else 0.0
        del b["pnls"]  # drop raw list to keep output clean

    total_net = sum(b["net_pnl"] for b in buckets.values())
    total_closes = sum(b["count"] for b in buckets.values())

    for bucket_name, b in buckets.items():
        b["pct_of_net"] = (b["net_pnl"] / total_net * 100) if total_net != 0 else 0.0
        b["pct_of_closes"] = (b["count"] / max(total_closes, 1) * 100)

    return {
        "file": str(path.name),
        "file_path": str(path),
        "symbol": symbol or "unknown",
        "mode": mode or "unknown",
        "total_events": total_events,
        "parse_errors": parse_errors,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "total_closes": total_closes,
        "total_net_pnl": total_net,
        "buckets": buckets,
    }


def format_report(analysis: dict) -> str:
    lines = []
    lines.append("# Closure Tax / Bucket-Split Analysis")
    lines.append("")
    lines.append(f"**File:** `{analysis['file']}`")
    lines.append(f"**Symbol:** {analysis['symbol']}")
    lines.append(f"**Mode:** {analysis['mode']}")
    lines.append(f"**Period:** {analysis['first_ts']} to {analysis['last_ts']}")
    lines.append(f"**Total events scanned:** {analysis['total_events']:,}")
    lines.append(f"**Parse errors:** {analysis['parse_errors']}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"**Total realized closes:** {analysis['total_closes']:,}")
    lines.append(f"**Total net PnL:** ${analysis['total_net_pnl']:+.2f}")
    lines.append("")

    lines.append("## Bucket Breakdown")
    lines.append("")
    lines.append("| Bucket | Count | % of Closes | Net PnL | % of Net | Avg PnL | Win Rate | Min | Max | Median |")
    lines.append("|--------|------:|------------:|--------:|---------:|--------:|---------:|----:|----:|-------:|")

    for bucket_name in ["harvest", "offensive", "forced_unwind", "other"]:
        b = analysis["buckets"][bucket_name]
        lines.append(
            f"| {bucket_name} "
            f"| {b['count']:,} "
            f"| {b['pct_of_closes']:.1f}% "
            f"| ${b['net_pnl']:+.2f} "
            f"| {b['pct_of_net']:+.1f}% "
            f"| ${b['avg_pnl']:+.4f} "
            f"| {b['win_rate']:.1f}% "
            f"| ${b['min_pnl']:+.2f} "
            f"| ${b['max_pnl']:+.2f} "
            f"| ${b['median_pnl']:+.2f} |"
        )

    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")

    harvest = analysis["buckets"]["harvest"]
    offensive = analysis["buckets"]["offensive"]
    forced = analysis["buckets"]["forced_unwind"]

    if harvest["count"] == 0 and offensive["count"] == 0 and forced["count"] == 0:
        lines.append("- No realized close events found in this log.")
    else:
        # Signal quality assessment
        if harvest["net_pnl"] > 0 and harvest["win_rate"] >= 50:
            lines.append(f"- **Signal quality: GOOD** — Harvest bucket is profitable (${harvest['net_pnl']:+.2f}, {harvest['win_rate']:.0f}% WR, {harvest['count']} closes)")
        elif harvest["count"] > 0:
            lines.append(f"- **Signal quality: DEGRADED** — Harvest bucket losing money (${harvest['net_pnl']:+.2f}, {harvest['win_rate']:.0f}% WR)")
        else:
            lines.append("- **Signal quality: NO DATA** — No harvest closes to evaluate.")

        # Closure tax assessment
        closure_tax = offensive["net_pnl"] + forced["net_pnl"]
        if closure_tax < 0:
            lines.append(f"- **Closure tax: SEVERE** — Offensive + Forced unwind cost ${closure_tax:+.2f}")
            if offensive["count"] > 0:
                lines.append(f"  - Offensive exits: {offensive['count']} closes, ${offensive['net_pnl']:+.2f} (${offensive['avg_pnl']:+.4f}/close), {offensive['win_rate']:.0f}% WR")
            if forced["count"] > 0:
                lines.append(f"  - Forced unwinds: {forced['count']} closes, ${forced['net_pnl']:+.2f} (${forced['avg_pnl']:+.4f}/close), {forced['win_rate']:.0f}% WR")
        elif closure_tax > 0:
            lines.append(f"- **Closure tax: NONE** — Escape/forced exits net positive (${closure_tax:+.2f})")
        else:
            lines.append(f"- **Closure tax: NEUTRAL** — Escape/forced exits break even")

        # Net driver
        if harvest["net_pnl"] > 0 and (offensive["net_pnl"] + forced["net_pnl"]) < 0:
            net_drag = abs(offensive["net_pnl"] + forced["net_pnl"])
            if net_drag > harvest["net_pnl"]:
                lines.append(f"- **Net driver: CLOSURE TAX dominates** — Drag of ${net_drag:+.2f} exceeds harvest profit of ${harvest['net_pnl']:+.2f}")
                lines.append(f"  - Without closure tax, this lane would be +${harvest['net_pnl']:+.2f}")
            else:
                lines.append(f"- **Net driver: HARVEST survives tax** — Harvest ${harvest['net_pnl']:+.2f} > closure drag ${net_drag:+.2f}")
        elif harvest["net_pnl"] < 0:
            lines.append(f"- **Net driver: SIGNAL itself is losing** — Harvest bucket is negative (${harvest['net_pnl']:+.2f})")

    lines.append("")
    lines.append("## Classification Rules")
    lines.append("")
    lines.append("- `harvest`: `close_ticket` — normal take-profit closes")
    lines.append("- `offensive`: `escape_tier{0,1,2,3}_offensive` — escape-driven exits (risk mitigation)")
    lines.append("- `forced_unwind`: `forced_unwind` — full lattice kill/reset (maximum pain)")
    lines.append("- `other`: any other action with `realized_pnl`")
    lines.append("")

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Closure Tax / Bucket-Split Analysis")
    parser.add_argument("files", nargs="*", help="Event log JSONL files to analyze")
    parser.add_argument("--all", action="store_true", help="Analyze all known event logs")
    parser.add_argument("--output", "-o", help="Write report to file")
    args = parser.parse_args()

    files = list(args.files)

    if args.all:
        reports_dir = ROOT / "reports"
        if reports_dir.exists():
            for f in sorted(reports_dir.glob("*_events.jsonl")):
                files.append(str(f))

    if not files:
        print("Usage: python scripts/analyze_closure_tax.py <event_log.jsonl> [--all]", flush=True)
        print("  or:  python scripts/analyze_closure_tax.py --all", flush=True)
        sys.exit(1)

    all_reports = []

    for filepath in files:
        print(f"\nAnalyzing: {filepath}", flush=True)
        analysis = analyze_event_log(filepath)
        if analysis is None:
            continue

        report = format_report(analysis)
        all_reports.append(report)

        # Print to stdout
        print(report, flush=True)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = ROOT / "reports" / "closure_tax_bucket_analysis.md"

    with open(output_path, "w") as f:
        f.write("# Closure Tax / Bucket-Split Analysis\n\n")
        f.write(f"*Generated: {datetime.now(timezone.utc).isoformat()}*\n\n")
        for i, report in enumerate(all_reports):
            if i > 0:
                f.write("\n---\n\n")
            f.write(report)
            f.write("\n")

    print(f"\nReport written to: {output_path}", flush=True)


if __name__ == "__main__":
    main()
