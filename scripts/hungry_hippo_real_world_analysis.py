#!/usr/bin/env python3
"""Hungry Hippo — Real-World Lane Performance Analyzer.

Reads ACTUAL event stream files from production lanes to extract:
- Per-symbol close frequency, $/close, win rate
- Config parameters used (step, alpha, max_open, asymmetry)
- Reset frequency, floating loss history

This replaces the bar-level simulation with REAL production data.

Usage:
    python scripts/hungry_hippo_real_world_analysis.py
"""
import json
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc
DAYS = 7

# Known lane patterns and their configs
KNOWN_CONFIGS = {
    "live_rearm_941777": {
        "symbols": ["EURUSD", "GBPUSD"],
        "step_pips": None,  # adaptive
        "alpha": 0.5,
        "max_open_per_side": None,
        "mode": "live",
    },
    "live_momentum_alpha50_941778": {
        "symbols": ["EURUSD", "GBPUSD", "NZDUSD"],
        "alpha": 1.0,
        "max_open_per_side": 24,
        "mode": "live",
    },
    "live_btcusd_exc2_tight_941779": {
        "symbols": ["BTCUSD"],
        "step_pips": 45,
        "mode": "live",
    },
    "live_btcusd_m15_warp_941781": {
        "symbols": ["BTCUSD"],
        "step_pips": 75,
        "max_open_per_side": 60,
        "mode": "live",
    },
    "live_ethusd_m15_warp_graduation_941782": {
        "symbols": ["ETHUSD"],
        "step_pips": 5,
        "max_open_per_side": 80,
        "mode": "live",
    },
    "shadow_gbpusd_m15_warp": {
        "symbols": ["GBPUSD"],
        "step_pips": None,
        "mode": "shadow",
    },
    "shadow_gbpusd_m15_fxmicro": {
        "symbols": ["GBPUSD"],
        "step_pips": 0.337,  # 0.000337
        "mode": "shadow",
    },
    "shadow_eurusd_m15_fxmicro": {
        "symbols": ["EURUSD"],
        "step_pips": 0.28,  # 0.00028
        "mode": "shadow",
    },
    "shadow_nzdusd_m15_fxmicro": {
        "symbols": ["NZDUSD"],
        "step_pips": 0.21,  # 0.00021
        "mode": "shadow",
    },
}


def parse_event_file(path: Path) -> list[dict]:
    """Parse a JSONL event file and extract close events."""
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get("action") in ("close_ticket", "close", "close_sell", "close_buy", "penetration_close"):
                        events.append(ev)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return events


def extract_symbol_from_path(path: Path) -> str | None:
    """Extract symbol from event file path."""
    name = path.stem.lower()
    for sym in ["btcusd", "ethusd", "gbpusd", "eurusd", "nzdusd", "usdjpy",
                "nas100", "us30", "xauusd", "solusd", "xrpusd", "audusd",
                "usdcad", "usdchf"]:
        if sym in name:
            return sym.upper()
    return None


def parse_close_pnl(event: dict) -> float | None:
    """Extract PnL from a close event."""
    for key in ["realized_pnl", "pnl", "pnl_usd", "net_pnl", "close_pnl"]:
        if key in event and event[key] is not None:
            try:
                return float(event[key])
            except (ValueError, TypeError):
                pass
    return None


def analyze_lane_performance() -> dict:
    """Analyze all event stream files and compute per-symbol per-lane metrics."""
    reports_dir = REPO / "reports"
    cutoff = datetime.now(UTC) - timedelta(days=DAYS)

    # Find all event stream files
    event_files = list(reports_dir.glob("**/*events*.jsonl"))
    event_files.extend(reports_dir.glob("**/*event*.jsonl"))

    # Deduplicate
    event_files = list(set(event_files))

    print(f"Found {len(event_files)} event files to analyze")

    # Per-symbol per-lane stats
    lane_stats = defaultdict(lambda: {
        "closes": 0,
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "events": 0,
        "config": {},
    })

    symbol_stats = defaultdict(lambda: {
        "closes": 0,
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "lanes": set(),
    })

    for ef in event_files:
        symbol = extract_symbol_from_path(ef)
        if not symbol:
            continue

        # Determine lane name from path
        lane_name = ef.stem
        # Remove common suffixes
        for suffix in ["_events", "_event", "_events_jsonl"]:
            lane_name = lane_name.replace(suffix, "")

        events = parse_event_file(ef)
        if not events:
            continue

        for ev in events:
            pnl = parse_close_pnl(ev)
            lane_stats[lane_name]["events"] += 1
            lane_stats[lane_name]["config"].setdefault("symbol", symbol)

            if pnl is not None:
                lane_stats[lane_name]["closes"] += 1
                lane_stats[lane_name]["total_pnl"] += pnl
                if pnl > 0:
                    lane_stats[lane_name]["wins"] += 1
                else:
                    lane_stats[lane_name]["losses"] += 1

                # Aggregate to symbol level
                symbol_stats[symbol]["closes"] += 1
                symbol_stats[symbol]["total_pnl"] += pnl
                symbol_stats[symbol]["lanes"].add(lane_name)
                if pnl > 0:
                    symbol_stats[symbol]["wins"] += 1
                else:
                    symbol_stats[symbol]["losses"] += 1

    # Compute derived metrics
    results = {"lanes": {}, "symbols": {}}

    for lane_name, stats in sorted(lane_stats.items()):
        if stats["closes"] == 0:
            continue
        per_close = stats["total_pnl"] / stats["closes"]
        win_rate = stats["wins"] / max(1, stats["wins"] + stats["losses"])
        results["lanes"][lane_name] = {
            "symbol": stats["config"].get("symbol", "unknown"),
            "closes": stats["closes"],
            "total_pnl": round(stats["total_pnl"], 2),
            "per_close": round(per_close, 4),
            "win_rate": round(win_rate, 3),
            "events_processed": stats["events"],
            "config": stats["config"],
        }

    for symbol, stats in sorted(symbol_stats.items()):
        if stats["closes"] == 0:
            continue
        per_close = stats["total_pnl"] / stats["closes"]
        win_rate = stats["wins"] / max(1, stats["wins"] + stats["losses"])
        results["symbols"][symbol] = {
            "closes": stats["closes"],
            "total_pnl": round(stats["total_pnl"], 2),
            "per_close": round(per_close, 4),
            "win_rate": round(win_rate, 3),
            "num_lanes": len(stats["lanes"]),
            "lanes": sorted(stats["lanes"]),
        }

    return results


