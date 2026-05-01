"""
Price Structure Detector -- maps price action patterns to lattice geometry.

Detects: key levels, consolidation zones, wedges, flags, pressure zones.
Outputs unified structure reading per symbol with lattice geometry mapping.

Usage:
    python scripts/price_structure_detector.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYMBOLS = ["GBPUSD", "EURUSD", "ETHUSD", "NAS100", "BTCUSD", "XAUUSD"]
BARS_COUNT = 200

# ── Lattice geometry mapping ──────────────────────────────────────────────
LATTICE_MAP = {
    "consolidation": {
        "step_mult": 0.3,
        "asymmetry_ratio": 1.0,
        "alpha": 0.2,
        "max_open_per_side": 15,
        "reason": "Consolidation -> vacuum cleaner mode",
    },
    "wedge": {
        "step_mult": 0.2,
        "asymmetry_ratio": 1.0,
        "alpha": 0.1,
        "max_open_per_side": 20,
        "reason": "Wedge pre-breakout -> pre-position both sides",
    },
    "flag": {
        "step_mult": 0.5,
        "asymmetry_ratio": 1.5,
        "alpha": 0.3,
        "max_open_per_side": 10,
        "reason": "Flag continuation -> ride with trend",
    },
    "breakout": {
        "step_mult": 1.5,
        "asymmetry_ratio": 2.0,
        "alpha": 0.5,
        "max_open_per_side": 8,
        "reason": "Breakout -> ride the trend",
    },
    "pressure": {
        "step_mult": 0.4,
        "asymmetry_ratio": 1.5,
        "alpha": 0.2,
        "max_open_per_side": 12,
        "reason": "Pressure zone -> fade the level",
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────

def calc_atr(rates: list, period: int = 14) -> float:
    """Average True Range over `period` bars."""
    if len(rates) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(rates)):
        high = rates[i]["high"]
        low = rates[i]["low"]
        prev_close = rates[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


def round_to_step(price: float, step: float) -> float:
    """Round price to nearest step increment."""
    return round(price / step) * step


def get_pip_step(symbol: str) -> float:
    """Approximate pip/step size for rounding key levels."""
    steps = {
        "GBPUSD": 0.0005,
        "EURUSD": 0.0005,
        "ETHUSD": 5.0,
        "NAS100": 5.0,
        "BTCUSD": 50.0,
        "XAUUSD": 0.5,
    }
    return steps.get(symbol, 0.001)


def get_decimal_places(symbol: str) -> int:
    if symbol in ("ETHUSD", "NAS100"):
        return 2
    if symbol == "BTCUSD":
        return 0
    if symbol == "XAUUSD":
        return 2
    return 5


def fetch_rates(symbol: str, count: int = 200):
    """Fetch M15 bars from MT5. Returns list of dicts with OHLCV."""
    try:
        import MetaTrader5 as mt5  # noqa: F401
    except ImportError:
        return None

    if not mt5.initialize():
        return None

    tf = mt5.TIMEFRAME_M15
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        return None

    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


# ── Structure Detectors ───────────────────────────────────────────────────

def detect_key_levels(rates: list, symbol: str) -> list[dict]:
    """Find price levels touched/bounced 2+ times."""
    step = get_pip_step(symbol)
    touch_tolerance = 0.001  # 0.1%

    # Collect candidate prices from highs and lows
    candidate_prices = []
    for r in rates:
        candidate_prices.append(round_to_step(r["high"], step))
        candidate_prices.append(round_to_step(r["low"], step))

    # Cluster nearby candidates
    unique_levels = sorted(set(candidate_prices))
    level_map: dict[float, list[int]] = {}  # price -> list of bar indices

    for level in unique_levels:
        touches = []
        for idx, r in enumerate(rates):
            high_touch = abs(r["high"] - level) / level <= touch_tolerance if level != 0 else False
            low_touch = abs(r["low"] - level) / level <= touch_tolerance if level != 0 else False
            if high_touch or low_touch:
                touches.append(idx)
        if len(touches) >= 2:
            level_map[level] = touches

    # Classify as support or resistance
    results = []
    for price, touches in sorted(level_map.items(), key=lambda x: -len(x[1])):
        current_price = rates[-1]["close"]
        bars_ago = len(rates) - 1 - max(touches)

        bounce_count = 0
        for idx in touches:
            r = rates[idx]
            if price >= current_price:
                if r["close"] < price:
                    bounce_count += 1
            else:
                if r["close"] > price:
                    bounce_count += 1

        level_type = "resistance" if price >= current_price else "support"

        results.append({
            "price": round(price, get_decimal_places(symbol)),
            "type": level_type,
            "touches": len(touches),
            "bounces": bounce_count,
            "last_touch_bars_ago": bars_ago,
        })

    return results[:10]


def detect_consolidation(rates: list, atr: float) -> dict | None:
    """Look for 10+ consecutive bars with compressed range OR 15+ bars within 1.0x ATR."""
    if atr <= 0 or len(rates) < 15:
        return None

    avg_range = sum(r["high"] - r["low"] for r in rates[-50:]) / min(50, len(rates))
    if avg_range <= 0:
        return None

    # Method A: 10+ consecutive bars where range < 0.5 * average
    threshold_a = 0.5 * avg_range
    consecutive_a = 0
    max_consecutive_a = 0
    best_end_a = -1

    for i in range(len(rates) - 1, max(0, len(rates) - 80), -1):
        bar_range = rates[i]["high"] - rates[i]["low"]
        if bar_range < threshold_a:
            consecutive_a += 1
            if consecutive_a > max_consecutive_a:
                max_consecutive_a = consecutive_a
                best_end_a = i
        else:
            consecutive_a = 0

    # Method B: any 15-bar window within range < 1.5 * ATR (relaxed for Asian session)
    threshold_b = 1.5 * atr
    best_count_b = 0
    best_range_b = None

    for start in range(max(0, len(rates) - 80), len(rates) - 14):
        window_rates = rates[start:start + 15]
        highs = [r["high"] for r in window_rates]
        lows = [r["low"] for r in window_rates]
        price_range = max(highs) - min(lows)
        if price_range < threshold_b:
            if best_count_b == 0:  # take the most recent qualifying window
                best_count_b = 15
                best_range_b = (min(lows), max(highs))

    dec = get_decimal_places(symbol_from_rates(rates))

    consolidation_a = None
    if max_consecutive_a >= 10 and best_end_a >= 0:
        end = best_end_a
        start = end - max_consecutive_a + 1
        window_rates = rates[start:end + 1]
        highs = [r["high"] for r in window_rates]
        lows = [r["low"] for r in window_rates]
        consolidation_a = {
            "top": round(max(highs), dec),
            "bottom": round(min(lows), dec),
            "bars": max_consecutive_a,
            "squeeze": max_consecutive_a >= 15,
        }

    consolidation_b = None
    if best_count_b >= 15 and best_range_b:
        consolidation_b = {
            "top": round(best_range_b[1], dec),
            "bottom": round(best_range_b[0], dec),
            "bars": best_count_b,
            "squeeze": best_count_b >= 20,
        }

    if consolidation_a and consolidation_b:
        return consolidation_a if consolidation_a["bars"] >= consolidation_b["bars"] else consolidation_b
    return consolidation_a or consolidation_b


def symbol_from_rates(rates: list) -> str:
    """Infer symbol from price magnitude (fallback helper)."""
    p = rates[-1]["close"] if rates else 0
    if p > 50000:
        return "BTCUSD"
    if p > 1000:
        return "ETHUSD"
    if p > 10000:
        return "NAS100"
    if p > 1000:
        return "XAUUSD"
    return "GBPUSD"


def detect_wedge(rates: list, atr: float) -> dict | None:
    """Look for converging highs and lows (compression)."""
    if atr <= 0 or len(rates) < 12:
        return None

    best_wedge = None
    best_compression = 0

    for window in range(10, min(30, len(rates))):
        window_rates = rates[-window:]
        highs = [r["high"] for r in window_rates]
        lows = [r["low"] for r in window_rates]

        mid = window // 2
        first_highs = highs[:mid]
        second_highs = highs[mid:]
        first_lows = lows[:mid]
        second_lows = lows[mid:]

        first_high_avg = sum(first_highs) / len(first_highs)
        second_high_avg = sum(second_highs) / len(second_highs)
        first_low_avg = sum(first_lows) / len(first_lows)
        second_low_avg = sum(second_lows) / len(second_lows)

        high_range = first_high_avg - second_high_avg
        low_range = second_low_avg - first_low_avg

        wedge_type = None
        if high_range > 0 and low_range > 0:
            wedge_type = "symmetric"
        elif high_range > 0 and abs(low_range) < atr * 0.1:
            wedge_type = "descending"
        elif low_range > 0 and abs(high_range) < atr * 0.1:
            wedge_type = "ascending"

        if wedge_type is None:
            continue

        overall_high_range = max(highs) - min(highs)
        overall_low_range = max(lows) - min(lows)
        if overall_high_range <= 0:
            continue
        compression = (overall_high_range - overall_low_range) / overall_high_range
        compression = max(0.0, min(1.0, compression))

        if compression > best_compression:
            best_compression = compression
            best_wedge = {
                "type": wedge_type,
                "compression": round(compression, 2),
                "bars": window,
                "breakout_imminent": compression >= 0.6 and window >= 15,
            }

    return best_wedge if best_compression >= 0.15 else None


def detect_flag(rates: list, atr: float) -> dict | None:
    """After a strong move, look for shallow counter-move (flag)."""
    if atr <= 0 or len(rates) < 10:
        return None

    closes = [r["close"] for r in rates]
    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]

    for flag_start in range(len(rates) - 8, max(3, len(rates) - 30), -1):
        prior_start = max(0, flag_start - 8)
        prior_closes = closes[prior_start:flag_start]

        if len(prior_closes) < 3:
            continue

        prior_move = prior_closes[-1] - prior_closes[0]
        prior_atr_mult = abs(prior_move) / atr if atr > 0 else 0

        if prior_atr_mult < 1.0:
            continue

        flag_end = len(rates)
        flag_closes = closes[flag_start:flag_end]

        if len(flag_closes) < 3 or len(flag_closes) > 15:
            continue

        flag_move = flag_closes[-1] - flag_closes[0]

        if prior_move > 0 and flag_move > 0:
            continue
        if prior_move < 0 and flag_move < 0:
            continue

        retrace_pct = abs(flag_move) / abs(prior_move) if prior_move != 0 else 1.0

        if retrace_pct > 0.382:
            continue

        direction = "bullish" if prior_move > 0 else "bearish"

        return {
            "direction": direction,
            "retrace_pct": round(retrace_pct, 3),
            "bars": len(flag_closes),
            "continuation_likely": retrace_pct < 0.25,
        }

    return None


def detect_pressure_zones(rates: list, symbol: str, atr: float) -> list[dict]:
    """Find levels where price rejected 3+ times at meaningful swing points."""
    step = get_pip_step(symbol)
    candidate_step = step * 4  # wider step for swing-level candidates

    current_price = rates[-1]["close"]

    # Candidate levels from swing highs/lows over last 80 bars
    candidates = set()
    lookback = min(80, len(rates))
    for i in range(5, lookback - 5):
        r = rates[i]
        window_highs = [rates[j]["high"] for j in range(i - 5, i + 6)]
        window_lows = [rates[j]["low"] for j in range(i - 5, i + 6)]
        if r["high"] == max(window_highs):
            candidates.add(round_to_step(r["high"], candidate_step))
        if r["low"] == min(window_lows):
            candidates.add(round_to_step(r["low"], candidate_step))

    results = []
    for level in candidates:
        rejections = 0
        last_rejection_bars_ago = 999

        for idx, r in enumerate(rates):
            if level >= current_price:
                # Resistance: high reached near level, closed well below
                if r["high"] >= level - atr * 0.5 and r["high"] <= level + atr * 0.5:
                    if r["close"] < level - atr * 0.3:
                        rejections += 1
                        last_rejection_bars_ago = min(last_rejection_bars_ago, len(rates) - 1 - idx)
            else:
                # Support: low reached near level, closed well above
                if r["low"] <= level + atr * 0.5 and r["low"] >= level - atr * 0.5:
                    if r["close"] > level + atr * 0.3:
                        rejections += 1
                        last_rejection_bars_ago = min(last_rejection_bars_ago, len(rates) - 1 - idx)

        if rejections >= 3 and last_rejection_bars_ago <= 50:
            level_type = "resistance" if level >= current_price else "support"
            breakout_prob = max(0.10, min(0.65, 1.0 - rejections * 0.10))

            results.append({
                "level": round(level, get_decimal_places(symbol)),
                "type": level_type,
                "rejections": rejections,
                "last_rejection_bars_ago": last_rejection_bars_ago,
                "next_breakout_probability": round(breakout_prob, 2),
            })

    return sorted(results, key=lambda x: -x["rejections"])[:5]


def detect_breakout(rates: list, atr: float) -> dict | None:
    """Detect if price has recently broken out of a range."""
    if atr <= 0 or len(rates) < 20:
        return None

    recent = rates[-20:]
    highs = [r["high"] for r in recent]
    lows = [r["low"] for r in recent]

    for i in range(-3, 0):
        idx = len(recent) + i
        if idx < 1:
            continue
        r = recent[idx]

        prev_high = max(h for h in highs[:idx])
        prev_low = min(l for l in lows[:idx])
        buffer = atr * 0.3

        if r["close"] > prev_high + buffer:
            avg_vol = sum(rates[max(0, idx - 9):idx + 1][k]["tick_volume"] for k in range(min(10, len(rates)))) / 10
            return {
                "direction": "up",
                "breakout_level": round(prev_high, get_decimal_places(symbol_from_rates(rates))),
                "breakout_bar": abs(i),
                "volume_confirm": r["tick_volume"] > avg_vol,
            }
        if r["close"] < prev_low - buffer:
            avg_vol = sum(rates[max(0, idx - 9):idx + 1][k]["tick_volume"] for k in range(min(10, len(rates)))) / 10
            return {
                "direction": "down",
                "breakout_level": round(prev_low, get_decimal_places(symbol_from_rates(rates))),
                "breakout_bar": abs(i),
                "volume_confirm": r["tick_volume"] > avg_vol,
            }

    return None


# ── Unified Structure Reading ─────────────────────────────────────────────

def analyze_symbol(symbol: str, rates: list) -> dict:
    """Run all detectors on a symbol's rates and produce unified reading."""
    atr = calc_atr(rates, 14)

    structures: dict[str, Any] = {}
    confidences: dict[str, float] = {}

    # A. Key levels
    levels = detect_key_levels(rates, symbol)

    # B. Consolidation
    consolidation = detect_consolidation(rates, atr)
    if consolidation:
        structures["consolidation"] = consolidation
        confidences["consolidation"] = min(0.95, 0.5 + consolidation["bars"] * 0.02)

    # C. Wedge
    wedge = detect_wedge(rates, atr)
    if wedge:
        structures["wedge"] = wedge
        confidences["wedge"] = min(0.90, 0.4 + wedge["compression"] * 0.4 + (0.1 if wedge["bars"] >= 15 else 0))

    # D. Flag
    flag = detect_flag(rates, atr)
    if flag:
        structures["flag"] = flag
        confidences["flag"] = min(0.90, 0.5 + (0.382 - flag["retrace_pct"]) * 0.8)

    # E. Pressure zones
    pressure_zones = detect_pressure_zones(rates, symbol, atr)
    if pressure_zones:
        structures["pressure"] = pressure_zones[0]
        confidences["pressure"] = min(0.85, 0.4 + pressure_zones[0]["rejections"] * 0.1)

    # Breakout
    breakout = detect_breakout(rates, atr)
    if breakout:
        structures["breakout"] = breakout
        confidences["breakout"] = min(0.95, 0.6 + (0.15 if breakout["volume_confirm"] else 0))

    # Boost consolidation+wedge confluence
    has_consolidation = "consolidation" in structures
    has_wedge = "wedge" in structures
    if has_consolidation and has_wedge:
        confidences["consolidation"] = min(0.95, confidences.get("consolidation", 0) + 0.1)
        confidences["wedge"] = min(0.95, confidences.get("wedge", 0) + 0.05)

    if not confidences:
        return {
            "primary_structure": "none",
            "confidence": 0.0,
            "atr": round(atr, get_decimal_places(symbol)),
            "current_price": round(rates[-1]["close"], get_decimal_places(symbol)),
            "structures": {},
            "key_levels": levels[:5],
            "lattice_geometry": {
                "step_mult": 0.5,
                "asymmetry_ratio": 1.0,
                "alpha": 0.2,
                "max_open_per_side": 10,
                "reason": "No clear structure -> neutral geometry",
            },
        }

    primary = max(confidences, key=confidences.get)
    confidence = round(confidences[primary], 2)

    geom = LATTICE_MAP.get(primary, LATTICE_MAP["consolidation"]).copy()

    if primary == "flag":
        geom["reason"] = f"Flag ({flag['direction']}) -> continuation at {flag['retrace_pct']*100:.0f}% retrace"
    elif primary == "wedge":
        geom["reason"] = f"Wedge ({wedge['type']}) compression {wedge['compression']:.2f} -> pre-breakout positioning"
    elif primary == "breakout":
        geom["reason"] = f"Breakout ({breakout['direction']}) -> expand geometry, ride trend"
    elif primary == "pressure":
        pz = pressure_zones[0]
        geom["reason"] = f"Pressure at {pz['level']} ({pz['rejections']} rejections) -> fade or prepare breakout"

    if primary == "flag":
        geom["asymmetry_note"] = f"favor {'longs' if flag['direction'] == 'bullish' else 'shorts'} 1.5:1"
    elif primary == "breakout":
        geom["asymmetry_note"] = f"favor {'longs' if breakout['direction'] == 'up' else 'shorts'} 2:1"

    return {
        "primary_structure": primary,
        "confidence": confidence,
        "atr": round(atr, get_decimal_places(symbol)),
        "current_price": round(rates[-1]["close"], get_decimal_places(symbol)),
        "structures": structures,
        "key_levels": levels[:5],
        "lattice_geometry": geom,
    }


