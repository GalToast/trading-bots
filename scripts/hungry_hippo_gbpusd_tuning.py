#!/usr/bin/env python3
"""Hungry Hippo GBPUSD M15 Lattice Tuning Sweep — Bar-Level Simulation.

Grid-search over step_multiplier, asymmetry_ratio, regime_gate_adx_threshold, and close_alpha
using M15 bars (not individual ticks) for speed. The bar-level simulation captures the same
lattice logic: anchor, levels, open/close on penetration, PnL computation.

Usage:
    python scripts/hungry_hippo_gbpusd_tuning.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

REPO = Path(__file__).resolve().parent
SCRIPTS = REPO
sys.path.insert(0, str(SCRIPTS))

UTC = timezone.utc
SYMBOL = "GBPUSD"
TIMEFRAME = "M15"
DAYS = 7
VOLUME = 0.01
PIP_SIZE = 0.0001  # GBPUSD pip size

# ---------------------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------------------
STEP_MULTIPLIERS = [0.5, 0.75, 1.0, 1.25, 1.5]
ASYMMETRY_RATIOS = [0.5, 0.67, 1.0, 1.5, 2.0]
ADX_THRESHOLDS = [25, 30, 35, None]
CLOSE_ALPHAS = [0.3, 0.5, 0.7]

# Fixed params
MAX_OPEN_PER_SIDE = 12
MAX_FLOATING_LOSS_USD = -15.0
MAX_LATTICE_WINDOW_BARS = 240


def load_m15_bars(symbol: str, days: int) -> list[dict]:
    """Load M15 bars for the last N days from MT5."""
    end_utc = datetime.now(UTC)
    start_utc = end_utc - timedelta(days=days)

    # MT5 copy_rates_range uses bar-level data, much faster than ticks
    tf = mt5.TIMEFRAME_M15
    rates = mt5.copy_rates_range(symbol, tf, start_utc, end_utc)
    if rates is None or len(rates) == 0:
        return []

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


def compute_adx_series(bars: list[dict], period: int = 14) -> dict[int, float]:
    """Compute ADX for each bar, returning {bar_time: adx} dict."""
    if len(bars) < period + 2:
        return {}

    adx_map = {}
    for i in range(period + 1, len(bars)):
        window = bars[max(0, i - period - 1):i + 1]
        highs = [float(b["high"]) for b in window]
        lows = [float(b["low"]) for b in window]

        plus_dm = []
        minus_dm = []
        for j in range(1, len(highs)):
            up_move = highs[j] - highs[j - 1]
            down_move = lows[j - 1] - lows[j]
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

        if len(plus_dm) < period:
            continue

        avg_plus = sum(plus_dm[-period:]) / period
        avg_minus = sum(minus_dm[-period:]) / period

        tr_sum = avg_plus + avg_minus
        if tr_sum == 0.0:
            adx = 0.0
        else:
            di_plus = (avg_plus / tr_sum) * 100.0
            di_minus = (avg_minus / tr_sum) * 100.0
            adx = abs(di_plus - di_minus) / max(0.001, di_plus + di_minus) * 100.0

        adx_map[bars[i]["time"]] = adx

    return adx_map


def derive_asymmetric_steps(base_step: float, asymmetry_ratio: float) -> tuple[float, float]:
    """Derive buy/sell steps from base step and asymmetry ratio.

    asymmetry_ratio > 1.0 -> BUY-tight (buy step narrower, sell step wider)
    asymmetry_ratio < 1.0 -> SELL-tight (sell step narrower, buy step wider)
    """
    if asymmetry_ratio <= 0.0:
        return base_step, base_step

    if asymmetry_ratio >= 1.0:
        sqrt_r = math.sqrt(asymmetry_ratio)
        step_buy = base_step / sqrt_r
        step_sell = base_step * sqrt_r
    else:
        sqrt_r = math.sqrt(1.0 / asymmetry_ratio)
        step_buy = base_step * sqrt_r
        step_sell = base_step / sqrt_r

    return step_sell, step_buy


def tick_pnl_usd(direction: str, entry_price: float, exit_price: float, volume: float = VOLUME) -> float:
    """Compute PnL in USD for a position."""
    # For GBPUSD: 0.01 lot = 1000 units, 1 pip = $0.10
    # PnL = (exit - entry) * volume * 100000 for BUY
    # PnL = (entry - exit) * volume * 100000 for SELL
    if direction == "BUY":
        return (exit_price - entry_price) * volume * 100000.0
    else:
        return (entry_price - exit_price) * volume * 100000.0


def run_bar_simulation(bars, step, step_sell, step_buy, close_alpha, adx_threshold, adx_map):
    """Run the lattice simulation on M15 bars.

    Returns dict with realized_closes, realized_net_usd, max_drawdown, anchor_resets, avg_open_positions.
    """
    if not bars:
        return {"realized_closes": 0, "realized_net_usd": 0.0, "max_drawdown": 0.0, "anchor_resets": 0, "avg_open_positions": 0.0}

    # State
    anchor = bars[0]["close"]
    next_sell_level = anchor + step_sell
    next_buy_level = anchor - step_buy

    # Each ticket: {direction, entry_price, level_idx, opened_bar_idx}
    sell_tickets = []  # sorted highest to lowest (by entry_price desc)
    buy_tickets = []   # sorted lowest to highest (by entry_price asc)

    realized_net = 0.0
    realized_closes = 0
    anchor_resets = 0
    lattice_started_bar = 0

    running_net = 0.0
    peak_net = 0.0
    max_drawdown = 0.0
    open_pos_sum = 0

    for idx, bar in enumerate(bars):
        bar_time = bar["time"]
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]

        # ADX regime gate
        if adx_threshold is not None:
            current_adx = adx_map.get(bar_time, 0.0)
            if current_adx > adx_threshold:
                # Skip this bar - strong trend regime, lattice doesn't perform well
                open_pos_sum += len(sell_tickets) + len(buy_tickets)
                continue

        # Open SELL positions: price goes UP through sell levels
        while bar_high >= next_sell_level and len(sell_tickets) < MAX_OPEN_PER_SIDE:
            level_idx = round((next_sell_level - anchor) / step_sell) if step_sell > 0 else 0
            sell_tickets.append({
                "direction": "SELL",
                "entry_price": next_sell_level,
                "level_idx": int(level_idx),
                "opened_bar": idx,
            })
            next_sell_level += step_sell
            if lattice_started_bar == 0:
                lattice_started_bar = idx

        # Open BUY positions: price goes DOWN through buy levels
        while bar_low <= next_buy_level and len(buy_tickets) < MAX_OPEN_PER_SIDE:
            level_idx = round((anchor - next_buy_level) / step_buy) if step_buy > 0 else 0
            buy_tickets.append({
                "direction": "BUY",
                "entry_price": next_buy_level,
                "level_idx": int(level_idx),
                "opened_bar": idx,
            })
            next_buy_level -= step_buy
            if lattice_started_bar == 0:
                lattice_started_bar = idx

        # Close SELL positions: price comes back DOWN
        # Sort sells by entry_price descending
        sell_tickets.sort(key=lambda t: t["entry_price"], reverse=True)
        close_gap = 1
        while len(sell_tickets) > close_gap:
            # The gap-th sell (0-indexed) sets the close threshold
            close_threshold = sell_tickets[close_gap]["entry_price"]
            if bar_low > close_threshold:
                break
            # Close the outermost (highest) sell
            outer = sell_tickets[0]
            pnl = tick_pnl_usd("SELL", outer["entry_price"], close_threshold, VOLUME)
            realized_net += pnl
            realized_closes += 1
            sell_tickets.pop(0)

        # Close BUY positions: price comes back UP
        buy_tickets.sort(key=lambda t: t["entry_price"])
        while len(buy_tickets) > close_gap:
            close_threshold = buy_tickets[close_gap]["entry_price"]
            if bar_high < close_threshold:
                break
            outer = buy_tickets[0]
            pnl = tick_pnl_usd("BUY", outer["entry_price"], close_threshold, VOLUME)
            realized_net += pnl
            realized_closes += 1
            buy_tickets.pop(0)

        # Check forced close conditions
        all_tickets = sell_tickets + buy_tickets
        if all_tickets:
            # Worst floating PnL
            worst_pnl = float("inf")
            for t in all_tickets:
                if t["direction"] == "SELL":
                    pnl = tick_pnl_usd("SELL", t["entry_price"], bar_high, VOLUME)
                else:
                    pnl = tick_pnl_usd("BUY", t["entry_price"], bar_low, VOLUME)
                worst_pnl = min(worst_pnl, pnl)

            # Timed out?
            timed_out = (lattice_started_bar > 0 and
                         (idx - lattice_started_bar) >= MAX_LATTICE_WINDOW_BARS)

            if worst_pnl <= MAX_FLOATING_LOSS_USD or timed_out:
                for t in list(all_tickets):
                    if t["direction"] == "SELL":
                        pnl = tick_pnl_usd("SELL", t["entry_price"], bar_close, VOLUME)
                    else:
                        pnl = tick_pnl_usd("BUY", t["entry_price"], bar_close, VOLUME)
                    realized_net += pnl
                    realized_closes += 1
                    if t in sell_tickets:
                        sell_tickets.remove(t)
                    if t in buy_tickets:
                        buy_tickets.remove(t)
                anchor_resets += 1
                lattice_started_bar = 0

        # Reset anchor if flat
        if not sell_tickets and not buy_tickets:
            mid = (bar_high + bar_low) / 2.0
            if abs(mid - anchor) >= max(step_sell, step_buy):
                anchor = mid
                next_sell_level = anchor + step_sell
                next_buy_level = anchor - step_buy
                anchor_resets += 1
                lattice_started_bar = 0

        # Track drawdown and avg open
        running_net = realized_net
        peak_net = max(peak_net, running_net)
        dd = peak_net - running_net
        max_drawdown = max(max_drawdown, dd)
        open_pos_sum += len(sell_tickets) + len(buy_tickets)

    avg_open = open_pos_sum / max(1, len(bars))

    return {
        "realized_closes": realized_closes,
        "realized_net_usd": round(realized_net, 2),
        "max_drawdown": round(max_drawdown, 2),
        "anchor_resets": anchor_resets,
        "avg_open_positions": round(avg_open, 2),
    }


def format_asymmetry(ratio: float) -> str:
    if ratio == 1.0:
        return "1:1"
    elif ratio > 1.0:
        return f"{ratio:.0f}:1"
    else:
        inv = 1.0 / ratio
        return f"1:{inv:.0f}"


def main() -> int:
    if not mt5.initialize():
        print("ERROR: MetaTrader5 initialize() failed")
        return 1

    try:
        # -------------------------------------------------------------------
        # Step 1: Baseline ATR
        # -------------------------------------------------------------------
        print("Computing baseline ATR(14) for GBPUSD M15...")
        recent_bars = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, 100)
        if recent_bars is None or len(recent_bars) == 0:
            print("ERROR: Could not fetch recent bars")
            return 1

        highs = [float(b[2]) for b in recent_bars]
        lows = [float(b[3]) for b in recent_bars]
        closes = [float(b[4]) for b in recent_bars]

        tr_values = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            tr_values.append(tr)

        atr_14 = sum(tr_values[-14:]) / 14.0 if len(tr_values) >= 14 else sum(tr_values) / max(1, len(tr_values))
        baseline_step = atr_14

        print(f"  ATR(14) = {baseline_step:.6f}")
        print()

        # -------------------------------------------------------------------
        # Step 2: Load 7 days of M15 bars
        # -------------------------------------------------------------------
        print(f"Loading {DAYS} days of {SYMBOL} M15 bars...")
        bars = load_m15_bars(SYMBOL, DAYS)
        if not bars:
            print("ERROR: No bars loaded")
            return 1

        print(f"  Loaded {len(bars)} bars")
        print()

        # -------------------------------------------------------------------
        # Step 3: Compute ADX series
        # -------------------------------------------------------------------
        print("Computing ADX series...")
        adx_map = compute_adx_series(bars, period=14)
        print(f"  Computed {len(adx_map)} ADX values")
        print()

        # -------------------------------------------------------------------
        # Step 4: Grid search
        # -------------------------------------------------------------------
        total_combos = len(STEP_MULTIPLIERS) * len(ASYMMETRY_RATIOS) * len(ADX_THRESHOLDS) * len(CLOSE_ALPHAS)
        print(f"Grid: {len(STEP_MULTIPLIERS)} x {len(ASYMMETRY_RATIOS)} x {len(ADX_THRESHOLDS)} x {len(CLOSE_ALPHAS)} = {total_combos} combinations")
        print()

        results = []
        combo_idx = 0
        t_start = time.time()

        for step_mult in STEP_MULTIPLIERS:
            for asym_ratio in ASYMMETRY_RATIOS:
                for adx_thresh in ADX_THRESHOLDS:
                    for alpha in CLOSE_ALPHAS:
                        combo_idx += 1
                        step = baseline_step * step_mult
                        step_sell, step_buy = derive_asymmetric_steps(step, asym_ratio)

                        if combo_idx % 25 == 0 or combo_idx == 1:
                            elapsed = time.time() - t_start
                            rate = combo_idx / elapsed if elapsed > 0 else 0
                            eta = (total_combos - combo_idx) / rate if rate > 0 else 0
                            print(f"  [{combo_idx}/{total_combos}] {step_mult}x asym={format_asymmetry(asym_ratio)} adx={adx_thresh} alpha={alpha} ...", end="", flush=True)

                        try:
                            sim = run_bar_simulation(
                                bars, step, step_sell, step_buy, alpha,
                                adx_thresh, adx_map
                            )

                            closes = sim["realized_closes"]
                            net = sim["realized_net_usd"]
                            per_close = net / max(1, closes)

                            results.append({
                                "step_multiplier": step_mult,
                                "asymmetry_ratio": asym_ratio,
                                "regime_gate_adx_threshold": adx_thresh,
                                "close_alpha": alpha,
                                "step": round(step, 8),
                                "step_sell": round(step_sell, 8),
                                "step_buy": round(step_buy, 8),
                                "realized_closes": closes,
                                "realized_net_usd": net,
                                "per_close": round(per_close, 4),
                                "max_drawdown": sim["max_drawdown"],
                                "anchor_resets": sim["anchor_resets"],
                                "avg_open_positions": sim["avg_open_positions"],
                            })

                            if combo_idx % 25 == 0 or combo_idx == 1:
                                elapsed2 = time.time() - t_start
                                print(f" closes={closes} net=${net:+.2f} $/c=${per_close:+.4f} [{elapsed2:.1f}s]")

                        except Exception as e:
                            if combo_idx % 25 == 0 or combo_idx == 1:
                                print(f" ERROR: {e}")
                            results.append({
                                "step_multiplier": step_mult,
                                "asymmetry_ratio": asym_ratio,
                                "regime_gate_adx_threshold": adx_thresh,
                                "close_alpha": alpha,
                                "error": str(e),
                            })

        elapsed_total = time.time() - t_start
        print(f"\nCompleted {len(results)}/{total_combos} in {elapsed_total:.1f}s ({len(results)/elapsed_total:.1f} combos/s)")

        # -------------------------------------------------------------------
        # Step 5: Analyze
        # -------------------------------------------------------------------
        valid = [r for r in results if "error" not in r]
        if not valid:
            print("ERROR: No valid results")
            return 1

        valid.sort(key=lambda r: r["per_close"], reverse=True)
        top_10 = valid[:10]
        optimal = valid[0]

        # Parameter importance
        def avg_by(param):
            vals, counts = {}, {}
            for r in valid:
                k = str(r[param])
                vals[k] = vals.get(k, 0.0) + r["per_close"]
                counts[k] = counts.get(k, 0) + 1
            return {k: round(vals[k] / counts[k], 4) for k in vals}

        step_imp = avg_by("step_multiplier")
        asym_imp = avg_by("asymmetry_ratio")
        adx_imp = avg_by("regime_gate_adx_threshold")
        alpha_imp = avg_by("close_alpha")

        # Find baseline-like result (step_mult=1.0, asym=2.0, adx=None, alpha=0.5)
        # The integration pipeline used step=0.0002 which is ~0.5x ATR for old ATR
        # With current ATR, 1.0x is more representative of the original intent
        baseline = None
        for r in valid:
            if (abs(r["step_multiplier"] - 1.0) < 0.01 and
                abs(r["asymmetry_ratio"] - 2.0) < 0.01 and
                r["regime_gate_adx_threshold"] is None and
                abs(r["close_alpha"] - 0.5) < 0.01):
                baseline = r
                break

        # -------------------------------------------------------------------
        # Step 6: Save
        # -------------------------------------------------------------------
        reports_dir = REPO.parent / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        results_path = reports_dir / "hungry_hippo_gbpusd_tuning_results.json"

        output = {
            "generated_at": datetime.now(UTC).isoformat(),
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "days": DAYS,
            "bars_processed": len(bars),
            "baseline_atr": round(baseline_step, 8),
            "grid": {
                "step_multipliers": STEP_MULTIPLIERS,
                "asymmetry_ratios": ASYMMETRY_RATIOS,
                "adx_thresholds": [str(t) if t is not None else "None" for t in ADX_THRESHOLDS],
                "close_alphas": CLOSE_ALPHAS,
                "total_combinations": total_combos,
                "valid_results": len(valid),
            },
            "all_results": valid,
            "top_10": top_10,
            "optimal": optimal,
            "baseline": baseline,
            "parameter_importance": {
                "step_multiplier": step_imp,
                "asymmetry_ratio": asym_imp,
                "regime_gate_adx_threshold": adx_imp,
                "close_alpha": alpha_imp,
            },
        }

        with open(results_path, "w") as f:
            json.dump(output, f, indent=2)

        print(f"\nResults saved to: {results_path}")

        # -------------------------------------------------------------------
        # Step 7: Print table
        # -------------------------------------------------------------------
        print()
        print("=" * 115)
        print("TUNING SWEEP SUMMARY — TOP 10 BY $/CLOSE")
        print("=" * 115)
        print(f"{'RANK':>4}  {'STEPx':>6}  {'ASYM':>5}  {'ADX':>5}  {'ALPHA':>5}  {'CLOSES':>6}  {'NET$':>9}  {'$/CLOSE':>8}  {'RESETS':>6}  {'AVG_OPN':>7}  {'DD$':>9}")
        print("-" * 115)

        for rank, r in enumerate(top_10, 1):
            sm = f"{r['step_multiplier']:.2f}x"
            asym = format_asymmetry(r['asymmetry_ratio'])
            adx = str(r['regime_gate_adx_threshold']) if r['regime_gate_adx_threshold'] is not None else "None"
            al = f"{r['close_alpha']:.1f}"
            cl = str(r['realized_closes'])
            net = f"${r['realized_net_usd']:+.2f}"
            pc = f"${r['per_close']:+.4f}"
            rs = str(r['anchor_resets'])
            ao = f"{r['avg_open_positions']:.1f}"
            dd = f"-${r['max_drawdown']:.2f}"
            print(f"{rank:>4}  {sm:>6}  {asym:>5}  {adx:>5}  {al:>5}  {cl:>6}  {net:>9}  {pc:>8}  {rs:>6}  {ao:>7}  {dd:>9}")

        print("-" * 115)
        if baseline:
            bl_asym = format_asymmetry(baseline['asymmetry_ratio'])
            print(f"  Baseline ref: step=1.00x asym={bl_asym} adx=None alpha=0.5  closes={baseline['realized_closes']}  net=${baseline['realized_net_usd']:+.2f}  $/close=${baseline['per_close']:+.4f}")

        print()
        print("=" * 115)
        print("OPTIMAL CONFIGURATION")
        print("=" * 115)
        print(f"  Step multiplier:   {optimal['step_multiplier']}x  (absolute step = {optimal['step']:.6f})")
        print(f"  Asymmetry:         {format_asymmetry(optimal['asymmetry_ratio'])}  (sell={optimal['step_sell']:.6f}, buy={optimal['step_buy']:.6f})")
        print(f"  ADX gate:          {optimal['regime_gate_adx_threshold']}")
        print(f"  Close alpha:       {optimal['close_alpha']}")
        print(f"  Realized closes:   {optimal['realized_closes']}")
        print(f"  Net PnL:           ${optimal['realized_net_usd']:+.2f}")
        print(f"  $/close:           ${optimal['per_close']:+.4f}")
        print(f"  Max drawdown:      ${optimal['max_drawdown']:.2f}")
        print(f"  Anchor resets:     {optimal['anchor_resets']}")
        print(f"  Avg open positions:{optimal['avg_open_positions']:.1f}")

        print()
        print("=" * 115)
        print("PARAMETER IMPORTANCE (avg $/close by value)")
        print("=" * 115)

        print("\n  Step multiplier:")
        for k, v in sorted(step_imp.items(), key=lambda x: x[1], reverse=True):
            marker = " <--" if k == str(optimal['step_multiplier']) else ""
            print(f"    {k:>5}x: ${v:+.4f}{marker}")

        print("\n  Asymmetry ratio:")
        for k, v in sorted(asym_imp.items(), key=lambda x: x[1], reverse=True):
            marker = " <--" if k == str(optimal['asymmetry_ratio']) else ""
            print(f"    {k:>5}: ${v:+.4f}{marker}")

        print("\n  ADX gate threshold:")
        for k, v in sorted(adx_imp.items(), key=lambda x: x[1], reverse=True):
            marker = " <--" if k == str(optimal['regime_gate_adx_threshold']) else ""
            print(f"    {k:>5}: ${v:+.4f}{marker}")

        print("\n  Close alpha:")
        for k, v in sorted(alpha_imp.items(), key=lambda x: x[1], reverse=True):
            marker = " <--" if k == str(optimal['close_alpha']) else ""
            print(f"    {k:>4}: ${v:+.4f}{marker}")

        # Key insights
        print()
        print("=" * 115)
        print("KEY INSIGHTS")
        print("=" * 115)

        best_step = max(step_imp.items(), key=lambda x: x[1])
        best_asym = max(asym_imp.items(), key=lambda x: x[1])
        best_adx = max(adx_imp.items(), key=lambda x: x[1])
        best_alpha = max(alpha_imp.items(), key=lambda x: x[1])

        print(f"  Best step multiplier:  {best_step[0]}x (avg ${best_step[1]:+.4f}/close)")
        print(f"  Best asymmetry:        {best_asym[0]} (avg ${best_asym[1]:+.4f}/close)")
        print(f"  Best ADX gate:         {best_adx[0]} (avg ${best_adx[1]:+.4f}/close)")
        print(f"  Best close alpha:      {best_alpha[0]} (avg ${best_alpha[1]:+.4f}/close)")

        # Spread analysis
        best_per_close = optimal['per_close']
        worst_per_close = valid[-1]['per_close']
        spread = best_per_close - worst_per_close
        print(f"\n  Best vs worst $/close spread: ${spread:+.4f}")
        print(f"  Profitable configs: {sum(1 for r in valid if r['per_close'] > 0)} / {len(valid)} ({100*sum(1 for r in valid if r['per_close'] > 0)/len(valid):.0f}%)")

        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
