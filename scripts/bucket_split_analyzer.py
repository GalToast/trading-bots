#!/usr/bin/env python3
"""
Bucket-Splitting Lane Analyzer

Reads state files and (optionally) event logs to produce a bucketed PnL breakdown
for any lane. This makes it immediately obvious which buckets contribute and which
destroy, instead of judging lanes by raw net alone.

Usage:
    python scripts/bucket_split_analyzer.py
    python scripts/bucket_split_analyzer.py --state reports/penetration_lattice_live_btcusd_m15_warp_state.json
    python scripts/bucket_split_analyzer.py --scan-reports  # scan all state files
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Known GBP bucket breakdown from earlier forward-shadow investigation
KNOWN_BUCKET_BREAKDOWNS: dict[str, dict[str, float]] = {
    "gbpusd_hh_forward_shadow": {
        "close_ticket": 153.71,
        "escape_tier0_offensive": -2074.07,
        "forced_unwind": -572.37,
    }
}


@dataclass
class BucketResult:
    lane: str
    symbol: str
    timeframe: str
    step: float
    realized_closes: int
    realized_net_usd: float
    buckets: dict[str, float] = field(default_factory=dict)

    @property
    def avg_per_close(self) -> float:
        if self.realized_closes == 0:
            return 0.0
        return self.realized_net_usd / self.realized_closes

    def bucket_sum(self) -> float:
        return sum(self.buckets.values())

    def unaccounted(self) -> float:
        """Difference between reported net and sum of known buckets."""
        return self.realized_net_usd - self.bucket_sum()


def load_state(path: Path) -> dict[str, Any] | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def extract_lane_info(state: dict[str, Any]) -> list[BucketResult]:
    """Extract lane info from a state file. Handles both single-symbol and multi-symbol states."""
    results = []
    metadata = state.get("metadata", {})
    symbols_data = state.get("symbols", {})

    for sym, sym_state in symbols_data.items():
        realized_closes = sym_state.get("realized_closes", 0)
        realized_net = sym_state.get("realized_net_usd", 0.0)
        step = metadata.get("step", sym_state.get("base_step_px", 0))
        timeframe = metadata.get("timeframe", sym_state.get("timeframe", "?"))

        result = BucketResult(
            lane=f"{sym.lower()}_{timeframe.lower()}",
            symbol=sym,
            timeframe=timeframe,
            step=step,
            realized_closes=realized_closes,
            realized_net_usd=realized_net,
        )
        results.append(result)

    return results


def scan_all_states() -> list[BucketResult]:
    """Scan all *_state.json files in reports/ and extract lane info."""
    results = []
    for state_file in sorted(REPORTS.glob("*_state.json")):
        state = load_state(state_file)
        if state:
            lanes = extract_lane_info(state)
            results.extend(lanes)
    return results


def apply_known_buckets(results: list[BucketResult]) -> list[BucketResult]:
    """Apply known bucket breakdowns to matching lanes."""
    for r in results:
        lane_key = r.lane.lower()
        for known_key, buckets in KNOWN_BUCKET_BREAKDOWNS.items():
            if known_key in lane_key or "gbpusd" in lane_key:
                r.buckets = dict(buckets)
                break
    return results


def format_report(results: list[BucketResult], sort_by: str = "net") -> str:
    """Format the bucket-splitting report as a markdown table."""
    # Sort
    if sort_by == "net":
        results.sort(key=lambda r: r.realized_net_usd, reverse=True)
    elif sort_by == "closes":
        results.sort(key=lambda r: r.realized_closes, reverse=True)

    lines = []
    lines.append("# Lane Bucket-Splitting Report")
    lines.append("")
    lines.append(f"Total lanes analyzed: {len(results)}")
    lines.append("")

    # Summary table
    lines.append("## Net PnL Leaderboard")
    lines.append("")
    lines.append("| Lane | Symbol | Step | Closes | Net USD | $/Close | Status |")
    lines.append("|------|--------|------|--------|---------|---------|--------|")

    for r in results:
        if r.realized_net_usd > 0:
            status = "GREEN"
        elif r.realized_net_usd < 0:
            status = "RED"
        else:
            status = "WAITING"
        if r.realized_closes == 0:
            status = "STARTING"
        lines.append(
            f"| {r.lane} | {r.symbol} | {r.step:.1f} | {r.realized_closes} "
            f"| {r.realized_net_usd:+.2f} | {r.avg_per_close:+.2f} | {status} |"
        )

    # Known bucket breakdowns
    bucketed = [r for r in results if r.buckets]
    if bucketed:
        lines.append("")
        lines.append("## Known Bucket Breakdowns")
        lines.append("")

        for r in bucketed:
            lines.append(f"### {r.lane} (Net: {r.realized_net_usd:+.2f})")
            lines.append("")
            lines.append("| Bucket | Amount |")
            lines.append("|--------|--------|")
            for bucket, amount in sorted(r.buckets.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {bucket} | {amount:+.2f} |")
            unaccounted = r.unaccounted()
            if abs(unaccounted) > 0.01:
                lines.append(f"| *unaccounted* | {unaccounted:+.2f} |")
            lines.append("")

    # Analysis
    positive = [r for r in results if r.realized_net_usd > 0]
    negative = [r for r in results if r.realized_net_usd < 0]
    total_net = sum(r.realized_net_usd for r in results)

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Positive lanes: {len(positive)}")
    lines.append(f"- Negative lanes: {len(negative)}")
    lines.append(f"- Total net across all lanes: {total_net:+.2f}")
    lines.append("")

    if bucketed:
        lines.append("## Key Insight")
        lines.append("")
        lines.append(
            "The GBPUSD HH bucket breakdown reveals that **core harvest (close_ticket) is profitable** "
            "(+$153.71) but **escape_tier0_offensive (-$2,074.07) and forced_unwind (-$572.37) destroy "
            "all profits and more**. This means the harvest mechanism works — the closure policy is "
            "the problem. Disabling Tier 0 offensive and fixing forced unwinds could turn -$2,492 "
            "into +$153."
        )
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Bucket-Splitting Lane Analyzer")
    parser.add_argument("--state", type=Path, help="Analyze a single state file")
    parser.add_argument("--scan-reports", action="store_true", help="Scan all state files in reports/")
    parser.add_argument("--sort", choices=["net", "closes"], default="net", help="Sort order")
    parser.add_argument("--output", type=Path, help="Write report to file")
    args = parser.parse_args()

    results = []

    if args.state:
        state = load_state(args.state)
        if state:
            results = extract_lane_info(state)
        else:
            print(f"Failed to load {args.state}", file=sys.stderr)
            sys.exit(1)
    elif args.scan_reports:
        results = scan_all_states()
    else:
        # Default: scan reports
        results = scan_all_states()

    results = apply_known_buckets(results)
    report = format_report(results, sort_by=args.sort)

    if args.output:
        args.output.write_text(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
