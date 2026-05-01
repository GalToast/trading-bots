"""Hungry Hippo Weakening Detector -- Real-Time Trend Health Monitor.

Monitors each HH lane and outputs a 0-100 weakening score every N seconds.
Reads state files from the reports directory:
    penetration_lattice_shadow_<symbol>_<timeframe>_hungry_hippo_v1_state.json

Output: reports/hh_weakening_scores.json

The weakening score combines:
1. BUY/SELL ratio (weight 40%): In BUY-tight uptrend lattice, if
   sell_opens / (buy_opens + 1) > 0.5, trend is weakening.
2. Grid overload (weight 30%): If total_opens > 2 * max_open_per_side,
   grid is stacking on both sides.
3. Close rate decay (weight 30%): If closes_per_minute is declining over
   last 5 minutes, momentum is fading.

Usage:
    python scripts/hungry_hippo_weakening_detector.py --watch-dir reports --interval 60
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_FILE_PATTERN = "penetration_lattice_shadow_*_hungry_hippo_v1_state.json"

WEIGHT_BUY_SELL_RATIO = 0.40
WEIGHT_GRID_OVERLOAD = 0.30
WEIGHT_CLOSE_RATE_DECAY = 0.30


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _discover_state_files(watch_dir: Path) -> list[Path]:
    """Find all HH state files in the watch directory."""
    if not watch_dir.is_dir():
        return []
    return sorted(watch_dir.glob(STATE_FILE_PATTERN))


def _parse_lane_name(state_path: Path) -> str:
    """Extract lane symbol from state file name.

    E.g. penetration_lattice_shadow_nas100_m15_hungry_hippo_v1_state.json -> NAS100_HH
    """
    stem = state_path.stem  # e.g. penetration_lattice_shadow_nas100_m15_hungry_hippo_v1_state
    # Remove prefix and suffix
    prefix = "penetration_lattice_shadow_"
    suffix = "_hungry_hippo_v1_state"
    if stem.startswith(prefix):
        stem = stem[len(prefix):]
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    # stem is now like "nas100_m15" or "eurusd_m15"
    parts = stem.rsplit("_", 1)
    symbol = parts[0].upper() if parts else stem.upper()
    return f"{symbol}_HH"


def _read_state_file(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _compute_buy_sell_score(buy_opens: int, sell_opens: int) -> float:
    """Compute BUY/SELL ratio component (0-100).

    In a healthy BUY-tight uptrend lattice, buy_opens >> sell_opens.
    When sell_opens catches up, the ratio rises toward 1.0+ and the score worsens.

    score = min(100, (sell_opens / (buy_opens + 1)) * 100)
    """
    ratio = sell_opens / (buy_opens + 1)
    return min(100.0, ratio * 100.0)


def _compute_grid_overload_score(total_opens: int, max_open_per_side: int) -> float:
    """Compute grid overload component (0-100).

    If total_opens > 2 * max_open_per_side, the grid is overloaded.
    score = 0 if total <= max_open_per_side
    score = 100 if total >= 2 * max_open_per_side
    linear interpolation in between.
    """
    if max_open_per_side <= 0:
        return 0.0
    if total_opens <= max_open_per_side:
        return 0.0
    if total_opens >= 2 * max_open_per_side:
        return 100.0
    return ((total_opens - max_open_per_side) / max_open_per_side) * 100.0


def _compute_close_rate_decay(
    realized_closes: int,
    uptime_minutes: float,
    prev_cpm: float | None,
) -> tuple[float, float]:
    """Compute close rate decay component (0-100) and current closes_per_minute.

    If closes_per_minute is declining compared to the previous reading,
    momentum is fading.

    decay_score = max(0, (prev_cpm - current_cpm) / max(prev_cpm, 0.001)) * 100
    """
    if uptime_minutes <= 0:
        return 0.0, 0.0
    current_cpm = realized_closes / uptime_minutes
    if prev_cpm is None or prev_cpm <= 0:
        return 0.0, current_cpm
    decay = (prev_cpm - current_cpm) / max(prev_cpm, 0.001)
    decay_score = max(0.0, min(100.0, decay * 100.0))
    return decay_score, current_cpm


def _extract_alert(buy_opens: int, sell_opens: int, score: float, grid_overload: bool, buy_sell_ratio: float) -> str:
    """Generate a human-readable alert string."""
    if score >= 70:
        level = "CRITICAL" if score >= 85 else "HIGH"
        if buy_sell_ratio > 0.8:
            return f"{level} -- SELL side nearly matching BUY side; trend likely reversing"
        if grid_overload:
            return f"{level} -- Grid severely overloaded on both sides"
        return f"{level} -- Weakening score {score:.0f}"
    elif score >= 40:
        return f"MEDIUM -- BUY/SELL ratio {buy_sell_ratio:.2f}, monitor for reversal"
    else:
        if buy_sell_ratio < 0.3 and sell_opens <= 2:
            return "LOW -- healthy BUY-dominant grid"
        return f"LOW -- score {score:.0f}, grid stable"


def _process_lane(state: dict[str, Any], prev_cpm: float | None = None) -> dict[str, Any]:
    """Process a single lane state and return the weakening report."""
    symbol_data = state.get("symbols", {})
    if not symbol_data:
        return None

    # Get the first (and typically only) symbol's data
    sym_key = list(symbol_data.keys())[0]
    sym = symbol_data[sym_key]

    anchor = float(sym.get("anchor", 0.0))
    open_tickets = sym.get("open_tickets", [])
    realized_closes = int(sym.get("realized_closes", 0))
    max_open_per_side = int(state.get("metadata", {}).get("max_open_per_side", 12))

    # Count BUY vs SELL opens
    buy_opens = sum(1 for t in open_tickets if str(t.get("direction", "")).upper() == "BUY")
    sell_opens = sum(1 for t in open_tickets if str(t.get("direction", "")).upper() == "SELL")
    total_opens = len(open_tickets)

    # BUY/SELL ratio score
    buy_sell_ratio = sell_opens / (buy_opens + 1)
    bs_score = _compute_buy_sell_score(buy_opens, sell_opens)

    # Grid overload score
    grid_overload = total_opens > 2 * max_open_per_side
    go_score = _compute_grid_overload_score(total_opens, max_open_per_side)

    # Close rate decay
    runner = state.get("runner", {})
    started_at = runner.get("started_at")
    uptime_minutes = 0.0
    if started_at:
        try:
            start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            uptime_minutes = max(0.0, (datetime.now(timezone.utc) - start_dt).total_seconds() / 60.0)
        except Exception:
            pass

    decay_score, current_cpm = _compute_close_rate_decay(realized_closes, uptime_minutes, prev_cpm)

    # Composite score
    score = (
        WEIGHT_BUY_SELL_RATIO * bs_score
        + WEIGHT_GRID_OVERLOAD * go_score
        + WEIGHT_CLOSE_RATE_DECAY * decay_score
    )
    score = round(min(100.0, max(0.0, score)), 1)

    alert = _extract_alert(buy_opens, sell_opens, score, grid_overload, buy_sell_ratio)

    return {
        "weakening_score": score,
        "buy_opens": buy_opens,
        "sell_opens": sell_opens,
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "grid_overload": grid_overload,
        "total_opens": total_opens,
        "max_open_per_side": max_open_per_side,
        "close_rate_decay": round(decay_score / 100.0, 3),
        "closes_per_minute": round(current_cpm, 4) if current_cpm else 0.0,
        "realized_closes": realized_closes,
        "uptime_minutes": round(uptime_minutes, 1),
        "alert": alert,
        "anchor": anchor,
    }


def run_once(watch_dir: Path, output_path: Path | None = None, prev_scores: dict[str, float] | None = None) -> dict[str, Any]:
    """Scan all HH state files and produce a weakening report.

    Args:
        watch_dir: Directory containing state JSON files.
        output_path: Where to write the output JSON (optional).
        prev_scores: Previous close-rate scores for decay comparison.

    Returns:
        The full report dict.
    """
    watch = Path(watch_dir)
    state_files = _discover_state_files(watch)

    if prev_scores is None:
        prev_scores = {}

    lanes: dict[str, Any] = {}
    new_cpm_map: dict[str, float] = {}

    for sf in state_files:
        lane_name = _parse_lane_name(sf)
        state = _read_state_file(sf)
        if state is None:
            lanes[lane_name] = {"error": "failed to read state file"}
            continue

        prev_cpm = prev_scores.get(lane_name)
        result = _process_lane(state, prev_cpm=prev_cpm)
        if result is None:
            lanes[lane_name] = {"error": "no symbol data in state"}
            continue

        lanes[lane_name] = result
        if result.get("closes_per_minute"):
            new_cpm_map[lane_name] = result["closes_per_minute"]

    report = {
        "generated_at": _utc_now_iso(),
        "lanes": lanes,
        "summary": {
            "total_lanes": len(lanes),
            "critical_lanes": sum(1 for v in lanes.values() if isinstance(v, dict) and v.get("weakening_score", 0) >= 70),
            "healthy_lanes": sum(1 for v in lanes.values() if isinstance(v, dict) and v.get("weakening_score", 0) < 40),
        },
    }

    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=False)

    return report, new_cpm_map


def main() -> None:
    parser = argparse.ArgumentParser(description="Hungry Hippo Weakening Detector")
    parser.add_argument("--watch-dir", default="reports", help="Directory with HH state JSON files")
    parser.add_argument("--output", default=None, help="Output JSON path (default: <watch-dir>/hh_weakening_scores.json)")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between scans (default: 60)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    watch_dir = Path(args.watch_dir)
    output_path = Path(args.output) if args.output else watch_dir / "hh_weakening_scores.json"

    prev_cpm: dict[str, float] = {}

    if args.once:
        report, _ = run_once(watch_dir, output_path, prev_cpm)
        print(json.dumps(report, indent=2))
        return

    print(f"[HH Weakening Detector] Watching {watch_dir}, output -> {output_path}")
    print(f"[HH Weakening Detector] Interval: {args.interval}s")

    cycle = 0
    while True:
        try:
            cycle += 1
            report, new_cpm = run_once(watch_dir, output_path, prev_cpm)
            prev_cpm = new_cpm

            summary = report.get("summary", {})
            ts = report.get("generated_at", "?")
            print(f"[{ts}] Cycle {cycle}: {summary.get('total_lanes', 0)} lanes, "
                  f"{summary.get('critical_lanes', 0)} critical, "
                  f"{summary.get('healthy_lanes', 0)} healthy")

            # Print per-lane alerts
            for lane_name, lane_data in report.get("lanes", {}).items():
                if isinstance(lane_data, dict) and "weakening_score" in lane_data:
                    score = lane_data["weakening_score"]
                    alert = lane_data.get("alert", "")
                    print(f"  {lane_name}: score={score} -- {alert}")

        except KeyboardInterrupt:
            print("\n[HH Weakening Detector] Shutting down.")
            break
        except Exception as e:
            print(f"[HH Weakening Detector] Error in cycle {cycle}: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
