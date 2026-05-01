#!/usr/bin/env python3
"""
Burst Fade Evolution Engine — push the edge beyond its current limits.

Starting from the proven +$300/72h baseline, test 8 evolutionary paths:
1. Micro-position sizing: $8-12 per position instead of $24 → more concurrent positions
2. Product-specific parameters: each product gets its own optimal target/stop
3. Trend-filtered fading: only fade when against the trend, skip with-trend bursts
4. Multi-entry per burst: enter at high + at 50% retrace of the burst candle
5. 1-minute burst detection: catch micro-bursts that 5-min candles miss
6. Dynamic target sizing: target = historical avg pullback for that product
7. Asymmetric stops: wider stops for up-trend bursts, tighter for down-trend
8. Momentum carry: if a burst fade hits TP, immediately look for next burst in same product
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "burst_fade_evolution.json"

# Top 15 products by burst frequency (from expansion scan)
TOP_PRODUCTS = ["RAVE-USD", "TROLL-USD", "BAL-USD", "NOM-USD", "MASK-USD",
                "ALEPH-USD", "CHECK-USD", "BLUR-USD", "AVT-USD", "IOTX-USD",
                "IRYS-USD", "CFG-USD", "BOBBOB-USD", "DASH-USD", "FARTCOIN-USD"]


def fetch_candles_72h(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "FIVE_MINUTE") -> list[dict]:
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append({
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.08)
    return sorted(all_candles, key=lambda x: x["time"])


def build_timeline(candles_by_pid: dict[str, list[dict]]) -> list[int]:
    """Build sorted timeline of all candle times."""
    all_times = set()
    for candles in candles_by_pid.values():
        for c in candles:
            all_times.add(int(c["time"]))
    return sorted(all_times)


def build_lookup(candles_by_pid: dict[str, list[dict]], times: list[int]) -> dict[int, dict[str, dict]]:
    """Build time → {pid → candle} lookup."""
    lookup = {}
    for pid, candles in candles_by_pid.items():
        for c in candles:
            t = int(c["time"])
            if t not in lookup:
                lookup[t] = {}
            lookup[t][pid] = c
    return lookup


def compute_sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0
    return sum(closes[-period:]) / period


# ============================================================
# BASELINE: Original burst fade
# ============================================================
def run_baseline(candles_by_pid, times, lookup, products, **kwargs):
    """Original: $24 quote, max 2 concurrent, fixed 60% target, 30% stop."""
    return run_burst_fade(candles_by_pid, times, lookup, products,
                          quote=24.0, max_concurrent=2,
                          target_frac=0.6, stop_frac=0.3,
                          burst_thresh=2.0, maker_fee_bps=40.0)


# ============================================================
# EVOLUTION 1: Micro-position sizing
# ============================================================
def run_micro_positions(candles_by_pid, times, lookup, products):
    """$10 per position, max 8 concurrent → more diversification, same $48 capital."""
    results = {}
    for max_conc in [4, 6, 8, 10]:
        for quote in [6, 8, 10, 12]:
            if quote * max_conc > 48:
                continue
            r = run_burst_fade(candles_by_pid, times, lookup, products,
                               quote=quote, max_concurrent=max_conc,
                               target_frac=0.6, stop_frac=0.3,
                               burst_thresh=2.0, maker_fee_bps=40.0)
            key = f"micro_quote{quote}_conc{max_conc}"
            results[key] = r
    return results


# ============================================================
# EVOLUTION 2: Product-specific targets
# ============================================================
def run_product_specific_targets(candles_by_pid, times, lookup, products):
    """Each product gets its own optimal target based on historical avg pullback."""
    # First, compute avg pullback per product
    product_pullbacks = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        pullbacks = []
        for i in range(1, len(candles)):
            prev_h = candles[i-1]["high"]
            prev_l = candles[i-1]["low"]
            prev_o = candles[i-1]["open"]
            prev_cl = candles[i-1]["close"]
            mid = (prev_o + prev_cl) / 2 if (prev_o + prev_cl) > 0 else 1
            range_pct = (prev_h - prev_l) / mid * 100
            if range_pct >= 2.0:
                # Measure pullback in next 3 candles
                entry = prev_h
                min_low = entry
                for j in range(1, min(4, len(candles) - i)):
                    if candles[i+j]["low"] < min_low:
                        min_low = candles[i+j]["low"]
                pullback_pct = (entry - min_low) / entry * 100
                pullbacks.append(pullback_pct)

        if pullbacks:
            avg_pullback = sum(pullbacks) / len(pullbacks)
            # Set target at 50-70% of avg pullback
            product_pullbacks[pid] = {
                "avg_pullback": round(avg_pullback, 2),
                "target_frac": round(min(0.7, avg_pullback / 100 * 1.5), 2),
                "stop_frac": round(min(0.4, avg_pullback / 100 * 0.8), 2),
                "samples": len(pullbacks),
            }
        else:
            product_pullbacks[pid] = {"avg_pullback": 0, "target_frac": 0.6, "stop_frac": 0.3, "samples": 0}

    # Run with product-specific params
    r = run_burst_fade(candles_by_pid, times, lookup, products,
                       quote=24.0, max_concurrent=2,
                       target_frac=0.6, stop_frac=0.3,  # fallback
                       burst_thresh=2.0, maker_fee_bps=40.0,
                       product_params=product_pullbacks)

    return {"product_specific_targets": r, "pullback_stats": product_pullbacks}


# ============================================================
# EVOLUTION 3: Trend-filtered fading
# ============================================================
def run_trend_filtered(candles_by_pid, times, lookup, products):
    """Only fade bursts that go AGAINST the trend. Skip with-trend bursts."""
    results = {}
    for trend_period in [12, 24, 48]:  # SMA period for trend
        for filter_mode in ["skip_with_trend", "double_down_with_trend"]:
            r = run_burst_fade(candles_by_pid, times, lookup, products,
                               quote=24.0, max_concurrent=2,
                               target_frac=0.6, stop_frac=0.3,
                               burst_thresh=2.0, maker_fee_bps=40.0,
                               trend_filter_period=trend_period,
                               trend_filter_mode=filter_mode)
            key = f"trend_{trend_period}_{filter_mode[:4]}"
            results[key] = r
    return results


# ============================================================
# EVOLUTION 4: Multi-entry per burst
# ============================================================
def run_multi_entry(candles_by_pid, times, lookup, products):
    """Enter at burst high AND at 50% retrace → two positions per burst."""
    results = {}
    for entry_levels in [2, 3]:
        for entry_spacing in [0.3, 0.5]:
            r = run_burst_fade(candles_by_pid, times, lookup, products,
                               quote=12.0, max_concurrent=4,
                               target_frac=0.6, stop_frac=0.3,
                               burst_thresh=2.0, maker_fee_bps=40.0,
                               multi_entry=entry_levels,
                               multi_entry_spacing=entry_spacing)
            key = f"multi{entry_levels}_sp{entry_spacing}"
            results[key] = r
    return results


# ============================================================
# EVOLUTION 5: 1-minute burst detection
# ============================================================
def run_1min_bursts(client, products):
    """Use 1-min candles to catch micro-bursts invisible on 5-min."""
    results = {}
    candles_1m = {}
    for pid in products[:8]:  # Limit to 8 due to API rate
        try:
            candles_1m[pid] = fetch_candles_72h(client, pid, "ONE_MINUTE")
        except Exception:
            pass

    if len(candles_1m) >= 3:
        times = build_timeline(candles_1m)
        lookup = build_lookup(candles_1m, times)
        for burst_t in [1.0, 1.5, 2.0]:
            for max_conc in [2, 4]:
                r = run_burst_fade(candles_1m, times, lookup, products[:8],
                                   quote=24.0, max_concurrent=max_conc,
                                   target_frac=0.5, stop_frac=0.3,
                                   burst_thresh=burst_t, maker_fee_bps=40.0)
                key = f"1min_bt{burst_t}_conc{max_conc}"
                results[key] = r

    return results


# ============================================================
# EVOLUTION 6: Dynamic quote sizing
# ============================================================
def run_dynamic_quote(candles_by_pid, times, lookup, products):
    """Scale position size based on burst magnitude — bigger bursts get bigger positions."""
    results = {}
    for base_quote in [12, 18, 24]:
        for max_quote_mult in [1.5, 2.0, 3.0]:
            r = run_burst_fade(candles_by_pid, times, lookup, products,
                               quote=base_quote, max_concurrent=3,
                               target_frac=0.6, stop_frac=0.3,
                               burst_thresh=2.0, maker_fee_bps=40.0,
                               dynamic_quote=True, max_quote_multiplier=max_quote_mult)
            key = f"dynq{base_quote}_max{max_quote_mult}"
            results[key] = r
    return results


# ============================================================
# EVOLUTION 7: Asymmetric stops
# ============================================================
def run_asymmetric_stops(candles_by_pid, times, lookup, products):
    """Wider stops for up-bursts (bullish), tighter for down-bursts (bearish)."""
    results = {}
    for up_stop_frac in [0.2, 0.3, 0.4, 0.5]:
        for down_stop_frac in [0.1, 0.2, 0.3]:
            r = run_burst_fade(candles_by_pid, times, lookup, products,
                               quote=24.0, max_concurrent=2,
                               target_frac=0.6, stop_frac=0.3,
                               burst_thresh=2.0, maker_fee_bps=40.0,
                               up_stop_frac=up_stop_frac, down_stop_frac=down_stop_frac)
            key = f"asym_up{up_stop_frac}_dn{down_stop_frac}"
            results[key] = r
    return results


# ============================================================
# EVOLUTION 8: Momentum carry
# ============================================================
def run_momentum_carry(candles_by_pid, times, lookup, products):
    """After hitting TP on a burst fade, immediately check for next burst in same product."""
    results = {}
    for cooldown_bars in [0, 1, 2, 3]:
        r = run_burst_fade(candles_by_pid, times, lookup, products,
                           quote=24.0, max_concurrent=2,
                           target_frac=0.6, stop_frac=0.3,
                           burst_thresh=2.0, maker_fee_bps=40.0,
                           momentum_carry=True, cooldown_bars=cooldown_bars)
        key = f"momcarry_cooldown{cooldown_bars}"
        results[key] = r
    return results


# ============================================================
# Core engine
# ============================================================
def run_burst_fade(
    candles_by_pid: dict[str, list[dict]],
    times: list[int],
    lookup: dict[int, dict[str, dict]],
    products: list[str],
    *,
    quote: float,
    max_concurrent: int,
    target_frac: float,
    stop_frac: float,
    burst_thresh: float,
    maker_fee_bps: float,
    product_params: dict = None,
    trend_filter_period: int = 0,
    trend_filter_mode: str = "",
    multi_entry: int = 0,
    multi_entry_spacing: float = 0,
    dynamic_quote: bool = False,
    max_quote_multiplier: float = 1.0,
    up_stop_frac: float = 0,
    down_stop_frac: float = 0,
    momentum_carry: bool = False,
    cooldown_bars: int = 0,
) -> dict:
    """Core burst fade engine with all evolution flags."""
    fee_rate = maker_fee_bps / 10000.0
    starting_cash = 48.0
    cash = starting_cash
    positions = {}  # pid -> {entry, target, stop, qty, entry_fee}
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    fees = 0.0
    last_candle_time = {}
    position_history = {}  # pid -> list of exit times (for momentum carry cooldown)
    sma_history = {}  # pid -> list of closes (for trend filter)

    for t in times:
        tick = lookup.get(t, {})

        # Update SMA history for trend filter
        if trend_filter_period > 0:
            for pid in products:
                if pid in tick:
                    cl = float(tick[pid]["close"])
                    if pid not in sma_history:
                        sma_history[pid] = []
                    sma_history[pid].append(cl)
                    if len(sma_history[pid]) > trend_filter_period + 10:
                        sma_history[pid] = sma_history[pid][-(trend_filter_period + 10):]

        # === EXITS ===
        exit_pids = []
        for pid, pos in list(positions.items()):
            if pid not in tick:
                continue
            c = tick[pid]
            h = float(c["high"])
            l = float(c["low"])
            ep = pos["entry"]
            tp = pos["target"]
            sp = pos["stop"]
            qty = pos["qty"]

            if l <= tp:
                gross = (ep - tp) * qty
                ef = pos["entry_fee"]
                xf = tp * qty * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                wins += 1
                fees += ef + xf
                cash += ep * qty + net  # Return capital + PnL

                if momentum_carry:
                    if pid not in position_history:
                        position_history[pid] = []
                    position_history[pid].append(t)

                exit_pids.append(pid)
            elif h >= sp:
                gross = (ep - sp) * qty
                ef = pos["entry_fee"]
                xf = sp * qty * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                losses += 1
                fees += ef + xf
                cash += ep * qty + net

                exit_pids.append(pid)

        for pid in exit_pids:
            positions.pop(pid, None)

        # === ENTRIES ===
        available_cash = cash + sum(p["entry"] * p["qty"] for p in positions.values())  # Total capital
        capital_in_use = sum(p["entry"] * p["qty"] for p in positions.values())

        if len(positions) < max_concurrent and available_cash >= quote:
            for pid in products:
                if pid in positions or pid not in tick:
                    continue
                c = tick[pid]
                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])
                mid = (o + cl) / 2 if (o + cl) > 0 else 1
                range_pct = (h - l) / mid * 100

                if range_pct < burst_thresh:
                    continue

                # Trend filter
                if trend_filter_period > 0 and pid in sma_history and len(sma_history[pid]) > trend_filter_period:
                    sma = sum(sma_history[pid][-trend_filter_period:]) / trend_filter_period
                    is_uptrend = cl > sma
                    burst_is_up = cl > o

                    if trend_filter_mode == "skip_with_trend":
                        if (is_uptrend and burst_is_up) or (not is_uptrend and not burst_is_up):
                            continue  # Skip with-trend bursts
                    elif trend_filter_mode == "double_down_with_trend":
                        pass  # No filtering, just proceed

                # Momentum carry cooldown
                if momentum_carry and pid in position_history:
                    last_exits = position_history[pid]
                    # Count how many bars since last exit
                    bars_since = 0
                    for lt in reversed(last_exits):
                        bars_since += 1
                    if bars_since <= cooldown_bars:
                        continue

                # Determine entry price (multi-entry support)
                entries = [(h, 1.0)]  # Default: single entry at high
                if multi_entry >= 2:
                    # Additional entries at retracement levels
                    spacing = range_pct / 100 * multi_entry_spacing
                    for level in range(1, multi_entry):
                        entry_price = h * (1 - spacing * level)
                        entries.append((entry_price, 0.5 ** level))

                # Determine effective stop
                effective_stop_frac = stop_frac
                if up_stop_frac > 0 or down_stop_frac > 0:
                    if cl > o:  # Up burst
                        effective_stop_frac = up_stop_frac if up_stop_frac > 0 else stop_frac
                    else:  # Down burst
                        effective_stop_frac = down_stop_frac if down_stop_frac > 0 else stop_frac

                # Dynamic quote sizing
                effective_quote = quote
                if dynamic_quote and range_pct > 3.0:
                    effective_quote = min(quote * max_quote_multiplier * (range_pct / 3.0), 48.0 - capital_in_use)

                # Product-specific params
                eff_target_frac = target_frac
                eff_stop_frac = effective_stop_frac
                if product_params and pid in product_params:
                    pp = product_params[pid]
                    if pp.get("target_frac", 0) > 0:
                        eff_target_frac = pp["target_frac"]
                    if pp.get("stop_frac", 0) > 0:
                        eff_stop_frac = pp["stop_frac"]

                for entry_price, size_mult in entries:
                    deploy = effective_quote * size_mult
                    if deploy < 1.0 or cash < deploy:
                        continue

                    entry_fee = entry_price * (deploy / entry_price) * fee_rate
                    qty = (deploy - entry_fee) / entry_price
                    if qty <= 0:
                        continue

                    target = entry_price * (1 - range_pct / 100 * eff_target_frac)
                    stop = entry_price * (1 + range_pct / 100 * eff_stop_frac)

                    positions[pid] = {
                        "entry": entry_price,
                        "target": target,
                        "stop": stop,
                        "qty": qty,
                        "entry_fee": entry_fee,
                    }
                    cash -= deploy
                    break  # One product per time step

    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "realized_net": round(realized_net, 2),
        "return_pct": round(realized_net / starting_cash * 100, 2),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, closes) * 100, 1),
        "avg_pnl_per_close": round(realized_net / max(1, closes), 4) if closes > 0 else 0,
        "total_fees": round(fees, 2),
        "trades_per_day": round(closes / (len(times) * 5 / 60 / 24), 1),
    }


def main() -> None:
    client = CoinbaseAdvancedClient()

    print("Fetching candles for top 15 products...")
    candles_cache = {}
    for pid in TOP_PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    times = build_timeline(candles_cache)
    lookup = build_lookup(candles_cache, times)
    print(f"\nTimeline: {len(times)} time steps, {len(candles_cache)} products")

    all_results = {}

    # Baseline
    print("\n=== BASELINE ===")
    all_results["baseline"] = run_baseline(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    print(f"  ${all_results['baseline']['realized_net']:.2f} ({all_results['baseline']['return_pct']:.1f}%), {all_results['baseline']['closes']} closes")

    # Evolution 1: Micro positions
    print("\n=== EVOLUTION 1: Micro-Position Sizing ===")
    e1 = run_micro_positions(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    best_e1 = max(e1.items(), key=lambda x: x[1]["realized_net"])
    all_results["evolution_1_micro"] = {"best": best_e1[1], "config": best_e1[0], "all": {k: v for k, v in sorted(e1.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
    print(f"  Best: {best_e1[0]} → ${best_e1[1]['realized_net']:.2f} ({best_e1[1]['return_pct']:.1f}%), {best_e1[1]['closes']} closes")

    # Evolution 2: Product-specific targets
    print("\n=== EVOLUTION 2: Product-Specific Targets ===")
    e2 = run_product_specific_targets(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    all_results["evolution_2_product_specific"] = e2
    print(f"  ${e2['product_specific_targets']['realized_net']:.2f} ({e2['product_specific_targets']['return_pct']:.1f}%), {e2['product_specific_targets']['closes']} closes")

    # Evolution 3: Trend filtering
    print("\n=== EVOLUTION 3: Trend-Filtered Fading ===")
    e3 = run_trend_filtered(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    best_e3 = max(e3.items(), key=lambda x: x[1]["realized_net"])
    all_results["evolution_3_trend"] = {"best": best_e3[1], "config": best_e3[0], "all": {k: v for k, v in sorted(e3.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
    print(f"  Best: {best_e3[0]} → ${best_e3[1]['realized_net']:.2f} ({best_e3[1]['return_pct']:.1f}%)")

    # Evolution 4: Multi-entry
    print("\n=== EVOLUTION 4: Multi-Entry Per Burst ===")
    e4 = run_multi_entry(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    best_e4 = max(e4.items(), key=lambda x: x[1]["realized_net"])
    all_results["evolution_4_multi_entry"] = {"best": best_e4[1], "config": best_e4[0], "all": {k: v for k, v in sorted(e4.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
    print(f"  Best: {best_e4[0]} → ${best_e4[1]['realized_net']:.2f} ({best_e4[1]['return_pct']:.1f}%)")

    # Evolution 5: 1-minute bursts
    print("\n=== EVOLUTION 5: 1-Minute Burst Detection ===")
    e5 = run_1min_bursts(client, TOP_PRODUCTS[:10])
    if e5:
        best_e5 = max(e5.items(), key=lambda x: x[1]["realized_net"])
        all_results["evolution_5_1min"] = {"best": best_e5[1], "config": best_e5[0], "all": {k: v for k, v in sorted(e5.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
        print(f"  Best: {best_e5[0]} → ${best_e5[1]['realized_net']:.2f} ({best_e5[1]['return_pct']:.1f}%)")
    else:
        print("  No results")

    # Evolution 6: Dynamic quote sizing
    print("\n=== EVOLUTION 6: Dynamic Quote Sizing ===")
    e6 = run_dynamic_quote(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    best_e6 = max(e6.items(), key=lambda x: x[1]["realized_net"])
    all_results["evolution_6_dynamic_quote"] = {"best": best_e6[1], "config": best_e6[0], "all": {k: v for k, v in sorted(e6.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
    print(f"  Best: {best_e6[0]} → ${best_e6[1]['realized_net']:.2f} ({best_e6[1]['return_pct']:.1f}%)")

    # Evolution 7: Asymmetric stops
    print("\n=== EVOLUTION 7: Asymmetric Stops ===")
    e7 = run_asymmetric_stops(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    best_e7 = max(e7.items(), key=lambda x: x[1]["realized_net"])
    all_results["evolution_7_asymmetric"] = {"best": best_e7[1], "config": best_e7[0], "all": {k: v for k, v in sorted(e7.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
    print(f"  Best: {best_e7[0]} → ${best_e7[1]['realized_net']:.2f} ({best_e7[1]['return_pct']:.1f}%)")

    # Evolution 8: Momentum carry
    print("\n=== EVOLUTION 8: Momentum Carry ===")
    e8 = run_momentum_carry(candles_cache, times, lookup, TOP_PRODUCTS[:15])
    best_e8 = max(e8.items(), key=lambda x: x[1]["realized_net"])
    all_results["evolution_8_momentum"] = {"best": best_e8[1], "config": best_e8[0], "all": {k: v for k, v in sorted(e8.items(), key=lambda x: x[1]["realized_net"], reverse=True)[:5]}}
    print(f"  Best: {best_e8[0]} → ${best_e8[1]['realized_net']:.2f} ({best_e8[1]['return_pct']:.1f}%)")

    # Summary
    print(f"\n{'='*120}")
    print(f"{'Evolution':<30} {'Net $':>8} {'Ret%':>7} {'Closes':>6} {'Win%':>6} {'Avg/Cl':>8} {'Tr/day':>7}")
    print(f"{'='*120}")
    baseline = all_results["baseline"]
    print(f"{'BASELINE':<30} ${baseline['realized_net']:>6.2f} {baseline['return_pct']:>6.1f}% {baseline['closes']:>6} {baseline['win_rate']:>5.1f}% ${baseline['avg_pnl_per_close']:>6.4f} {baseline['trades_per_day']:>6.1f}")

    for evo_name, evo_key in [
        ("Micro-Position", "evolution_1_micro"),
        ("Product-Specific", "evolution_2_product_specific"),
        ("Trend-Filtered", "evolution_3_trend"),
        ("Multi-Entry", "evolution_4_multi_entry"),
        ("1-Minute Bursts", "evolution_5_1min"),
        ("Dynamic Quote", "evolution_6_dynamic_quote"),
        ("Asymmetric Stops", "evolution_7_asymmetric"),
        ("Momentum Carry", "evolution_8_momentum"),
    ]:
        if evo_key in all_results:
            r = all_results[evo_key]["best"]
            print(f"{evo_name:<30} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['closes']:>6} {r['win_rate']:>5.1f}% ${r['avg_pnl_per_close']:>6.4f} {r['trades_per_day']:>6.1f}")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline": baseline,
        "evolutions": all_results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
