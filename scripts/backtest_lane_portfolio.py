#!/usr/bin/env python3
"""Offline backtester for the USDJPY breakout lane portfolio.

Simulates each lane's exit logic against historical trades to produce
pre-live performance estimates. Run before wiring lanes into the live registry.

Usage: python scripts/backtest_lane_portfolio.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"

SYMBOL = "USDJPY"
SIGNAL = "breakout_hold_above_high"
MODE = "SNIPER"
REGIME = "PRICE"

LANES = {
    "usd_breakout_ctrl_a": {
        "lane_id": "usd_breakout_ctrl_a",
        "role": "control",
        "hypothesis": "baseline_trail_after_30s_confirmation",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": None,
        "exit_min_profit_floor_usd": 0.0,
    },
    "usd_breakout_exit_60_floor03": {
        "lane_id": "usd_breakout_exit_60_floor03",
        "role": "challenger",
        "hypothesis": "bank_lower_tf_winners_with_moderate_peak_retention",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": 0.60,
        "exit_min_profit_floor_usd": 0.03,
    },
    "usd_breakout_exit_75_floor03": {
        "lane_id": "usd_breakout_exit_75_floor03",
        "role": "challenger",
        "hypothesis": "aggressive_peak_capture_if_noise_does_not_break_it",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": 0.75,
        "exit_min_profit_floor_usd": 0.03,
    },
    "usd_breakout_exit_50_floor03": {
        "lane_id": "usd_breakout_exit_50_floor03",
        "role": "challenger",
        "hypothesis": "looser_peak_retention_may_preserve_runners",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": 0.50,
        "exit_min_profit_floor_usd": 0.03,
    },
    "usd_breakout_fast_trail_above_1_peak": {
        "lane_id": "usd_breakout_fast_trail_above_1_peak",
        "role": "challenger",
        "hypothesis": "tighten_large_peak_giveback_after_1_dollar_breakout",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": 0.60,
        "exit_min_profit_floor_usd": 0.03,
        "large_peak_threshold_usd": 1.00,
        "large_peak_retain_ratio": 0.80,
    },
    "usd_breakout_peak_gate_120s": {
        "lane_id": "usd_breakout_peak_gate_120s",
        "role": "challenger",
        "hypothesis": "flat_if_peak_never_reaches_15c_within_120s",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": None,
        "exit_min_profit_floor_usd": 0.0,
        "peak_gate_hold_seconds": 120.0,
        "peak_gate_min_peak_usd": 0.15,
    },
    "usd_breakout_adverse_tolerance_015": {
        "lane_id": "usd_breakout_adverse_tolerance_015",
        "role": "challenger",
        "hypothesis": "allow_more_early_adverse_noise_before_failure",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": None,
        "exit_min_profit_floor_usd": 0.0,
    },
    "usd_breakout_tiered_peak_capture": {
        "lane_id": "usd_breakout_tiered_peak_capture",
        "role": "challenger",
        "hypothesis": "match_giveback_to_peak_size_clusters",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": None,
        "exit_min_profit_floor_usd": 0.0,
        "tiered_peak_capture": (
            (0.10, 0.50),
            (0.30, 0.60),
            (1.00, 0.70),
            (float("inf"), 0.80),
        ),
    },
    "usd_breakout_time_decay_trail": {
        "lane_id": "usd_breakout_time_decay_trail",
        "role": "challenger",
        "hypothesis": "tighten_or_loosen_trail_by_time_since_first_green",
        "entry_holdoff_seconds": 30.0,
        "exit_retain_ratio": None,
        "exit_min_profit_floor_usd": 0.0,
        "time_decay_capture": (
            (60.0, 0.40),
            (180.0, 0.60),
            (float("inf"), 0.80),
        ),
    },
    "usd_breakout_entry_10_exit_baseline": {
        "lane_id": "usd_breakout_entry_10_exit_baseline",
        "role": "challenger",
        "hypothesis": "very_short_holdoff_may_capture_more_breakout_continuation",
        "entry_holdoff_seconds": 10.0,
        "exit_retain_ratio": None,
        "exit_min_profit_floor_usd": 0.0,
    },
}


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def simulate_lane(trade: dict, lane: dict) -> dict | None:
    """Simulate a lane's exit logic on a single trade.
    Returns the simulated exit dict, or None if the holdoff would have blocked it.
    """
    peak = float(trade.get("max_favorable_excursion_pnl", 0.0) or 0.0)
    realized = float(trade.get("realized_pnl", 0.0) or 0.0)
    hold = float(trade.get("hold_seconds", 0.0) or 0.0)
    first_green = trade.get("first_green_before_fail", False)
    exit_reason = str(trade.get("exit_reason", ""))
    adverse = float(trade.get("max_adverse_excursion_pnl", 0.0) or 0.0)

    # Simulate holdoff: if hold < holdoff and trade lost, it was blocked
    holdoff = lane["entry_holdoff_seconds"]
    if hold < holdoff and realized <= 0:
        return None  # holdoff would have blocked this trade entirely

    # If no first_green and trade lost, conservative: blocked
    if not first_green and realized <= 0:
        return None

    # Peak gate: close flat if peak < threshold within N seconds
    peak_gate_hold = lane.get("peak_gate_hold_seconds")
    peak_gate_min = lane.get("peak_gate_min_peak_usd")
    if peak_gate_hold is not None and peak_gate_min is not None:
        if hold >= float(peak_gate_hold) and peak < float(peak_gate_min) and realized <= 0:
            return None  # peak gate would have closed this early

    # Apply exit logic
    retain = lane["exit_retain_ratio"]
    floor = lane["exit_min_profit_floor_usd"]

    if retain is not None and peak > 0:
        scaled_peak = peak * retain
        simulated_exit = max(scaled_peak, floor) if scaled_peak > 0 else floor
        if simulated_exit > realized and simulated_exit <= peak:
            realized = simulated_exit
        elif simulated_exit > peak:
            realized = peak

    # Large peak variant: 80% retain when peak > $1.00
    large_thresh = lane.get("large_peak_threshold_usd")
    large_retain = lane.get("large_peak_retain_ratio")
    if large_thresh is not None and large_retain is not None and peak > float(large_thresh):
        scaled = peak * float(large_retain)
        sim = max(scaled, floor) if scaled > 0 else floor
        if sim > realized and sim <= peak:
            realized = sim
        elif sim > peak:
            realized = peak

    # Tiered peak capture
    tiers = lane.get("tiered_peak_capture")
    if tiers:
        ratio = None
        for peak_cap, candidate_ratio in tiers:
            if peak <= float(peak_cap):
                ratio = candidate_ratio
                break
        if ratio is None:
            ratio = tiers[-1][1]
        scaled = peak * float(ratio)
        sim = max(scaled, 0.03) if scaled > 0 else 0.03
        if sim > realized and sim <= peak:
            realized = sim
        elif sim > peak:
            realized = peak

    # Time decay trail
    decay = lane.get("time_decay_capture")
    if decay:
        ratio = None
        for time_cap, candidate_ratio in decay:
            if hold <= float(time_cap):
                ratio = candidate_ratio
                break
        if ratio is None:
            ratio = decay[-1][1]
        scaled = peak * float(ratio)
        sim = max(scaled, 0.03) if scaled > 0 else 0.03
        if sim > realized and sim <= peak:
            realized = sim
        elif sim > peak:
            realized = peak

    return {
        **trade,
        "simulated_pnl": realized,
        "original_pnl": float(trade.get("realized_pnl", 0.0) or 0.0),
        "lane_id": lane["lane_id"],
    }


def main():
    trades = [
        r for r in load_jsonl(TRADE_LOG)
        if str(r.get("symbol", "")).upper() == SYMBOL
        and str(r.get("entry_signal_type", "")) == SIGNAL
        and str(r.get("entry_mode", "")).upper() == MODE
        and str(r.get("regime_at_entry", "")).upper() == REGIME
    ]

    print(f"USDJPY breakout lane portfolio — backtest over {len(trades)} historical trades")
    print()

    results: dict[str, list[dict]] = {lid: [] for lid in LANES}

    for lane_id, lane in LANES.items():
        for trade in trades:
            sim = simulate_lane(trade, lane)
            if sim is not None:
                results[lane_id].append(sim)

    print(f"{'Lane':<35} {'Trades':>6} {'Blocked':>8} {'Net P/L':>10} {'Exp/Trade':>10} {'WR':>7} {'Avg GB':>8} {'Peak Cap':>10}")
    print("-" * 100)

    for lane_id, lane in LANES.items():
        sim_trades = results[lane_id]
        blocked = len(trades) - len(sim_trades)
        wins = [t for t in sim_trades if t["simulated_pnl"] > 0]
        losses = [t for t in sim_trades if t["simulated_pnl"] <= 0]
        net = sum(t["simulated_pnl"] for t in sim_trades)
        expectancy = net / len(sim_trades) if sim_trades else 0.0
        wr = len(wins) / len(sim_trades) * 100 if sim_trades else 0.0

        # Give-back for winners
        gbs = []
        for t in wins:
            peak = float(t.get("max_favorable_excursion_pnl", 0.0) or 0.0)
            pnl = t["simulated_pnl"]
            if peak > 0:
                gbs.append((peak - pnl) / peak * 100)
        avg_gb = sum(gbs) / len(gbs) if gbs else 0.0

        # Peak capture ratio
        peaks = [float(t.get("max_favorable_excursion_pnl", 0.0) or 0.0) for t in wins if float(t.get("max_favorable_excursion_pnl", 0.0) or 0.0) > 0]
        captured = [t["simulated_pnl"] for t in wins if float(t.get("max_favorable_excursion_pnl", 0.0) or 0.0) > 0]
        peak_cap = sum(captured) / sum(peaks) * 100 if peaks and sum(peaks) > 0 else 0.0

        role = lane["role"].upper()
        label = f"{role} {lane_id}"
        print(f"{label:<35} {len(sim_trades):>6} {blocked:>8} ${net:>9.2f} ${expectancy:>9.2f} {wr:>6.1f}% {avg_gb:>7.1f}% {peak_cap:>9.1f}%")

    print()
    print("Key insight: Compare challenger net P/L and peak capture % against control.")
    print("The lane with highest expectancy AND peak capture > control is the promotion candidate.")


if __name__ == "__main__":
    main()
