#!/usr/bin/env python3
"""BTCUSD H1 fidelity sweep — spread + slippage injection model.

Tests whether the gap between backtest ($250K) and live (-$914) is explained
by simulation fidelity: variable spread, entry/exit slippage, and their combination.

Scenarios:
  Baseline       — static spread, no slippage (current backtest)
  VariableSpread — ATR-scaled spread, 1-5 pip range
  EntrySlip      — 0.5-2 pip random slippage on entries, scaled by volume
  ExitSlip       — 0.3-1 pip random slippage on exits
  Combined       — variable spread + entry slip + exit slip

Spread model:
  spread = base_spread + volatility_factor * (high - low) / close
  base_spread = 1.0 pip, volatility_factor = 0.1
  Clamped to [1.0, 5.0] pips

Slippage model:
  Entry slip = random_uniform(0.5, 2.0) * point, scaled 1.5x on high-volume bars
  Exit slip  = random_uniform(0.3, 1.0) * point
  Applied AGAINST position (BUY fills higher, SELL fills lower)
"""
from __future__ import annotations

import csv
import random
import statistics
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "BTCUSD"
DAYS = 120
TIMEFRAME = mt5.TIMEFRAME_H1
VOLUME = 0.01  # matches penetration_lattice_lab_v2

# Fixed lattice params
MAX_OPEN_PER_SIDE = 50
CLOSE_GAP = 2
ALPHA = 1.0
REARM_VARIANT = "lvl2_exc2"
STEP = 50.0  # backtest-optimal step

# Spread model params
BASE_SPREAD_PIPS = 1.0
VOLATILITY_FACTOR = 0.1
MIN_SPREAD_PIPS = 1.0
MAX_SPREAD_PIPS = 5.0

# Slippage params
ENTRY_SLIP_MIN_PIPS = 0.5
ENTRY_SLIP_MAX_PIPS = 2.0
EXIT_SLIP_MIN_PIPS = 0.3
EXIT_SLIP_MAX_PIPS = 1.0
HIGH_VOL_MULTIPLIER = 1.5
HIGH_VOL_THRESHOLD = 1.5  # bars with volume > 1.5x median are "high volume"


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


def load_h1_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 24 * days)
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


def compute_volume_median(bars: list[dict]) -> float:
    if not bars:
        return 0.0
    vols = [b["tick_volume"] for b in bars]
    return float(statistics.median(vols))


def pips_to_price(point: float, pips: float) -> float:
    """Convert pip count to price units. BTCUSD: 1 pip = 10 * point (0.1)."""
    return pips * point * 10.0


def compute_variable_spread(bar: dict, point: float) -> float:
    """ATR-based spread: wider on volatile bars."""
    bar_range = bar["high"] - bar["low"]
    vol_ratio = bar_range / bar["close"] if bar["close"] > 0 else 0.0
    spread_pips = BASE_SPREAD_PIPS + VOLATILITY_FACTOR * vol_ratio * (bar["close"] / (point * 10.0))
    spread_pips = max(MIN_SPREAD_PIPS, min(MAX_SPREAD_PIPS, spread_pips))
    return pips_to_price(point, spread_pips)


def compute_entry_slippage(bar: dict, point: float, vol_median: float) -> float:
    """Random entry slippage, scaled by volume."""
    base_pips = random.uniform(ENTRY_SLIP_MIN_PIPS, ENTRY_SLIP_MAX_PIPS)
    if vol_median > 0 and bar["tick_volume"] > HIGH_VOL_THRESHOLD * vol_median:
        base_pips *= HIGH_VOL_MULTIPLIER
    return pips_to_price(point, base_pips)


def compute_exit_slippage(point: float) -> float:
    """Random exit slippage."""
    pips = random.uniform(EXIT_SLIP_MIN_PIPS, EXIT_SLIP_MAX_PIPS)
    return pips_to_price(point, pips)


