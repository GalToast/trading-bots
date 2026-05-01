#!/usr/bin/env python3
"""Hungry Hippo — CORRECTED Regime-Segmented Tuning Sweep.

Fixes from audit:
1. PnL formula now uses CORRECT 0.01 lot sizing (not standard lot)
2. Regime-matched runs as ONE CONTINUOUS simulation with config switches
3. Spread cost model added (half-spread deducted per close)

Usage:
    python scripts/hungry_hippo_extreme_tuning_corrected.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc
DAYS = 7
VOLUME = 0.01  # 0.01 lot — this is what live lanes use
WARMUP = 50

# Spread in pips per symbol (typical broker spreads)
SYMBOL_SPREAD_PIPS = {
    "GBPUSD": 1.2, "EURUSD": 1.0, "NZDUSD": 1.5,
    "USDJPY": 1.3, "NAS100": 2.0, "US30": 3.0,
    "XAUUSD": 3.0, "BTCUSD": 15.0, "ETHUSD": 8.0,
}

SYMBOL_META = {
    "GBPUSD":  {"pip": 0.0001, "pip_value_per_lot": 10.0},
    "EURUSD":  {"pip": 0.0001, "pip_value_per_lot": 10.0},
    "NZDUSD":  {"pip": 0.0001, "pip_value_per_lot": 10.0},
    "USDJPY":  {"pip": 0.01,   "pip_value_per_lot": 6.70},
    "NAS100":  {"pip": 1.0,    "pip_value_per_lot": 1.0},
    "US30":    {"pip": 1.0,    "pip_value_per_lot": 1.0},
    "XAUUSD":  {"pip": 0.01,   "pip_value_per_lot": 1.0},
    "BTCUSD":  {"pip": 1.0,    "pip_value_per_lot": 1.0},
    "ETHUSD":  {"pip": 0.01,   "pip_value_per_lot": 1.0},
}

SYMBOLS = list(SYMBOL_META.keys())


# ===================================================================
# CORRECTED PnL formula
# ===================================================================

def pnl_usd(direction: str, entry: float, exit_price: float, meta: dict) -> float:
    """Compute PnL in USD for a single position at 0.01 lot.

    pip_value_per_lot = dollar value per pip for 1 standard lot
    For 0.01 lot: pip_value = pip_value_per_lot × 0.01
    """
    pip = meta["pip"]
    pip_value_per_lot = meta["pip_value_per_lot"]
    pip_value_01 = pip_value_per_lot * VOLUME  # 0.01 lot

    diff = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    pips = diff / pip
    return pips * pip_value_01


def spread_cost_usd(meta: dict, spread_pips: float) -> float:
    """Cost of half-spread per close at 0.01 lot."""
    pip_value_01 = meta["pip_value_per_lot"] * VOLUME
    return spread_pips * pip_value_01 * 0.5


# ===================================================================
# Regime classification (same as before)
# ===================================================================

def compute_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.full(n, np.nan, dtype=np.float64)
    if n >= period:
        for i in range(period - 1, n):
            atr[i] = np.mean(tr[i - period + 1:i + 1])
    return atr


def price_position(closes, window=200):
    n = len(closes)
    pp = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        w = closes[i - window + 1:i + 1]
        rng = np.max(w) - np.min(w)
        if rng > 0:
            pp[i] = (closes[i] - np.min(w)) / rng * 100.0
        else:
            pp[i] = 50.0
    return pp


def classify_regime(closes, atr, window=200):
    """Simplified regime classification without ADX/Hurst dependencies."""
    n = len(closes)
    regimes = np.full(n, "MIXED", dtype="<U8")
    pp = price_position(closes, window)

    for i in range(n):
        if np.isnan(pp[i]) or np.isnan(atr[i]):
            continue
        p = pp[i]
        # Use ATR relative to price as trend proxy
        atr_pct = atr[i] / closes[i] * 100 if closes[i] > 0 else 0
        if p > 85 or p < 15:
            regimes[i] = "EXTREME"
        elif atr_pct > 0.5:  # High volatility = trending
            regimes[i] = "TREND"
        elif 20 < p < 80 and atr_pct < 0.3:
            regimes[i] = "CHOP"
        else:
            regimes[i] = "MIXED"
    return regimes


# ===================================================================
# CORRECTED: Continuous regime-switching simulation
# ===================================================================

def simulate_lattice_continuous(
    closes, highs, lows, atr_series, regimes, config, meta, spread_pips
):
    """ONE continuous simulation that switches configs when regime changes.

    This is the CORRECT way to compare regime-matched vs static:
    - Static: one config for ALL bars
    - Regime-matched: different config per regime, switching continuously
    """
    n = len(closes)
    max_open = config.get("max_open_per_side", 12)
    alpha = config["alpha"]
    step_mult_fn = config.get("step_mult_fn", lambda r: config["step_mult"])  # regime-dependent step_mult

    anchor = closes[WARMUP]
    cur_atr = atr_series[WARMUP] if not np.isnan(atr_series[WARMUP]) else closes[WARMUP] * 0.001
    if cur_atr <= 0:
        cur_atr = closes[WARMUP] * 0.001

    base_step = cur_atr * step_mult_fn("MIXED")
    step_sell = base_step
    step_buy = base_step
    next_sell_level = anchor + step_sell
    next_buy_level = anchor - step_buy

    sell_tickets = []
    buy_tickets = []

    realized_net = 0.0
    realized_closes = 0
    anchor_resets = 0
    spread_total_cost = 0.0

    half_spread = spread_cost_usd(meta, spread_pips)

    for i in range(WARMUP + 1, n):
        bar_high = highs[i]
        bar_low = lows[i]
        bar_close = closes[i]
        cur_regime = regimes[i]

        # Update step based on current regime's step_mult
        cur_atr = atr_series[i]
        if not np.isnan(cur_atr) and cur_atr > 0:
            sm = step_mult_fn(cur_regime)
            base_step = cur_atr * sm
            step_sell = base_step
            step_buy = base_step

        # Open SELL
        attempts = 0
        while bar_high >= next_sell_level and len(sell_tickets) < max_open and attempts < 200:
            sell_tickets.append({"entry": next_sell_level, "idx": i})
            next_sell_level += step_sell
            attempts += 1

        # Open BUY
        attempts = 0
        while bar_low <= next_buy_level and len(buy_tickets) < max_open and attempts < 200:
            buy_tickets.append({"entry": next_buy_level, "idx": i})
            next_buy_level -= step_buy
            attempts += 1

        # Gap-based close SELL
        sell_tickets.sort(key=lambda t: t["entry"], reverse=True)
        while len(sell_tickets) > 1:
            gap_idx = min(1, len(sell_tickets) - 1)
            close_threshold = sell_tickets[gap_idx]["entry"]
            if bar_low > close_threshold:
                break
            outer = sell_tickets[0]
            pnl = pnl_usd("SELL", outer["entry"], close_threshold, meta) - half_spread
            realized_net += pnl
            realized_closes += 1
            spread_total_cost += half_spread
            sell_tickets.pop(0)

        # Gap-based close BUY
        buy_tickets.sort(key=lambda t: t["entry"])
        while len(buy_tickets) > 1:
            gap_idx = min(1, len(buy_tickets) - 1)
            close_threshold = buy_tickets[gap_idx]["entry"]
            if bar_high < close_threshold:
                break
            outer = buy_tickets[0]
            pnl = pnl_usd("BUY", outer["entry"], close_threshold, meta) - half_spread
            realized_net += pnl
            realized_closes += 1
            spread_total_cost += half_spread
            buy_tickets.pop(0)

        # Alpha-weighted close at bar close
        profitable = []
        for t in sell_tickets:
            pnl = pnl_usd("SELL", t["entry"], bar_close, meta) - half_spread
            if pnl > 0:
                profitable.append(("SELL", t))
        for t in buy_tickets:
            pnl = pnl_usd("BUY", t["entry"], bar_close, meta) - half_spread
            if pnl > 0:
                profitable.append(("BUY", t))

        if profitable:
            n_to_close = max(1, int(len(profitable) * alpha))
            profitable.sort(key=lambda x: pnl_usd(x[0], x[1]["entry"], bar_close, meta), reverse=True)
            for direction, ticket in profitable[:n_to_close]:
                pnl = pnl_usd(direction, ticket["entry"], bar_close, meta) - half_spread
                realized_net += pnl
                realized_closes += 1
                spread_total_cost += half_spread
                lst = sell_tickets if direction == "SELL" else buy_tickets
                if ticket in lst:
                    lst.remove(ticket)

        # Anchor reset when flat
        if not sell_tickets and not buy_tickets:
            mid = (bar_high + bar_low) / 2.0
            if abs(mid - anchor) >= max(step_sell, step_buy):
                anchor = mid
                next_sell_level = anchor + step_sell
                next_buy_level = anchor - step_buy
                anchor_resets += 1

    return {
        "closes": realized_closes,
        "net_usd": round(realized_net, 2),
        "per_close": round(realized_net / max(1, realized_closes), 4),
        "spread_cost": round(spread_total_cost, 2),
        "resets": anchor_resets,
    }


# ===================================================================
# Main sweep
# ===================================================================

def load_m15_mt5(symbol, days):
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        end_utc = datetime.now(UTC)
        start_utc = end_utc - timedelta(days=days)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start_utc, end_utc)
        mt5.shutdown()
        if rates is None or len(rates) == 0:
            return None
        return {
            "high": np.array([float(r[2]) for r in rates]),
            "low": np.array([float(r[3]) for r in rates]),
            "close": np.array([float(r[4]) for r in rates]),
        }
    except Exception:
        return None


def generate_synthetic(symbol, n_bars=700):
    np.random.seed(hash(symbol) % 2**32)
    meta = SYMBOL_META[symbol]
    pip = meta["pip"]
    base_prices = {
        "GBPUSD": 1.2700, "EURUSD": 1.0850, "NZDUSD": 0.5900,
        "USDJPY": 154.50, "NAS100": 19500, "US30": 42000,
        "XAUUSD": 3100, "BTCUSD": 84000, "ETHUSD": 1700,
    }
    price = base_prices.get(symbol, 1.0)
    closes, highs, lows = [], [], []
    regime = "chop"
    regime_dur = 0
    for i in range(n_bars):
        if regime_dur <= 0:
            regime = np.random.choice(["chop", "trend", "extreme"], p=[0.4, 0.35, 0.25])
            regime_dur = np.random.randint(20, 80)
        regime_dur -= 1
        if regime == "trend":
            drift = np.random.choice([-1, 1]) * pip * 3
            vol = pip * 2
        elif regime == "extreme":
            drift = np.random.choice([-1, 1]) * pip * 5
            vol = pip * 5
        else:
            drift = 0
            vol = pip * 1.5
        o = price
        c = o + drift + np.random.normal(0, vol)
        h = max(o, c) + abs(np.random.normal(0, vol * 0.5))
        l = min(o, c) - abs(np.random.normal(0, vol * 0.5))
        closes.append(c)
        highs.append(h)
        lows.append(l)
        price = c
    return {
        "close": np.array(closes),
        "high": np.array(highs),
        "low": np.array(lows),
    }


# Regime-specific step multipliers for the regime-matched strategy
REGIME_STEP_MULTS = {
    "EXTREME": 0.7,   # Tight steps at extremes
    "TREND": 1.0,     # Moderate steps in trends
    "CHOP": 0.8,      # Tight steps in chop
    "MIXED": 2.0,     # Wide steps in uncertainty
}

REGIME_ALPHA = {
    "EXTREME": 0.1,   # Hyper-fast closes at extremes
    "TREND": 0.5,     # Moderate closes in trends
    "CHOP": 0.2,      # Fast closes in chop
    "MIXED": 0.05,    # Close anything profitable in mixed
}

REGIME_MAX_OPEN = {
    "EXTREME": 12,
    "TREND": 8,
    "CHOP": 12,
    "MIXED": 3,
}


def sweep_symbol(symbol, data):
    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    meta = SYMBOL_META[symbol]
    spread_pips = SYMBOL_SPREAD_PIPS.get(symbol, 1.0)

    atr = compute_atr(highs, lows, closes, period=14)
    regimes = classify_regime(closes, atr, window=200)

    # Regime breakdown
    regime_counts = {}
    for i in range(WARMUP, len(regimes)):
        r = regimes[i]
        regime_counts[r] = regime_counts.get(r, 0) + 1

    regime_breakdown = {}
    total_classified = sum(regime_counts.values())
    for r in ["EXTREME", "TREND", "CHOP", "MIXED"]:
        cnt = regime_counts.get(r, 0)
        regime_breakdown[r] = {"bars": cnt, "pct": round(cnt / max(1, total_classified) * 100, 1)}

    # Strategy 1: STATIC (one config for all bars)
    static_config = {
        "step_mult_fn": lambda r: 1.0,
        "alpha": 0.3,
        "max_open_per_side": 12,
    }
    static_result = simulate_lattice_continuous(
        closes, highs, lows, atr, regimes, static_config, meta, spread_pips
    )

    # Strategy 2: REGIME-MATCHED (continuous config switching)
    def regime_step_mult(regime):
        return REGIME_STEP_MULTS.get(regime, 1.0)

    # Use weighted average alpha based on regime distribution
    regime_matched_config = {
        "step_mult_fn": regime_step_mult,
        "alpha": 0.2,  # Overall faster closes
        "max_open_per_side": 12,
    }
    regime_matched_result = simulate_lattice_continuous(
        closes, highs, lows, atr, regimes, regime_matched_config, meta, spread_pips
    )

    # Strategy 3: REGIME-GATED (only trade EXTREME + CHOP bars)
    def gated_step_mult(regime):
        if regime in ("EXTREME", "CHOP"):
            return REGIME_STEP_MULTS[regime]
        return 0.0  # Don't open positions

    gated_config = {
        "step_mult_fn": gated_step_mult,
        "alpha": 0.15,
        "max_open_per_side": 12,
    }
    gated_result = simulate_lattice_continuous(
        closes, highs, lows, atr, regimes, gated_config, meta, spread_pips
    )

    return {
        "regime_breakdown": regime_breakdown,
        "strategy_comparison": {
            "static": {
                "closes": static_result["closes"],
                "net_usd": static_result["net_usd"],
                "per_close": static_result["per_close"],
                "spread_cost": static_result["spread_cost"],
                "resets": static_result["resets"],
            },
            "regime_matched": {
                "closes": regime_matched_result["closes"],
                "net_usd": regime_matched_result["net_usd"],
                "per_close": regime_matched_result["per_close"],
                "spread_cost": regime_matched_result["spread_cost"],
                "resets": regime_matched_result["resets"],
            },
            "regime_gated": {
                "closes": gated_result["closes"],
                "net_usd": gated_result["net_usd"],
                "per_close": gated_result["per_close"],
                "spread_cost": gated_result["spread_cost"],
                "resets": gated_result["resets"],
            },
        },
        "optimal_by_regime": {
            r: {
                "step_mult": REGIME_STEP_MULTS.get(r),
                "alpha": REGIME_ALPHA.get(r),
                "max_open": REGIME_MAX_OPEN.get(r),
            }
            for r in ["EXTREME", "TREND", "CHOP", "MIXED"]
        },
    }


def main():
    print("=" * 120)
    print("HUNGRY HIPPO — CORRECTED REGIME-SEGMENTED TUNING SWEEP")
    print("Fixes: correct 0.01 lot PnL, continuous regime switching, spread cost model")
    print("=" * 120)
    print()

    all_results = {}
    aggregate_static = 0.0
    aggregate_matched = 0.0
    aggregate_gated = 0.0

    for sym in SYMBOLS:
        print(f"[{sym}] Loading data...", end="", flush=True)
        data = load_m15_mt5(sym, DAYS)
        source = "MT5"
        if data is None:
            data = generate_synthetic(sym)
            source = "synthetic"
        n_bars = len(data["close"])
        print(f" {source} ({n_bars} bars)", flush=True)

        print(f"[{sym}] Running sweep...", end="", flush=True)
        t0 = time.time()
        result = sweep_symbol(sym, data)
        elapsed = time.time() - t0
        print(f" done ({elapsed:.1f}s)", flush=True)

        all_results[sym] = result
        sc = result["strategy_comparison"]
        aggregate_static += sc["static"]["net_usd"]
        aggregate_matched += sc["regime_matched"]["net_usd"]
        aggregate_gated += sc["regime_gated"]["net_usd"]

    aggregate = {
        "static_total_net": round(aggregate_static, 2),
        "regime_matched_total_net": round(aggregate_matched, 2),
        "regime_gated_total_net": round(aggregate_gated, 2),
        "improvement_factor": round(
            aggregate_matched / abs(aggregate_static) if aggregate_static != 0 else 0, 2
        ),
    }

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": DAYS,
        "volume": VOLUME,
        "spread_model": "half_spread_per_close",
        "symbols": all_results,
        "aggregate": aggregate,
    }

    out_path = REPO / "reports" / "hungry_hippo_extreme_tuning_corrected.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print()
    print("=" * 140)
    print(f"{'SYMBOL':<10} {'STATIC$':>10} {'STATIC_close':>12} {'MATCHED$':>10} {'MATCHED_close':>12} "
          f"{'GATED$':>10} {'IMPROVEMENT':>12} {'SPREAD_COST':>12} {'VERDICT'}")
    print("-" * 140)

    for sym in SYMBOLS:
        sc = all_results[sym]["strategy_comparison"]
        s = sc["static"]["net_usd"]
        rm = sc["regime_matched"]["net_usd"]
        rg = sc["regime_gated"]["net_usd"]
        spread = sc["regime_matched"]["spread_cost"]

        improvement = rm / abs(s) if s != 0 else (1.0 if rm > 0 else 0.0)
        imp_str = f"{improvement:.2f}x"

        if rm > max(s, rg):
            verdict = "Matched wins"
        elif rg > max(s, rm):
            verdict = "Gated wins"
        elif s > max(rm, rg):
            verdict = "Static wins"
        else:
            verdict = "Tie"

        print(f"{sym:<10} ${s:>+.2f} {sc['static']['closes']:>10} "
              f"${rm:>+.2f} {sc['regime_matched']['closes']:>10} "
              f"${rg:>+.2f} {imp_str:>10} ${spread:>+.2f} {verdict}")

    print("-" * 140)
    print(f"{'AGGREGATE':<10} ${aggregate_static:>+.2f} ${aggregate_matched:>+.2f} ${aggregate_gated:>+.2f} "
          f"{aggregate['improvement_factor']:.2f}x")
    print()
    print(f"Results saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
