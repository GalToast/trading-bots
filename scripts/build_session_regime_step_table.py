#!/usr/bin/env python3
"""
Build a session-weighted adaptive step multiplier table.

Combines regime classification with session timing data to produce
per-symbol, per-hour effective buy/sell step sizes.

Usage:
    python scripts/build_session_regime_step_table.py
"""

import json
import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths (relative to trading-bots root)
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIME_PATH = os.path.join(ROOT, "reports", "regime_classification_live.json")
EVENTS_PATH = os.path.join(
    ROOT, "reports", "penetration_lattice_live_rearm_941777_events.jsonl"
)
OUTPUT_PATH = os.path.join(ROOT, "reports", "session_regime_step_table.json")

# ---------------------------------------------------------------------------
# 1. Session Weight Analysis
# ---------------------------------------------------------------------------

# Canonical session weights (derived from FX session characteristics).
# These are the default weights; if the rearm event stream exists, they are
# overridden by empirical analysis.
SESSION_WEIGHTS_DEFAULT = {
    # hour -> weight
}

def _build_default_session_weights():
    """Build canonical session weight map."""
    weights = {}
    for h in range(24):
        if 6 <= h < 9:
            weights[h] = 3.0       # London open
        elif 13 <= h < 16:
            weights[h] = 1.5       # NY overlap
        elif h >= 22 or h < 4:
            weights[h] = 0.2       # Asian / dead
        else:
            weights[h] = 1.0       # Other
    return weights


def _analyse_event_stream(path: str) -> dict[int, float] | None:
    """
    Parse the rearm event stream and derive empirical session weights.

    Returns a dict of hour -> weight, or None if file doesn't exist / is empty.
    """
    if not os.path.exists(path):
        return None

    closes_by_hour: dict[int, list[float]] = {}
    total_pnl = 0.0

    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Only process close events
            event_type = evt.get("event", "") or evt.get("type", "")
            if "close" not in event_type.lower():
                continue

            # Extract timestamp and PnL
            ts = evt.get("timestamp") or evt.get("ts") or evt.get("time")
            pnl = evt.get("pnl") or evt.get("profit") or evt.get("gain", 0.0)

            if ts is None:
                continue

            # Parse hour from various timestamp formats
            hour = None
            if isinstance(ts, (int, float)):
                from datetime import datetime as _dt
                hour = _dt.fromtimestamp(ts, tz=timezone.utc).hour
            elif isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hour = dt.hour
                except (ValueError, AttributeError):
                    pass

            if hour is None:
                continue

            pnl_val = float(pnl) if pnl is not None else 0.0
            closes_by_hour.setdefault(hour, []).append(pnl_val)
            total_pnl += pnl_val

    if not closes_by_hour or total_pnl == 0:
        return None

    # Compute session weights proportional to profit share, normalized so
    # the neutral baseline is 1.0 (average hour weight = 1.0).
    hour_pnl = {h: sum(pnls) for h, pnls in closes_by_hour.items()}
    # Fill missing hours with 0
    for h in range(24):
        hour_pnl.setdefault(h, 0.0)

    raw_weights = {}
    for h in range(24):
        share = hour_pnl[h] / total_pnl if total_pnl != 0 else 0.0
        raw_weights[h] = share

    # Normalize: average weight across 24h = 1.0
    avg = sum(raw_weights.values()) / 24.0
    if avg > 0:
        weights = {h: w / avg for h, w in raw_weights.items()}
    else:
        return None

    return weights


def get_session_weights() -> dict[int, float]:
    """Return session weights (empirical if available, else canonical defaults)."""
    empirical = _analyse_event_stream(EVENTS_PATH)
    if empirical is not None:
        return empirical
    return _build_default_session_weights()


# ---------------------------------------------------------------------------
# 2. Regime Coefficients
# ---------------------------------------------------------------------------

REGIME_COEFFS = {
    "STRONG_TREND": 1.5,
    "WEAK_TREND": 1.0,
    "TRANSITION": 0.8,
    "RANGE": 0.5,
}


def load_regime_classification(path: str) -> list[dict]:
    """Load regime classification JSON."""
    with open(path, "r") as fh:
        data = json.load(fh)
    return data.get("symbols", [])


# ---------------------------------------------------------------------------
# 3. Directional Skew
# ---------------------------------------------------------------------------

def directional_weights(directional_bias: float) -> tuple[float, float]:
    """Return (buy_weight, sell_weight) from directional bias."""
    if directional_bias > 0.05:
        return 0.6, 0.4
    elif directional_bias < -0.05:
        return 0.4, 0.6
    else:
        return 0.5, 0.5


# ---------------------------------------------------------------------------
# 4 & 5. Build the Lookup Table
# ---------------------------------------------------------------------------

SESSION_LABELS = {
    (6, 9): "LONDON",
    (13, 16): "NY_OVERLAP",
    (22, 24): "ASIAN",
    (0, 4): "ASIAN",
}


def session_label(hour: int) -> str:
    for (lo, hi), label in SESSION_LABELS.items():
        if lo <= hour < hi:
            return label
    return "OTHER"


