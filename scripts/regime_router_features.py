#!/usr/bin/env python3
"""Regime-router feature extractor for USDJPY breakout SNIPER.

Produces a per-trade feature matrix that a regime router can use to
decide which strategy architecture should own the next trade.

Features extracted:
1. Volatility expansion: ATR vs 20-bar rolling avg ATR
2. Range compression: (high-low)/ATR over last N bars at entry
3. Session band: UTC hour classification
4. Spread quality: spread_at_entry vs median spread
5. Recent win rate: last 5 trades WR at time of entry
6. Adverse excursion profile: MAE as % of ATR at entry
7. Entry context: breakout_continuation vs other

Usage: python scripts/regime_router_features.py [--symbol USDJPY]
Output: CSV to stdout + saves to reports/regime_features.csv
"""
from __future__ import annotations

import argparse
import json
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LOCAL_TZ = ZoneInfo("America/Chicago")


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


def classify_session(utc_hour: int) -> str:
    if 13 <= utc_hour < 17:
        return "NY"
    if 0 <= utc_hour < 8:
        return "Asian"
    if 21 <= utc_hour < 24 or 0 <= utc_hour < 5:
        return "Off"
    return "Other"


def compute_features(trades: list[dict]) -> list[dict]:
    """Compute per-trade regime features."""
    # Sort by entry time
    trades = sorted(trades, key=lambda t: t.get("entry_time_utc", ""))

    # Compute median spread
    spreads = [float(t.get("spread_at_entry", 0) or 0) for t in trades if float(t.get("spread_at_entry", 0) or 0) > 0]
    median_spread = sorted(spreads)[len(spreads) // 2] if spreads else 0.01

    # Compute median ATR
    atrs = [float(t.get("atr_at_entry", 0) or 0) for t in trades if float(t.get("atr_at_entry", 0) or 0) > 0]
    median_atr = sorted(atrs)[len(atrs) // 2] if atrs else 0.00050

    features = []
    rolling_wr = []  # Last 5 outcomes

    for i, t in enumerate(trades):
        entry_time_str = t.get("entry_time_utc", "")
        try:
            entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
            utc_hour = entry_dt.hour
        except (ValueError, TypeError):
            utc_hour = 12  # default

        atr = float(t.get("atr_at_entry", 0) or 0)
        spread = float(t.get("spread_at_entry", 0) or 0)
        mae_pnl = float(t.get("max_adverse_excursion_pnl", 0) or 0)
        mae_atr = float(t.get("max_adverse_excursion_atr", 0) or 0)
        mfe_pnl = float(t.get("max_favorable_excursion_pnl", 0) or 0)
        realized = float(t.get("realized_pnl", 0) or 0)
        hold = float(t.get("hold_seconds", 0) or 0)
        entry_context = str(t.get("entry_context", "") or "")
        signal = str(t.get("entry_signal_type", "") or "")
        mode = str(t.get("entry_mode", "") or "")

        # 1. Volatility regime
        vol_expansion = atr / median_atr if median_atr > 0 else 1.0
        if vol_expansion > 1.5:
            vol_regime = "high"
        elif vol_expansion < 0.7:
            vol_regime = "low"
        else:
            vol_regime = "normal"

        # 2. Range compression (approximate from MAE/MFE ratio)
        # If MAE is small relative to ATR, range is compressed
        range_compression = mae_atr if mae_atr > 0 else 0.0
        if range_compression < 0.15:
            range_regime = "compressed"
        elif range_compression > 0.50:
            range_regime = "expanded"
        else:
            range_regime = "normal"

        # 3. Session
        session = classify_session(utc_hour)

        # 4. Spread quality
        spread_ratio = spread / median_spread if median_spread > 0 else 1.0
        spread_regime = "wide" if spread_ratio > 1.5 else "tight"

        # 5. Recent win rate (last 5 trades)
        recent_wr = sum(rolling_wr[-5:]) / min(len(rolling_wr), 5) if rolling_wr else 0.5
        if recent_wr > 0.60:
            hot_cold = "hot"
        elif recent_wr < 0.40:
            hot_cold = "cold"
        else:
            hot_cold = "neutral"

        # 6. Adverse excursion profile
        mae_pct_of_atr = mae_pnl / (atr * 100000 * 0.01) if atr > 0 else 0  # rough pip conversion
        if mae_atr > 0.50:
            adv_regime = "deep"
        elif mae_atr > 0.25:
            adv_regime = "moderate"
        else:
            adv_regime = "shallow"

        # 7. Entry context classification
        if "breakout" in entry_context.lower():
            entry_arch = "breakout"
        elif "continuation" in entry_context.lower():
            entry_arch = "continuation"
        elif "pullback" in entry_context.lower():
            entry_arch = "pullback"
        else:
            entry_arch = "other"

        # Outcome
        is_win = realized > 0
        rolling_wr.append(1 if is_win else 0)

        feat = {
            "ticket": t.get("ticket", ""),
            "entry_time_utc": entry_time_str,
            "utc_hour": utc_hour,
            "session": session,
            "vol_regime": vol_regime,
            "vol_expansion": round(vol_expansion, 2),
            "range_regime": range_regime,
            "range_compression": round(range_compression, 3),
            "spread_regime": spread_regime,
            "spread_ratio": round(spread_ratio, 2),
            "recent_wr": round(recent_wr, 3),
            "hot_cold": hot_cold,
            "adv_regime": adv_regime,
            "mae_atr": round(mae_atr, 3),
            "entry_arch": entry_arch,
            "signal": signal,
            "mode": mode,
            "realized_pnl": round(realized, 4),
            "is_win": int(is_win),
            "hold_seconds": round(hold, 0),
            "mfe_pnl": round(mfe_pnl, 4),
            "mae_pnl": round(mae_pnl, 4),
        }
        features.append(feat)

    return features


def analyze_by_regime(features: list[dict]):
    """Cross-tabulate win rate and net P/L by regime features."""
    # Session
    by_session = defaultdict(list)
    for f in features:
        by_session[f["session"]].append(f)

    print("\n=== SESSION REGIME ===")
    print(f"{'Session':>8} | {'N':>4} | {'WR':>6} | {'Net P/L':>10} | {'Exp':>8} | {'Avg Hold':>9}")
    for sess in sorted(by_session.keys()):
        group = by_session[sess]
        wr = sum(1 for f in group if f["is_win"]) / len(group) * 100
        net = sum(f["realized_pnl"] for f in group)
        exp = net / len(group) if group else 0
        avg_hold = sum(f["hold_seconds"] for f in group) / len(group) if group else 0
        print(f"{sess:>8} | {len(group):>4} | {wr:>5.1f}% | ${net:>9.2f} | ${exp:>7.3f} | {avg_hold:>8.0f}s")

    # Volatility
    by_vol = defaultdict(list)
    for f in features:
        by_vol[f["vol_regime"]].append(f)

    print("\n=== VOLATILITY REGIME ===")
    print(f"{'Vol Regime':>10} | {'N':>4} | {'WR':>6} | {'Net P/L':>10} | {'Exp':>8} | {'Avg Vol Exp':>11}")
    for regime in ["low", "normal", "high"]:
        group = by_vol.get(regime, [])
        if not group:
            continue
        wr = sum(1 for f in group if f["is_win"]) / len(group) * 100
        net = sum(f["realized_pnl"] for f in group)
        exp = net / len(group)
        avg_ve = sum(f["vol_expansion"] for f in group) / len(group)
        print(f"{regime:>10} | {len(group):>4} | {wr:>5.1f}% | ${net:>9.2f} | ${exp:>7.3f} | {avg_ve:>10.2f}x")

    # Hot/Cold
    by_hc = defaultdict(list)
    for f in features:
        by_hc[f["hot_cold"]].append(f)

    print("\n=== HOT/COLD STREAK ===")
    print(f"{'Streak':>8} | {'N':>4} | {'WR':>6} | {'Net P/L':>10} | {'Exp':>8}")
    for streak in ["hot", "neutral", "cold"]:
        group = by_hc.get(streak, [])
        if not group:
            continue
        wr = sum(1 for f in group if f["is_win"]) / len(group) * 100
        net = sum(f["realized_pnl"] for f in group)
        exp = net / len(group)
        print(f"{streak:>8} | {len(group):>4} | {wr:>5.1f}% | ${net:>9.2f} | ${exp:>7.3f}")

    # Spread
    by_spread = defaultdict(list)
    for f in features:
        by_spread[f["spread_regime"]].append(f)

    print("\n=== SPREAD REGIME ===")
    print(f"{'Spread':>8} | {'N':>4} | {'WR':>6} | {'Net P/L':>10} | {'Exp':>8} | {'Avg Ratio':>10}")
    for regime in ["tight", "wide"]:
        group = by_spread.get(regime, [])
        if not group:
            continue
        wr = sum(1 for f in group if f["is_win"]) / len(group) * 100
        net = sum(f["realized_pnl"] for f in group)
        exp = net / len(group)
        avg_ratio = sum(f["spread_ratio"] for f in group) / len(group)
        print(f"{regime:>8} | {len(group):>4} | {wr:>5.1f}% | ${net:>9.2f} | ${exp:>7.3f} | {avg_ratio:>9.2f}x")

    # Entry architecture
    by_arch = defaultdict(list)
    for f in features:
        by_arch[f["entry_arch"]].append(f)

    print("\n=== ENTRY ARCHITECTURE ===")
    print(f"{'Arch':>14} | {'N':>4} | {'WR':>6} | {'Net P/L':>10} | {'Exp':>8}")
    for arch in sorted(by_arch.keys()):
        group = by_arch[arch]
        wr = sum(1 for f in group if f["is_win"]) / len(group) * 100
        net = sum(f["realized_pnl"] for f in group)
        exp = net / len(group)
        print(f"{arch:>14} | {len(group):>4} | {wr:>5.1f}% | ${net:>9.2f} | ${exp:>7.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--signal", default=None)
    parser.add_argument("--mode", default=None)
    args = parser.parse_args()

    trades = load_jsonl(TRADE_LOG)

    if args.symbol:
        trades = [t for t in trades if str(t.get("symbol", "")).upper() == args.symbol.upper()]
    if args.signal:
        trades = [t for t in trades if str(t.get("entry_signal_type", "")) == args.signal]
    if args.mode:
        trades = [t for t in trades if str(t.get("entry_mode", "")).upper() == args.mode.upper()]

    print(f"Regime-router features — {len(trades)} trades")
    print()

    features = compute_features(trades)

    # Output CSV
    output_path = ROOT / "reports" / "regime_features.csv"
    output_path.parent.mkdir(exist_ok=True)

    if features:
        fieldnames = list(features[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(features)

        # Print CSV to stdout
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(features)

    print(f"\nSaved to {output_path}", file=sys.stderr)

    # Analysis
    analyze_by_regime(features)


if __name__ == "__main__":
    main()