def detect_structure(symbol: str, rates: list) -> dict:
    """Alias for analyze_symbol to satisfy bridge contract."""
    return analyze_symbol(symbol, rates)


def structure_to_geometry(analysis: dict) -> dict:
    """Extract lattice geometry from analysis results."""
    return analysis.get("lattice_geometry", {
        "step_mult": 0.5,
        "asymmetry_ratio": 1.0,
        "alpha": 0.2,
        "max_open_per_side": 10,
        "reason": "Neutral default (mapping failed)",
    })


# ── Output ─────────────────────────────────────────────────────────────────

def print_summary_table(results: dict):
    """Print a formatted summary table."""
    header = f"{'SYMBOL':<10} {'PRIMARY STRUCT':<20} {'CONF':<6} {'STEPx':<7} {'ASYM':<7} {'ALPHA':<7} {'MAX_OPEN':<10} {'REASON'}"
    print(header)
    print("-" * len(header))

    for symbol, data in results.items():
        primary = data["primary_structure"].capitalize()
        if primary == "Flag" and "flag" in data["structures"]:
            primary += f" ({data['structures']['flag']['direction']})"
        if primary == "Wedge" and "wedge" in data["structures"]:
            primary += f" ({data['structures']['wedge']['type']})"
        if primary == "Breakout" and "breakout" in data["structures"]:
            primary += f" ({data['structures']['breakout']['direction']})"
        if primary == "None":
            primary = "Range-bound"

        conf = f"{data['confidence']:.2f}"
        geom = data["lattice_geometry"]
        step = f"{geom['step_mult']}x"
        asym = f"{geom['asymmetry_ratio']}:1"
        alpha = f"{geom['alpha']}"
        max_open = str(geom["max_open_per_side"])
        reason = geom["reason"][:50]

        print(f"{symbol:<10} {primary:<20} {conf:<6} {step:<7} {asym:<7} {alpha:<7} {max_open:<10} {reason}")