def build_step_table(
    symbols: list[dict],
    session_weights: dict[int, float],
) -> dict:
    """Build the full session-regime step table."""
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": {},
    }

    for sym in symbols:
        name = sym["symbol"]
        atr = sym["current_atr"]
        regime = sym["regime"]
        regime_coeff = REGIME_COEFFS.get(regime, 1.0)
        direction = sym["directional_bias"]
        buy_w, sell_w = directional_weights(direction)

        by_hour = {}
        for h in range(24):
            sw = session_weights.get(h, 1.0)
            # effective_buy_step = ATR * regime_coeff * session_weight * (2 * buy_weight)
            # effective_sell_step = ATR * regime_coeff * session_weight * (2 * sell_weight)
            buy_step = atr * regime_coeff * sw * (2 * buy_w)
            sell_step = atr * regime_coeff * sw * (2 * sell_w)

            by_hour[str(h)] = {
                "session_weight": sw,
                "buy_step": round(buy_step, 6),
                "sell_step": round(sell_step, 6),
            }

        result["symbols"][name] = {
            "atr_m15": round(atr, 6) if atr < 10 else round(atr, 2),
            "regime": regime,
            "regime_coeff": regime_coeff,
            "direction": f"{direction:+.2f}" if direction >= 0 else f"{direction:.2f}",
            "by_hour": by_hour,
        }

    return result


# ---------------------------------------------------------------------------
# 6. Print Summary
# ---------------------------------------------------------------------------

def print_summary(table: dict):
    """Print the most impactful hour/regime combinations."""
    rows = []

    for sym_name, sym_data in table["symbols"].items():
        atr = sym_data["atr_m15"]
        regime = sym_data["regime"]
        for h_str, hour_data in sym_data["by_hour"].items():
            hour = int(h_str)
            sw = hour_data["session_weight"]
            buy = hour_data["buy_step"]
            sell = hour_data["sell_step"]
            label = session_label(hour)
            # Impact = session_weight * regime_coeff (higher = more impactful)
            impact = sw * sym_data["regime_coeff"]
            rows.append({
                "symbol": sym_name,
                "hour": hour,
                "hour_str": f"{hour:02d}:00",
                "regime": regime,
                "session": label,
                "buy_step": buy,
                "sell_step": sell,
                "session_weight": sw,
                "impact": impact,
            })

    # Sort by impact descending, take top entries per symbol
    rows.sort(key=lambda r: (-r["impact"], r["symbol"], r["hour"]))

    # Show top rows: all high-impact rows (impact >= 3.0) + one per hour for each symbol
    # Cap at a readable number
    print("\n" + "=" * 100)
    print("SESSION-REGIME ADAPTIVE STEP TABLE — TOP COMBINATIONS")
    print("=" * 100)

    header = f"{'SYMBOL':<10} {'HOUR':<8} {'REGIME':<16} {'SESSION':<14} {'BUY_STEP':>12} {'SELL_STEP':>12} {'SESSION_WEIGHT':>16}"
    print(header)
    print("-" * 100)

    shown = set()
    for r in rows:
        key = (r["symbol"], r["hour"])
        if key in shown:
            continue
        shown.add(key)

        buy_str = f"{r['buy_step']:.5f}" if r["buy_step"] < 10 else f"{r['buy_step']:.1f}"
        sell_str = f"{r['sell_step']:.5f}" if r["sell_step"] < 10 else f"{r['sell_step']:.1f}"

        print(
            f"{r['symbol']:<10} {r['hour_str']:<8} {r['regime']:<16} {r['session']:<14} "
            f"{buy_str:>12} {sell_str:>12} {r['session_weight']:.1f}x"
        )

        if len(shown) >= 60:
            break

    print("=" * 100)
    print(f"Total symbols: {len(table['symbols'])}")
    print(f"Generated at: {table['generated_at']}")

    # Quick per-symbol summary
    print("\nPER-SYMBOL REGIME & ATR SUMMARY:")
    print("-" * 80)
    print(f"{'SYMBOL':<10} {'REGIME':<16} {'ATR':>14} {'DIR':>8} {'COEFF':>8}")
    print("-" * 80)
    for sym_name, sym_data in table["symbols"].items():
        print(
            f"{sym_name:<10} {sym_data['regime']:<16} "
            f"{sym_data['atr_m15']:>14} {sym_data['direction']:>8} {sym_data['regime_coeff']:>8}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading regime classification...")
    symbols = load_regime_classification(REGIME_PATH)
    print(f"  Found {len(symbols)} symbols.")

    print("Computing session weights...")
    session_weights = get_session_weights()
    # Check if we used empirical or default
    empirical_available = os.path.exists(EVENTS_PATH)
    if empirical_available:
        print(f"  Using empirical weights from {EVENTS_PATH}")
    else:
        print(f"  Event stream not found at {EVENTS_PATH}")
        print("  Using canonical session weights (London=3.0, NY=1.5, Asian=0.2, Other=1.0)")

    print("Building session-regime step table...")
    table = build_step_table(symbols, session_weights)

    # Save
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as fh:
        json.dump(table, fh, indent=2)
    print(f"  Saved to {OUTPUT_PATH}")

    # Print summary
    print_summary(table)


if __name__ == "__main__":
    main()