def sim_with_fidelity(
    bars: list[dict],
    info,
    step: float,
    mop: int,
    gap: int,
    alpha: float,
    scenario: str,
    seed: int | None = None,
) -> dict:
    """H1 penetration lattice with configurable spread + slippage fidelity."""
    if seed is not None:
        random.seed(seed)

    if not bars:
        return {}

    point = float(info.point or 1.0)
    vol_median = compute_volume_median(bars)

    # Static spread for baseline and scenarios that don't use variable spread
    static_spread = spread_price(info)

    anchor = bars[0]["close"]
    ns = anchor + step
    nb = anchor - step
    tk: list[Ticket] = []
    rl: list[float] = []
    churn: list[ChurnTicket] = []
    crl: list[float] = []

    all_trade_pnls: list[float] = []
    equity_curve = [0.0]

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        # Determine spread for this bar
        if scenario in ("VariableSpread", "Combined"):
            spread_px = compute_variable_spread(bar, point)
        else:
            spread_px = static_spread

        # Lattice entries
        while bar["high"] >= ns and os_ < mop:
            entry_px = ns
            if scenario in ("EntrySlip", "Combined"):
                slip = compute_entry_slippage(bar, point, vol_median)
                # SELL entry: worse fill = lower price (sell cheaper)
                entry_px = ns - slip
            tk.append(Ticket(direction="SELL", entry_price=entry_px, opened_idx=idx))
            os_ += 1
            ns += step
        while bar["low"] <= nb and ob < mop:
            entry_px = nb
            if scenario in ("EntrySlip", "Combined"):
                slip = compute_entry_slippage(bar, point, vol_median)
                # BUY entry: worse fill = higher price (buy more expensive)
                entry_px = nb + slip
            tk.append(Ticket(direction="BUY", entry_price=entry_px, opened_idx=idx))
            ob += 1
            nb -= step

        # Lattice closes (lvl2_exc2: gap-based penetration)
        closed = []
        sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > gap and bar["low"] <= sl[gap].entry_price:
            o = sl[0]
            r = sl[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            if scenario in ("ExitSlip", "Combined"):
                slip = compute_exit_slippage(point)
                # SELL exit: filled higher than expected = worse
                close_px += slip
            pnl = unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px)
            rl.append(pnl)
            all_trade_pnls.append(pnl)
            closed.append(("SELL", o.entry_price))
            tk.remove(o)
            sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]
            r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            if scenario in ("ExitSlip", "Combined"):
                slip = compute_exit_slippage(point)
                # BUY exit: filled lower than expected = worse
                close_px -= slip
            pnl = unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px)
            rl.append(pnl)
            all_trade_pnls.append(pnl)
            closed.append(("BUY", o.entry_price))
            tk.remove(o)
            bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Anchor reset when flat and price moves >= step
        if not tk and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            ns = anchor + step
            nb = anchor - step

        # Rearm churn entries at closed levels
        cos_ = sum(1 for t in churn if t.direction == "SELL")
        cob = sum(1 for t in churn if t.direction == "BUY")
        for d, cp in closed:
            c = cos_ if d == "SELL" else cob
            if c >= mop:
                continue
            # Churn entries also get entry slippage
            churn_entry_px = cp
            if scenario in ("EntrySlip", "Combined"):
                slip = compute_entry_slippage(bar, point, vol_median)
                if d == "SELL":
                    churn_entry_px = cp - slip  # SELL: worse = lower
                else:
                    churn_entry_px = cp + slip  # BUY: worse = higher
            churn.append(ChurnTicket(d, churn_entry_px, idx))
            if d == "SELL":
                cos_ += 1
            else:
                cob += 1

        # Churn closes
        cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            o = cs[0]
            r = cs[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            if scenario in ("ExitSlip", "Combined"):
                slip = compute_exit_slippage(point)
                close_px += slip
            pnl = unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px)
            crl.append(pnl)
            all_trade_pnls.append(pnl)
            churn.remove(o)
            cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]
            r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            if scenario in ("ExitSlip", "Combined"):
                slip = compute_exit_slippage(point)
                close_px -= slip
            pnl = unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px)
            crl.append(pnl)
            all_trade_pnls.append(pnl)
            churn.remove(o)
            cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Equity curve
        fl_now = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bar["close"], spread_px) for t in tk]
        cfl_now = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bar["close"], spread_px) for t in churn]
        equity_curve.append(sum(rl) + sum(crl) + sum(fl_now) + sum(cfl_now))

    # Floating PnL at end
    final_spread = static_spread
    fl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], final_spread) for t in tk]
    cfl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], final_spread) for t in churn]

    realized_pnl = sum(rl) + sum(crl)
    floating_pnl = sum(fl) + sum(cfl)
    combined_pnl = realized_pnl + floating_pnl

    total_trades = len(all_trade_pnls)
    winning_trades = sum(1 for p in all_trade_pnls if p > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    return {
        "scenario": scenario,
        "realized_pnl": realized_pnl,
        "floating_pnl": floating_pnl,
        "combined_pnl": combined_pnl,
        "lattice_realized": sum(rl),
        "churn_realized": sum(crl),
        "lattice_floating": sum(fl),
        "churn_floating": sum(cfl),
        "closes": len(rl) + len(crl),
        "lattice_closes": len(rl),
        "rearm_opens": len(crl),
        "lattice_tickets_left": len(tk),
        "churn_tickets_left": len(churn),
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": total_trades - winning_trades,
        "max_drawdown": round(max_dd, 2),
        "avg_trade_pnl": round(sum(all_trade_pnls) / total_trades, 2) if total_trades > 0 else 0.0,
    }


def run_scenario_multiple_times(
    bars: list[dict],
    info,
    scenario: str,
    n_runs: int = 20,
) -> list[dict]:
    """Run a stochastic scenario multiple times and return all results."""
    results = []
    for i in range(n_runs):
        r = sim_with_fidelity(bars, info, STEP, MAX_OPEN_PER_SIDE, CLOSE_GAP, ALPHA, scenario, seed=i * 7 + 13)
        if r:
            results.append(r)
    return results


def summarize_scenario(scenario: str, results: list[dict]) -> dict:
    """Compute mean/median/std across multiple runs."""
    if not results:
        return {"scenario": scenario, "error": "no results"}

    combined = [r["combined_pnl"] for r in results]
    realized = [r["realized_pnl"] for r in results]
    wr = [r["win_rate"] for r in results]
    trades = [r["total_trades"] for r in results]
    dd = [r["max_drawdown"] for r in results]
    avg_pnl = [r["avg_trade_pnl"] for r in results]

    return {
        "scenario": scenario,
        "combined_mean": round(statistics.mean(combined), 2),
        "combined_median": round(statistics.median(combined), 2),
        "combined_std": round(statistics.stdev(combined), 2) if len(combined) > 1 else 0.0,
        "combined_min": round(min(combined), 2),
        "combined_max": round(max(combined), 2),
        "realized_mean": round(statistics.mean(realized), 2),
        "win_rate_mean": round(statistics.mean(wr), 1),
        "trades_mean": round(statistics.mean(trades), 0),
        "max_dd_mean": round(statistics.mean(dd), 2),
        "avg_trade_pnl_mean": round(statistics.mean(avg_pnl), 2),
        "n_runs": len(results),
    }


def main() -> int:
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"{SYMBOL} symbol info unavailable")
        mt5.shutdown()
        return 1

    bars = load_h1_bars(SYMBOL, DAYS)
    if not bars:
        print(f"No H1 bars loaded for {SYMBOL} ({DAYS} days)")
        mt5.shutdown()
        return 1

    point = float(info.point or 1.0)
    static_spread = spread_price(info)
    static_spread_pips = static_spread / (point * 10.0)

    print(f"\n{'='*120}")
    print(f"  BTCUSD H1 FIDELITY SWEEP — {len(bars)} bars, {DAYS} days, step=${STEP:.0f}")
    print(f"  Static spread: {static_spread_pips:.1f} pips")
    print(f"  Point: {point}, 1 pip = {point * 10.0}")
    print(f"  Spread model: {BASE_SPREAD_PIPS}-{MAX_SPREAD_PIPS} pips, vol_factor={VOLATILITY_FACTOR}")
    print(f"  Entry slip: {ENTRY_SLIP_MIN_PIPS}-{ENTRY_SLIP_MAX_PIPS} pips, {HIGH_VOL_MULTIPLIER}x on high vol")
    print(f"  Exit slip: {EXIT_SLIP_MIN_PIPS}-{EXIT_SLIP_MAX_PIPS} pips")
    print(f"{'='*120}")

    scenarios = ["Baseline", "VariableSpread", "EntrySlip", "ExitSlip", "Combined"]
    all_summaries = []

    for scenario in scenarios:
        is_stochastic = scenario != "Baseline"
        if is_stochastic:
            results = run_scenario_multiple_times(bars, info, scenario, n_runs=20)
            summary = summarize_scenario(scenario, results)
        else:
            r = sim_with_fidelity(bars, info, STEP, MAX_OPEN_PER_SIDE, CLOSE_GAP, ALPHA, scenario, seed=None)
            summary = {
                "scenario": scenario,
                "combined_mean": round(r["combined_pnl"], 2),
                "combined_median": round(r["combined_pnl"], 2),
                "combined_std": 0.0,
                "combined_min": round(r["combined_pnl"], 2),
                "combined_max": round(r["combined_pnl"], 2),
                "realized_mean": round(r["realized_pnl"], 2),
                "win_rate_mean": r["win_rate"],
                "trades_mean": float(r["total_trades"]),
                "max_dd_mean": r["max_drawdown"],
                "avg_trade_pnl_mean": r["avg_trade_pnl"],
                "n_runs": 1,
            }
        all_summaries.append(summary)

    # Print comparison table
    print(f"\n{'Scenario':<18}  {'Combined Mean':>16}  {'Median':>12}  {'Std':>10}  {'Range':>20}  {'Win%':>6}  {'Trades':>7}  {'MaxDD':>12}  {'AvgPnL':>10}")
    print("-" * 130)
    for s in all_summaries:
        range_str = f"[{s['combined_min']:>+,.0f},{s['combined_max']:>+,.0f}]"
        marker = ""
        if s["scenario"] == "Baseline":
            marker = " <-- CURRENT BT"
        elif s["scenario"] == "Combined":
            marker = " <-- MOST REALISTIC"
        print(
            f"{s['scenario']:<18}  ${s['combined_mean']:>15,.2f}  ${s['combined_median']:>11,.2f}  "
            f"±{s['combined_std']:>9,.2f}  {range_str:>20}  {s['win_rate_mean']:>5.1f}%  "
            f"{s['trades_mean']:>7.0f}  ${s['max_dd_mean']:>11,.2f}  ${s['avg_trade_pnl_mean']:>9,.2f}{marker}"
        )

    # Live comparison
    live_pnl = -914.0
    bt_pnl = all_summaries[0]["combined_mean"]
    combined_pnl = next((s["combined_mean"] for s in all_summaries if s["scenario"] == "Combined"), 0.0)

    print(f"\n{'='*120}")
    print(f"  GAP ANALYSIS")
    print(f"  {'='*120}")
    print(f"  Backtest baseline: ${bt_pnl:>12,.2f}")
    print(f"  Live trading:      ${live_pnl:>12,.2f}")
    print(f"  Gap:               ${bt_pnl - live_pnl:>12,.2f}")
    print(f"  Combined model:    ${combined_pnl:>12,.2f}")
    if combined_pnl != 0.0:
        residual = combined_pnl - live_pnl
        explained = (bt_pnl - combined_pnl) / (bt_pnl - live_pnl) * 100 if (bt_pnl - live_pnl) != 0 else 0.0
        print(f"  Residual gap:      ${residual:>12,.2f}")
        print(f"  Fidelity explains: {explained:>11.1f}% of the gap")

    # Scenario delta breakdown
    print(f"\n  SCENARIO DELTAS vs Baseline:")
    print(f"  {'Scenario':<18}  {'Delta':>14}  {'% of Gap':>10}")
    print(f"  {'-'*50}")
    for s in all_summaries[1:]:
        delta = s["combined_mean"] - bt_pnl
        pct = delta / (bt_pnl - live_pnl) * 100 if (bt_pnl - live_pnl) != 0 else 0.0
        print(f"  {s['scenario']:<18}  ${delta:>13,.2f}  {pct:>9.1f}%")

    # Spread statistics for variable spread scenario
    vs_results = run_scenario_multiple_times(bars, info, "VariableSpread", n_runs=3)
    if vs_results:
        print(f"\n  VARIABLE SPREAD STATISTICS (sample of 3 runs):")
        print(f"  {'Run':>4}  {'Avg Spread (pips)':>20}  {'Min Spread':>12}  {'Max Spread':>12}")
        print(f"  {'-'*55}")
        for i, r in enumerate(vs_results):
            # We don't track per-bar spread in results, but we can note the model range
            print(f"  {i+1:>4}  {'(modeled 1.0-5.0 pip range, scaled by bar volatility)':>50}")

    # Write CSV
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = reports_dir / "btcusd_fidelity_sweep.csv"
    fieldnames = [
        "scenario", "combined_mean", "combined_median", "combined_std",
        "combined_min", "combined_max", "realized_mean",
        "win_rate_mean", "trades_mean", "max_dd_mean", "avg_trade_pnl_mean", "n_runs",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_summaries)
    print(f"\nWrote {csv_path}")

    # Write detailed JSONL with per-run data
    # Reuse already-computed results instead of re-running
    jsonl_path = reports_dir / "btcusd_fidelity_sweep_details.jsonl"
    import json

    # Collect all per-run results
    all_run_results = []
    for scenario in scenarios:
        is_stochastic = scenario != "Baseline"
        if is_stochastic:
            results = run_scenario_multiple_times(bars, info, scenario, n_runs=20)
        else:
            r = sim_with_fidelity(bars, info, STEP, MAX_OPEN_PER_SIDE, CLOSE_GAP, ALPHA, scenario, seed=None)
            results = [r] if r else []
        all_run_results.extend(results)

    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in all_run_results:
            r_out = {k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()}
            f.write(json.dumps(r_out) + "\n")
    print(f"Wrote {jsonl_path}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
