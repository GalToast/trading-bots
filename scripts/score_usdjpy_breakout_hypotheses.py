#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import mean, median


ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")


def load_lane_rows() -> list[dict]:
    rows: list[dict] = []
    if not TRADE_LOG.exists():
        return rows
    with TRADE_LOG.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                str(row.get("symbol", "")).upper(),
                str(row.get("entry_signal_type", "")),
                str(row.get("entry_mode", "")).upper(),
                str(row.get("regime_at_entry", "")).upper(),
            ) == LANE:
                rows.append(row)
    return rows


def pnl(row: dict) -> float:
    return float(row.get("realized_pnl", 0.0) or 0.0)


def peak(row: dict) -> float:
    return float(row.get("peak_pnl_before_exit", 0.0) or 0.0)


def fmt_money(value: float) -> str:
    return f"{value:+.2f}"


def summarize(rows: list[dict]) -> dict:
    realized = [pnl(row) for row in rows]
    wins = [value for value in realized if value > 0]
    losses = [value for value in realized if value < 0]
    captures = [pnl(row) / peak(row) for row in rows if peak(row) > 0]
    ttfg_values = [
        float(row.get("time_to_first_green_seconds"))
        for row in rows
        if row.get("time_to_first_green_seconds") is not None
    ]
    return {
        "trades": len(rows),
        "net": sum(realized),
        "wr": (len(wins) / len(rows) * 100.0) if rows else 0.0,
        "exp": (sum(realized) / len(rows)) if rows else 0.0,
        "avg_win": mean(wins) if wins else 0.0,
        "avg_loss": mean(losses) if losses else 0.0,
        "capture_mean": mean(captures) if captures else 0.0,
        "capture_median": median(captures) if captures else 0.0,
        "first_green_rate": (
            sum(1 for row in rows if row.get("first_green_before_fail")) / len(rows) * 100.0
            if rows
            else 0.0
        ),
        "avg_ttfg": mean(ttfg_values) if ttfg_values else 0.0,
        "exits": Counter(
            str(row.get("exit_reason", "UNKNOWN")).split("(")[0].strip() or "UNKNOWN"
            for row in rows
        ),
    }


def apply_exit_floor(rows: list[dict], floor: float) -> list[dict]:
    adjusted: list[dict] = []
    for row in rows:
        copy = dict(row)
        copy["realized_pnl"] = max(pnl(row), floor if peak(row) > 0 else pnl(row))
        adjusted.append(copy)
    return adjusted


def apply_peak_capture_floor(rows: list[dict], retain_ratio: float, min_floor: float = 0.0) -> list[dict]:
    adjusted: list[dict] = []
    for row in rows:
        copy = dict(row)
        target = max(min_floor, peak(row) * retain_ratio) if peak(row) > 0 else pnl(row)
        copy["realized_pnl"] = max(pnl(row), target)
        adjusted.append(copy)
    return adjusted


def filter_rows(rows: list[dict], name: str) -> list[dict]:
    if name == "baseline":
        return rows
    if name == "entry_gate_minus035_lt_30":
        return [
            row for row in rows
            if (row.get("time_to_minus_0_35_atr_seconds") is None)
            or float(row.get("time_to_minus_0_35_atr_seconds")) >= 30.0
        ]
    if name == "entry_gate_minus035_lt_45":
        return [
            row for row in rows
            if (row.get("time_to_minus_0_35_atr_seconds") is None)
            or float(row.get("time_to_minus_0_35_atr_seconds")) >= 45.0
        ]
    if name == "entry_gate_never_green":
        return [row for row in rows if row.get("time_to_first_green_seconds") is not None]
    raise ValueError(f"unknown filter {name}")


ENTRY_VARIANTS = {
    "baseline": {"description": "No entry filter", "live_feasible": True},
    "entry_gate_minus035_lt_30": {
        "description": "Skip trades that hit -0.35 ATR inside 30s",
        "live_feasible": True,
    },
    "entry_gate_minus035_lt_45": {
        "description": "Skip trades that hit -0.35 ATR inside 45s",
        "live_feasible": True,
    },
    "entry_gate_never_green": {
        "description": "Skip trades that never went green (upper-bound proxy)",
        "live_feasible": False,
    },
}