def save_report(results: dict, output_path: str):
    """Save results to JSON file."""
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": results,
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(script_dir)
    output_path = os.path.join(base_dir, "reports", "price_structure_analysis.json")

    print("=" * 80)
    print("PRICE STRUCTURE DETECTOR")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    results = {}

    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} M15 bars ({BARS_COUNT} bars)...")
        rates = fetch_rates(symbol, BARS_COUNT)

        if rates is None:
            print(f"  WARNING: Could not fetch {symbol} from MT5. Skipping.")
            results[symbol] = {
                "primary_structure": "error",
                "confidence": 0.0,
                "error": "MT5 connection failed or insufficient data",
                "lattice_geometry": {
                    "step_mult": 0.5,
                    "asymmetry_ratio": 1.0,
                    "alpha": 0.2,
                    "max_open_per_side": 10,
                    "reason": "No data -> neutral default",
                },
            }
            continue

        print(f"  Got {len(rates)} bars. Analyzing structures...")
        analysis = analyze_symbol(symbol, rates)
        results[symbol] = analysis

        print(f"\n  {symbol} current structure:")
        print(f"  - Primary: {analysis['primary_structure'].upper()} "
              f"(confidence: {analysis['confidence']:.2f})")
        print(f"  - Current price: {analysis['current_price']}, ATR(14): {analysis['atr']}")

        if "consolidation" in analysis["structures"]:
            c = analysis["structures"]["consolidation"]
            print(f"  - Consolidation: {c['bottom']}-{c['top']} ({c['bars']} bars, squeeze: {c['squeeze']})")
        if "wedge" in analysis["structures"]:
            w = analysis["structures"]["wedge"]
            print(f"  - Wedge: {w['type']} compression={w['compression']:.2f} "
                  f"bars={w['bars']} breakout_imminent={w['breakout_imminent']}")
        if "flag" in analysis["structures"]:
            f_ = analysis["structures"]["flag"]
            print(f"  - Flag: {f_['direction']} retrace={f_['retrace_pct']*100:.1f}% "
                  f"bars={f_['bars']} continuation={f_['continuation_likely']}")
        if "pressure" in analysis["structures"]:
            p = analysis["structures"]["pressure"]
            print(f"  - Pressure: {p['level']} ({p['type']}, {p['rejections']} rejections, "
                  f"breakout_prob={p['next_breakout_probability']})")
        if "breakout" in analysis["structures"]:
            b = analysis["structures"]["breakout"]
            print(f"  - Breakout: {b['direction']} at {b['breakout_level']} "
                  f"(volume_confirm={b['volume_confirm']})")

        if analysis["key_levels"]:
            print(f"  - Key levels ({len(analysis['key_levels'])} found):")
            for lv in analysis["key_levels"][:3]:
                print(f"    {lv['price']} ({lv['type']}, {lv['touches']} touches, "
                      f"last {lv['last_touch_bars_ago']} bars ago)")

        geom = analysis["lattice_geometry"]
        print(f"  - Lattice: step={geom['step_mult']}x asym={geom['asymmetry_ratio']}:1 "
              f"alpha={geom['alpha']} max_open={geom['max_open_per_side']}")
        print(f"    Reason: {geom['reason']}")

    print("\n")
    print_summary_table(results)
    save_report(results, output_path)

    return results


if __name__ == "__main__":
    main()