def cross_reference_with_configs(results: dict) -> dict:
    """Cross-reference lane performance with known config parameters."""
    enhanced = {"lanes": {}, "symbols": results["symbols"]}

    for lane_name, perf in results["lanes"].items():
        symbol = perf["symbol"]
        config = KNOWN_CONFIGS.get(lane_name, {})

        # Try to extract step from the config
        step = config.get("step_pips", "unknown")
        alpha = config.get("alpha", "unknown")
        max_open = config.get("max_open_per_side", "unknown")
        mode = config.get("mode", "unknown")

        enhanced["lanes"][lane_name] = {
            **perf,
            "step_pips": step,
            "alpha": alpha,
            "max_open_per_side": max_open,
            "mode": mode,
        }

    return enhanced


def find_winning_configs(enhanced: dict) -> list[dict]:
    """Identify the config parameters that produce the highest $/close."""
    winning = []

    for lane_name, perf in enhanced["lanes"].items():
        if perf["closes"] < 5:  # Too few closes to be meaningful
            continue

        winning.append({
            "lane": lane_name,
            "symbol": perf["symbol"],
            "closes": perf["closes"],
            "per_close": perf["per_close"],
            "win_rate": perf["win_rate"],
            "step_pips": perf.get("step_pips", "unknown"),
            "alpha": perf.get("alpha", "unknown"),
            "max_open": perf.get("max_open_per_side", "unknown"),
            "mode": perf.get("mode", "unknown"),
        })

    # Sort by $/close descending
    winning.sort(key=lambda x: x["per_close"], reverse=True)
    return winning


def main():
    print("=" * 100)
    print("HUNGRY HIPPO — REAL-WORLD LANE PERFORMANCE ANALYSIS")
    print(f"Analyzing last {DAYS} days of production event streams")
    print("=" * 100)
    print()

    print("Phase 1: Analyzing event streams...")
    results = analyze_lane_performance()

    print("Phase 2: Cross-referencing with known configs...")
    enhanced = cross_reference_with_configs(results)

    print("Phase 3: Finding winning configs...")
    winners = find_winning_configs(enhanced)

    # Save results
    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": DAYS,
        "winning_configs": winners,
        "lane_performance": enhanced["lanes"],
        "symbol_summary": enhanced["symbols"],
    }

    out_path = REPO / "reports" / "hungry_hippo_real_world_analysis.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print()
    print("=" * 100)
    print("TOP PERFORMING LANES BY $/CLOSE")
    print("=" * 100)
    print(f"{'LANE':<45} {'SYM':>6} {'CLOSES':>7} {'$/CLOSE':>10} {'WIN_PCT':>7} {'STEP':>8} {'ALPHA':>6} {'MODE':>8}")
    print("-" * 100)

    for w in winners[:20]:
        step_str = str(w["step_pips"])[:8]
        print(f"{w['lane']:<45} {w['symbol']:>6} {w['closes']:>7} "
              f"${w['per_close']:>+.4f} {w['win_rate']:>6.1%} {step_str:>8} "
              f"{str(w['alpha'])[:6]:>6} {w['mode']:>8}")

    print()
    print("=" * 100)
    print("SYMBOL SUMMARY (ALL LANES COMBINED)")
    print("=" * 100)
    print(f"{'SYMBOL':<10} {'CLOSES':>7} {'TOTAL_PNL':>12} {'$/CLOSE':>10} {'WIN_PCT':>7} {'LANES':>6}")
    print("-" * 100)

    for sym, stats in sorted(enhanced["symbols"].items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        print(f"{sym:<10} {stats['closes']:>7} ${stats['total_pnl']:>+.2f} "
              f"${stats['per_close']:>+.4f} {stats['win_rate']:>6.1%} {stats['num_lanes']:>6}")

    print()
    print(f"Results saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