EXIT_VARIANTS = {
    "baseline": {
        "description": "Keep realized exits unchanged",
        "apply": lambda rows: rows,
        "live_feasible": True,
    },
    "floor_0_03": {
        "description": "Raise any green exit below +$0.03 to +$0.03",
        "apply": lambda rows: apply_exit_floor(rows, 0.03),
        "live_feasible": True,
    },
    "floor_0_05": {
        "description": "Raise any green exit below +$0.05 to +$0.05",
        "apply": lambda rows: apply_exit_floor(rows, 0.05),
        "live_feasible": True,
    },
    "retain_75pct": {
        "description": "Counterfactual retain at least 75% of peak",
        "apply": lambda rows: apply_peak_capture_floor(rows, 0.75),
        "live_feasible": True,
    },
    "retain_60pct": {
        "description": "Counterfactual retain at least 60% of peak",
        "apply": lambda rows: apply_peak_capture_floor(rows, 0.60),
        "live_feasible": True,
    },
    "retain_50pct": {
        "description": "Counterfactual retain at least 50% of peak",
        "apply": lambda rows: apply_peak_capture_floor(rows, 0.50),
        "live_feasible": True,
    },
    "retain_35pct": {
        "description": "Counterfactual retain at least 35% of peak",
        "apply": lambda rows: apply_peak_capture_floor(rows, 0.35),
        "live_feasible": True,
    },
    "retain_75pct_floor_0_03": {
        "description": "Counterfactual retain 75% of peak with +$0.03 floor",
        "apply": lambda rows: apply_peak_capture_floor(rows, 0.75, min_floor=0.03),
        "live_feasible": True,
    },
    "retain_50pct_floor_0_03": {
        "description": "Counterfactual retain 50% of peak with +$0.03 floor",
        "apply": lambda rows: apply_peak_capture_floor(rows, 0.50, min_floor=0.03),
        "live_feasible": True,
    },
}


def print_summary(label: str, data: dict) -> None:
    print(
        f"{label}: trades={data['trades']} wr={data['wr']:.1f}% "
        f"net={fmt_money(data['net'])} exp={fmt_money(data['exp'])} "
        f"fg={data['first_green_rate']:.1f}% avg_ttfg={data['avg_ttfg']:.1f}s "
        f"cap_mean={data['capture_mean']:.2f} cap_median={data['capture_median']:.2f}"
    )
    if data["exits"]:
        print("  exits: " + ", ".join(f"{k}={v}" for k, v in data["exits"].most_common(4)))


def print_ranked(label: str, ranked: list[tuple[float, float, str, str, dict]], limit: int = 10) -> None:
    print(label)
    for exp_value, net_value, entry_key, exit_key, stats in sorted(ranked, reverse=True)[:limit]:
        print(
            f"{entry_key} + {exit_key}: trades={stats['trades']} "
            f"net={fmt_money(net_value)} exp={fmt_money(exp_value)} "
            f"wr={stats['wr']:.1f}% fg={stats['first_green_rate']:.1f}% "
            f"cap_mean={stats['capture_mean']:.2f}"
        )


def main() -> None:
    rows = load_lane_rows()
    print(f"Lane: {'|'.join(LANE)}")
    print(f"Trade log: {TRADE_LOG}")
    print()
    print_summary("Baseline", summarize(rows))
    print()

    print("Entry Filters")
    for key, config in ENTRY_VARIANTS.items():
        filtered = filter_rows(rows, key)
        stats = summarize(filtered)
        print_summary(f"{key}", stats)
        print(f"  note: {config['description']}")
    print()

    print("Exit Variants (counterfactual on realized trade set)")
    for key, config in EXIT_VARIANTS.items():
        adjusted = config["apply"](list(rows))
        stats = summarize(adjusted)
        print_summary(f"{key}", stats)
        print(f"  note: {config['description']}")
    print()

    ranked: list[tuple[float, float, str, str, dict]] = []
    for entry_key in ENTRY_VARIANTS:
        filtered = filter_rows(rows, entry_key)
        for exit_key, config in EXIT_VARIANTS.items():
            adjusted = config["apply"](list(filtered))
            stats = summarize(adjusted)
            ranked.append((stats["exp"], stats["net"], entry_key, exit_key, stats))
    print_ranked("Combined Matrix", ranked)
    print()

    live_ranked = [
        row
        for row in ranked
        if ENTRY_VARIANTS[row[2]]["live_feasible"] and EXIT_VARIANTS[row[3]]["live_feasible"]
    ]
    print_ranked("Live-Feasible Matrix", live_ranked, limit=8)


if __name__ == "__main__":
    main()
