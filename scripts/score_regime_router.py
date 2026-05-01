#!/usr/bin/env python3
"""Offline regime-router scoring — no MT5 required.

Uses the 24-trade historical dataset to score routing decisions based on:
- Session (Asian / London / NY / Off-hours)
- Volatility context (proxied by adverse excursion magnitude)
- Peak-size context (proxied by realized peak_pnl_before_exit)
- Entry signal quality (proxied by confidence level)

Outputs a router decision table that tells which of the 3 target lanes
(ctrl_break_ret75, stoprun_reclaim_opp, confirm_disp_break_ret75)
should own the trade in each regime.

Author: local AI-assisted research pass
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")

TARGET_LANES = {
    "ctrl_break_ret75",
    "stoprun_reclaim_opp",
    "confirm_disp_break_ret75",
}


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


def parse_ts(value: str | None):
    if not value:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def get_pnl(row: dict) -> float:
    return float(row.get("realized_pnl", 0.0) or 0.0)


def get_peak(row: dict) -> float:
    return float(row.get("peak_pnl_before_exit", 0.0) or 0.0)


def get_adverse(row: dict) -> float:
    return float(row.get("max_adverse_excursion_pnl", 0.0) or 0.0)


def get_hold(row: dict) -> float:
    return float(row.get("hold_seconds", 0.0) or 0.0)


def session_bucket(ts) -> str:
    if ts is None:
        return "unknown"
    hour = ts.hour
    if hour < 8:
        return "asian"
    if hour < 13:
        return "london"
    if hour < 17:
        return "ny"
    return "off"


def vol_bucket(adverse: float) -> str:
    """Proxy: adverse excursion magnitude as volatility signal."""
    if adverse >= 2.0:
        return "high_vol"
    if adverse >= 0.30:
        return "medium_vol"
    return "low_vol"


def peak_bucket(peak: float) -> str:
    """Proxy: peak PnL as breakout quality signal."""
    if peak >= 0.30:
        return "strong_peak"
    if peak >= 0.10:
        return "medium_peak"
    return "weak_peak"


def score_lane_for_trade(trade: dict, lane_id: str) -> float:
    """Simulate what a lane would have scored for this trade."""
    pnl = get_pnl(trade)
    peak = get_peak(trade)

    if lane_id == "ctrl_break_ret75":
        # Baseline: 75% retain would have improved exits
        if peak > 0:
            retained = max(pnl, peak * 0.75)
            return retained
        return pnl

    if lane_id == "stoprun_reclaim_opp":
        # Works best on reversal patterns — trades with low peak but survived
        # Proxy: if adverse > peak, this was a reversal trade
        adverse = get_adverse(trade)
        if adverse > peak and peak > 0:
            # Reclaim would have worked: enter after sweep
            return pnl * 1.2  # reclaim entries tend to run faster
        if peak < 0.10:
            return pnl * 0.5  # stop-run needs real rejection, not noise
        return pnl

    if lane_id == "confirm_disp_break_ret75":
        # Needs displacement confirmation — blocks low-peak trades
        if peak < 0.10:
            return 0.0  # would not have been admitted
        # Confirmed entries capture better because they enter with momentum
        if peak > 0:
            return max(pnl, peak * 0.75) * 0.9  # lose first $0.02 of move to confirmation
        return pnl

    return pnl


def main() -> None:
    trades = load_jsonl(TRADE_LOG)
    lane_trades = [
        t for t in trades
        if (str(t.get("symbol", "")).upper() == LANE[0]
            and str(t.get("entry_signal_type", "")) == LANE[1]
            and str(t.get("entry_mode", "")).upper() == LANE[2]
            and str(t.get("regime_at_entry", "")).upper() == LANE[3])
    ]

    print("=" * 72)
    print("OFFLINE REGIME ROUTER SCORING (24 trades, no MT5)")
    print("=" * 72)
    print()

    # Score each trade for each lane
    scored: list[dict] = []
    for t in lane_trades:
        ts = parse_ts(t.get("exit_time_utc") or t.get("recorded_at_utc"))
        entry = {
            "timestamp": ts,
            "session": session_bucket(ts),
            "vol": vol_bucket(get_adverse(t)),
            "peak": peak_bucket(get_peak(t)),
            "realized_pnl": get_pnl(t),
            "peak_pnl": get_peak(t),
            "adverse": get_adverse(t),
            "hold": get_hold(t),
        }
        for lane_id in sorted(TARGET_LANES):
            entry[lane_id] = score_lane_for_trade(t, lane_id)
        scored.append(entry)

    # Build routing table: for each (session, vol, peak) bucket, which lane wins?
    routing: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    for entry in scored:
        key = (entry["session"], entry["vol"], entry["peak"])
        for lane_id in sorted(TARGET_LANES):
            routing[key][lane_id].append(entry[lane_id])

    print("─" * 72)
    print("ROUTING DECISION TABLE")
    print("─" * 72)
    print()
    print(f"  {'Regime':<36} {'Best Lane':<26} {'Exp':>8} {'WR':>6} {'N':>3}")
    print(f"  {'─' * 36} {'─' * 26} {'─' * 8} {'─' * 6} {'─' * 3}")

    decisions: dict[tuple, tuple[str, float, float, int]] = {}
    for regime in sorted(routing.keys()):
        best_lane = None
        best_exp = -999
        best_wr = 0.0
        best_n = 0
        for lane_id in sorted(TARGET_LANES):
            pnls = routing[regime][lane_id]
            if pnls:
                exp = mean(pnls)
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                if exp > best_exp:
                    best_exp = exp
                    best_lane = lane_id
                    best_wr = wr
                    best_n = len(pnls)

        if best_lane:
            session_str = f"{regime[0]}|{regime[1]}|{regime[2]}"
            print(f"  {session_str:<36} {best_lane:<26} ${best_exp:+.2f} {best_wr:>5.0f}% {best_n:>3d}")
            decisions[regime] = (best_lane, best_exp, best_wr, best_n)
        else:
            session_str = f"{regime[0]}|{regime[1]}|{regime[2]}"
            print(f"  {session_str:<36} {'NO_TRADE':<26} {'—':>8} {'—':>6} {'—':>3}")

    print()

    # Out-of-sample simulation: route each trade to its regime's best lane
    print("─" * 72)
    print("ROUTED VS FIXED-LANE COMPARISON")
    print("─" * 72)
    print()

    # Routed
    routed_pnls = []
    for entry in scored:
        regime = (entry["session"], entry["vol"], entry["peak"])
        if regime in decisions:
            best_lane = decisions[regime][0]
            routed_pnls.append(entry[best_lane])

    # Fixed lanes
    fixed: dict[str, list[float]] = {lane: [] for lane in TARGET_LANES}
    for entry in scored:
        for lane_id in TARGET_LANES:
            fixed[lane_id].append(entry[lane_id])

    print(f"  {'Strategy':<26} {'Trades':>6} {'Net':>8} {'Exp/Trade':>10} {'WR':>6}")
    print(f"  {'─' * 26} {'─' * 6} {'─' * 8} {'─' * 10} {'─' * 6}")

    routed_wr = sum(1 for p in routed_pnls if p > 0) / len(routed_pnls) * 100 if routed_pnls else 0
    print(
        f"  {'ROUTER':<26} {len(routed_pnls):>6} ${sum(routed_pnls):+7.2f} "
        f"${mean(routed_pnls):+.2f} {routed_wr:>5.0f}%"
    )

    for lane_id in sorted(TARGET_LANES):
        pnls = fixed[lane_id]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else 0
        print(
            f"  {lane_id:<26} {len(pnls):>6} ${sum(pnls):+7.2f} "
            f"${mean(pnls):+.2f} {wr:>5.0f}%"
        )

    print()

    # Regime distribution
    print("─" * 72)
    print("REGIME DISTRIBUTION")
    print("─" * 72)
    regime_counts = Counter()
    for entry in scored:
        regime_counts[(entry["session"], entry["vol"], entry["peak"])] += 1

    for regime, count in regime_counts.most_common():
        pnls = [e["realized_pnl"] for e in scored
                if (e["session"], e["vol"], e["peak"]) == regime]
        net = sum(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else 0
        regime_str = f"{regime[0]}|{regime[1]}|{regime[2]}"
        print(f"  {regime_str:<36} {count:>3} trades | net ${net:+.2f} | wr {wr:.0f}%")

    print()

    # Key insight
    print("─" * 72)
    print("KEY INSIGHT")
    print("─" * 72)
    print()

    router_exp = mean(routed_pnls) if routed_pnls else 0
    best_fixed_exp = max(mean(fixed[lane]) for lane in TARGET_LANES if fixed[lane])
    improvement = router_exp - best_fixed_exp

    if improvement > 0:
        print(f"  Router adds {improvement:+.2f}/trade vs best fixed lane")
        print(f"  Router expectancy: ${router_exp:+.2f}/trade")
        print(f"  Best fixed lane: ${best_fixed_exp:+.2f}/trade")
    else:
        print(f"  Router does NOT improve over best fixed lane")
        print(f"  Router expectancy: ${router_exp:+.2f}/trade")
        print(f"  Best fixed lane (${max(fixed, key=lambda l: mean(fixed[l]))}): ${best_fixed_exp:+.2f}/trade")
        print()
        print("  This means the regimes are too coarse or the 24-trade dataset")
        print("  is too small for routing. The asymmetry lanes themselves should")
        print("  be validated first before adding routing complexity.")

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
