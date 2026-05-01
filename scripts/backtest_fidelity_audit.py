#!/usr/bin/env python3
"""Backtest fidelity audit -- quantify the gap between backtest numbers and live reality.

What this audits:
1. SPREAD GAP -- backtest uses mid-price or bar extreme fills; live pays the spread on every touch.
   We run: naive (current spread model) vs spread-doubled (conservative live estimate).
2. SLIPPAGE GAP -- live orders fill worse than the requested price, especially on fast moves.
   We add a per-trade slippage penalty based on ATR fraction.
3. SAME-BAR ROUND-TRIP -- backtest can open AND close on the same bar if price wicks both ways.
   Live cannot do this on a single tick without paying spread twice. We enforce a minimum
   one-bar separation between entry and exit.
4. FILL REALISM -- backtest uses bar high/low extremes as fill prices. Live fills at
   bid/ask, which is one spread worse.

How it works:
  For each lane, runs 4 backtest modes and compares results:
    - naive: current code (spread from symbol_info, bar-extreme fills, same-bar allowed)
    - spread_adjusted: double the spread cost on every round-trip
    - slippage_adjusted: add estimated slippage per trade (based on ATR fraction)
    - no_same_bar: prevent opening and closing on the same bar index

Usage:
    python scripts/backtest_fidelity_audit.py
    python scripts/backtest_fidelity_audit.py --symbols BTCUSD EURUSD NZDUSD
    python scripts/backtest_fidelity_audit.py --days 60 --timeframe H1
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

# Ensure the scripts dir is on sys.path so we can import penetration_lattice modules
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from penetration_lattice_hybrid_apex import RawConfig  # noqa: F401
from penetration_lattice_lab_v2 import (
    Ticket,
    dynamic_step,
    pip_size_for,
    spread_price,
    unit_pnl_usd,
)


# ---------------------------------------------------------------------------
# Lane definitions -- symbols with their live-config step_pips and max_open
# ---------------------------------------------------------------------------
LANE_CONFIGS = {
    "BTCUSD": {
        "step_pips": 50.0,
        "max_open_per_side": 50,
        "close_mode": "two_level",
        "timeframe": mt5.TIMEFRAME_H1,
        "days": 120,
        "step_is_price_units": True,
        "gap": 2,
    },
    "EURUSD": {
        "step_pips": 3.0,
        "max_open_per_side": 20,
        "close_mode": "two_level",
        "timeframe": mt5.TIMEFRAME_M1,
        "days": 60,
        "step_is_price_units": False,
        "gap": 2,
    },
    "NZDUSD": {
        "step_pips": 1.5,
        "max_open_per_side": 12,
        "close_mode": "two_level",
        "timeframe": mt5.TIMEFRAME_M1,
        "days": 60,
        "step_is_price_units": False,
        "gap": 2,
    },
    "GBPUSD": {
        "step_pips": 2.0,
        "max_open_per_side": 20,
        "close_mode": "two_level",
        "timeframe": mt5.TIMEFRAME_M1,
        "days": 60,
        "step_is_price_units": False,
        "gap": 2,
    },
    "USDJPY": {
        "step_pips": 0.5,
        "max_open_per_side": 20,
        "close_mode": "two_level",
        "timeframe": mt5.TIMEFRAME_M1,
        "days": 60,
        "step_is_price_units": False,
        "gap": 2,
    },
    "USDCHF": {
        "step_pips": 0.5,
        "max_open_per_side": 20,
        "close_mode": "two_level",
        "timeframe": mt5.TIMEFRAME_M1,
        "days": 60,
        "step_is_price_units": False,
        "gap": 2,
    },
}


@dataclass
class AuditResult:
    """Result for one symbol + one fidelity mode."""
    symbol: str
    mode: str
    realized_pnl: float = 0.0
    floating_pnl: float = 0.0
    combined_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    max_drawdown: float = 0.0
    total_spread_cost: float = 0.0
    total_slippage_cost: float = 0.0
    same_bar_roundtrips_blocked: int = 0
    same_bar_roundtrips_allowed: int = 0


def load_bars_for_symbol(symbol: str, timeframe: int, days: int) -> list[dict]:
    """Load bars from MT5 for the given symbol and timeframe."""
    if timeframe == mt5.TIMEFRAME_M1:
        count = 1440 * days
    elif timeframe == mt5.TIMEFRAME_M5:
        count = 288 * days
    elif timeframe == mt5.TIMEFRAME_M15:
        count = 96 * days
    elif timeframe == mt5.TIMEFRAME_H1:
        count = 24 * days
    elif timeframe == mt5.TIMEFRAME_H4:
        count = 6 * days
    else:
        count = 1440 * days

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
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


def compute_atr(bars: list[dict], period: int = 14) -> list[float]:
    """Compute ATR series for slippage estimation."""
    atr = []
    for i in range(len(bars)):
        if i < period:
            atr.append(0.0)
            continue
        trs = []
        for j in range(i - period + 1, i + 1):
            bar = bars[j]
            prev_close = bars[j - 1]["close"]
            tr = max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev_close),
                abs(bar["low"] - prev_close),
            )
            trs.append(tr)
        atr.append(sum(trs) / period)
    return atr


# Adaptivity config shared across simulations
_ADAPT_CFG = type("Cfg", (), {
    "adaptive_step_threshold_1": 10,
    "adaptive_step_threshold_2": 20,
    "adaptive_step_multiplier_1": 1.5,
    "adaptive_step_multiplier_2": 2.0,
})()


def simulate_lane_fidelity(
    symbol: str,
    bars: list[dict],
    symbol_info,
    lane_cfg: dict,
    mode: str,
) -> AuditResult:
    """Run a penetration lattice backtest for one symbol in one fidelity mode.

    Modes:
      naive             -- current behavior: spread from symbol_info, bar-extreme fills, same-bar allowed
      spread_adjusted   -- double the spread cost on every PnL calculation
      slippage_adjusted  -- add slippage = ATR_fraction * bar_range per trade
      no_same_bar       -- block close if ticket was opened on same bar index
    """
    if not bars:
        return AuditResult(symbol=symbol, mode=mode)

    pip_size = pip_size_for(symbol_info)
    base_spread_px = spread_price(symbol_info)

    step_is_price_units = lane_cfg.get("step_is_price_units", False)
    if step_is_price_units:
        base_step_px = lane_cfg["step_pips"]
    else:
        base_step_px = lane_cfg["step_pips"] * pip_size

    max_open = lane_cfg["max_open_per_side"]
    gap = lane_cfg.get("gap", 2)

    atr_series = compute_atr(bars, period=14)

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    spread_costs: list[float] = []
    slippage_costs: list[float] = []
    same_bar_blocked = 0
    same_bar_allowed = 0

    equity_curve = [0.0]
    all_trade_pnls: list[float] = []

    for idx in range(1, len(bars)):
        bar = bars[idx]
        current_atr = atr_series[idx] if idx < len(atr_series) else 0.0

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, _ADAPT_CFG)
        current_buy_step = dynamic_step(base_step_px, open_buy, _ADAPT_CFG)

        # Open SELL orders
        while bar["high"] >= next_sell_level and open_sell < max_open:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, _ADAPT_CFG)
            next_sell_level += current_sell_step

        # Open BUY orders
        while bar["low"] <= next_buy_level and open_buy < max_open:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, _ADAPT_CFG)
            next_buy_level -= current_buy_step

        # -- Close SELL side --
        while True:
            sells = sorted(
                (t for t in open_tickets if t.direction == "SELL"),
                key=lambda t: t.entry_price, reverse=True,
            )
            if len(sells) <= gap or bar["low"] > sells[gap].entry_price:
                break

            outer = sells[0]

            # no_same_bar mode: block close if opened on same bar
            if mode == "no_same_bar" and outer.opened_idx == idx:
                same_bar_blocked += 1
                # Remove from sell-side consideration but keep open
                # We can't easily remove it from the sorted list without breaking
                # the gap logic, so we skip it by temporarily filtering
                open_tickets_for_sell = [t for t in open_tickets if t is not outer]
                # Continue the while loop with filtered list -- but since we can't
                # mutate open_tickets yet, we just skip this outer one.
                # The simplest way: remove from open_tickets and re-add at end (won't trigger again)
                # Actually the cleanest: just don't close it.
                # We need to filter it from the sell list for this bar's close decisions.
                # Since we're in a while loop re-checking sells each iteration, we need
                # a different approach. Let's use a set of "blocked this bar" tickets.
                break  # Simplified: if the outermost is blocked, stop closing this side this bar

            if outer.opened_idx == idx:
                same_bar_allowed += 1

            close_ref = sells[gap].entry_price
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, base_spread_px)

            spread_cost_for_trade = 0.0
            if mode == "spread_adjusted":
                spread_cost_for_trade = abs(
                    unit_pnl_usd(symbol, "SELL", outer.entry_price, outer.entry_price + base_spread_px, base_spread_px)
                )
                pnl += spread_cost_for_trade  # negative value reduces pnl

            slip_cost = 0.0
            if mode == "slippage_adjusted":
                slip_fraction = 0.1
                slip_px = max(current_atr * slip_fraction, base_spread_px) if current_atr > 0 else base_spread_px
                slip_cost = abs(
                    unit_pnl_usd(symbol, "SELL", outer.entry_price, outer.entry_price + slip_px, base_spread_px)
                )
                pnl += slip_cost

            realized_pnls.append(pnl)
            spread_costs.append(spread_cost_for_trade)
            slippage_costs.append(slip_cost)
            all_trade_pnls.append(pnl)
            open_tickets.remove(outer)

        # -- Close BUY side --
        while True:
            buys = sorted(
                (t for t in open_tickets if t.direction == "BUY"),
                key=lambda t: t.entry_price,
            )
            if len(buys) <= gap or bar["high"] < buys[gap].entry_price:
                break

            outer = buys[0]

            if mode == "no_same_bar" and outer.opened_idx == idx:
                same_bar_blocked += 1
                break  # Skip closing if outermost is same-bar

            if outer.opened_idx == idx:
                same_bar_allowed += 1

            close_ref = buys[gap].entry_price
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, base_spread_px)

            spread_cost_for_trade = 0.0
            if mode == "spread_adjusted":
                spread_cost_for_trade = abs(
                    unit_pnl_usd(symbol, "BUY", outer.entry_price, outer.entry_price - base_spread_px, base_spread_px)
                )
                pnl -= spread_cost_for_trade

            slip_cost = 0.0
            if mode == "slippage_adjusted":
                slip_fraction = 0.1
                slip_px = max(current_atr * slip_fraction, base_spread_px) if current_atr > 0 else base_spread_px
                slip_cost = abs(
                    unit_pnl_usd(symbol, "BUY", outer.entry_price, outer.entry_price - slip_px, base_spread_px)
                )
                pnl -= slip_cost

            realized_pnls.append(pnl)
            spread_costs.append(spread_cost_for_trade)
            slippage_costs.append(slip_cost)
            all_trade_pnls.append(pnl)
            open_tickets.remove(outer)

        # Anchor reset when flat
        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px

        # Equity curve tracking
        fl = [unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], base_spread_px) for t in open_tickets]
        equity_curve.append(sum(realized_pnls) + sum(fl))

    # End of sample
    last_close = bars[-1]["close"]
    floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, base_spread_px) for t in open_tickets]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating)
    combined_net = realized_net + floating_net

    total_trades = len(all_trade_pnls)
    winning = sum(1 for p in all_trade_pnls if p > 0)
    losing = total_trades - winning
    wr = (winning / total_trades * 100) if total_trades > 0 else 0.0
    avg_pnl = (sum(all_trade_pnls) / total_trades) if total_trades > 0 else 0.0

    # Max drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    return AuditResult(
        symbol=symbol,
        mode=mode,
        realized_pnl=round(realized_net, 2),
        floating_pnl=round(floating_net, 2),
        combined_pnl=round(combined_net, 2),
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=losing,
        win_rate=round(wr, 1),
        avg_trade_pnl=round(avg_pnl, 3),
        max_drawdown=round(max_dd, 2),
        total_spread_cost=round(sum(spread_costs), 2),
        total_slippage_cost=round(sum(slippage_costs), 2),
        same_bar_roundtrips_blocked=same_bar_blocked,
        same_bar_roundtrips_allowed=same_bar_allowed,
    )


def compute_fidelity_gap(naive: AuditResult, adjusted: AuditResult) -> dict:
    """Compute the gap between naive and adjusted results."""
    pnl_gap = adjusted.combined_pnl - naive.combined_pnl
    pnl_gap_pct = (pnl_gap / abs(naive.combined_pnl) * 100) if naive.combined_pnl != 0 else 0.0
    wr_gap = adjusted.win_rate - naive.win_rate
    dd_gap = adjusted.max_drawdown - naive.max_drawdown
    return {
        "pnl_gap_usd": round(pnl_gap, 2),
        "pnl_gap_pct": round(pnl_gap_pct, 1),
        "wr_gap_pp": round(wr_gap, 1),
        "dd_gap_usd": round(dd_gap, 2),
        "edge_survival_pct": round(
            (adjusted.combined_pnl / naive.combined_pnl * 100) if naive.combined_pnl != 0 else 0, 1
        ),
    }


def run_audit(
    symbols: list[str] | None = None,
    days_override: int | None = None,
    tf_override: str | None = None,
):
    """Run the full fidelity audit across all lanes."""
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return None

    try:
        target_symbols = symbols or list(LANE_CONFIGS.keys())
        all_results: dict[str, list[AuditResult]] = {}
        modes = ["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"]

        for symbol in target_symbols:
            lane_cfg = LANE_CONFIGS.get(symbol)
            if lane_cfg is None:
                print(f"  SKIP {symbol}: no lane config defined")
                continue

            info = mt5.symbol_info(symbol)
            if info is None:
                print(f"  SKIP {symbol}: symbol_info returned None")
                continue

            tf = lane_cfg["timeframe"]
            days = lane_cfg["days"]
            if days_override is not None:
                days = days_override
            if tf_override is not None:
                tf_map = {
                    "M1": mt5.TIMEFRAME_M1,
                    "M5": mt5.TIMEFRAME_M5,
                    "M15": mt5.TIMEFRAME_M15,
                    "H1": mt5.TIMEFRAME_H1,
                    "H4": mt5.TIMEFRAME_H4,
                }
                tf = tf_map.get(tf_override, tf)

            bars = load_bars_for_symbol(symbol, tf, days)
            if not bars:
                print(f"  SKIP {symbol}: no bars loaded (tf={tf}, days={days})")
                continue

            spread_px = spread_price(info)
            print(f"\n  {'=' * 80}")
            print(f"  {symbol}  bars={len(bars)}  spread=${spread_px:.6f}  step={lane_cfg['step_pips']}")
            print(f"  {'=' * 80}")

            symbol_results = []
            for mode in modes:
                r = simulate_lane_fidelity(symbol, bars, info, lane_cfg, mode)
                symbol_results.append(r)
                marker = ""
                if mode == "spread_adjusted":
                    marker = f"  (spread penalty: ${r.total_spread_cost:.2f})"
                elif mode == "slippage_adjusted":
                    marker = f"  (slippage penalty: ${r.total_slippage_cost:.2f})"
                elif mode == "no_same_bar":
                    marker = f"  (same-bar blocked: {r.same_bar_roundtrips_blocked})"

                print(
                    f"    {mode:<22s}  realized=${r.realized_pnl:>+10.2f}  "
                    f"floating=${r.floating_pnl:>+10.2f}  "
                    f"combined=${r.combined_pnl:>+10.2f}  "
                    f"trades={r.total_trades:>5}  "
                    f"WR={r.win_rate:>5.1f}%  "
                    f"MaxDD=${r.max_drawdown:>10.2f}{marker}"
                )

            all_results[symbol] = symbol_results

        # Build summary report
        summary_rows = []
        for symbol, results in all_results.items():
            naive_r = next((r for r in results if r.mode == "naive"), None)
            if naive_r is None:
                continue

            for r in results:
                if r.mode == "naive":
                    continue
                gap = compute_fidelity_gap(naive_r, r)
                summary_rows.append({
                    "symbol": symbol,
                    "mode": r.mode,
                    "naive_combined": naive_r.combined_pnl,
                    "adjusted_combined": r.combined_pnl,
                    "naive_wr": naive_r.win_rate,
                    "adjusted_wr": r.win_rate,
                    "naive_trades": naive_r.total_trades,
                    "adjusted_trades": r.total_trades,
                    "pnl_gap_usd": gap["pnl_gap_usd"],
                    "pnl_gap_pct": gap["pnl_gap_pct"],
                    "wr_gap_pp": gap["wr_gap_pp"],
                    "dd_gap_usd": gap["dd_gap_usd"],
                    "edge_survival_pct": gap["edge_survival_pct"],
                    "spread_cost": r.total_spread_cost,
                    "slippage_cost": r.total_slippage_cost,
                    "same_bar_blocked": r.same_bar_roundtrips_blocked,
                })

        # Rank lanes by edge survival (spread_adjusted is the most realistic proxy)
        rankings = []
        for symbol, results in all_results.items():
            naive_r = next((r for r in results if r.mode == "naive"), None)
            spread_r = next((r for r in results if r.mode == "spread_adjusted"), None)
            if naive_r and spread_r:
                gap = compute_fidelity_gap(naive_r, spread_r)
                rankings.append({
                    "symbol": symbol,
                    "naive_combined": naive_r.combined_pnl,
                    "spread_adjusted_combined": spread_r.combined_pnl,
                    "edge_survival_pct": gap["edge_survival_pct"],
                    "pnl_gap_usd": gap["pnl_gap_usd"],
                })
        rankings.sort(key=lambda x: x["edge_survival_pct"], reverse=True)

        return {
            "all_results": {
                sym: [
                    {
                        "mode": r.mode,
                        "realized_pnl": r.realized_pnl,
                        "floating_pnl": r.floating_pnl,
                        "combined_pnl": r.combined_pnl,
                        "total_trades": r.total_trades,
                        "win_rate": r.win_rate,
                        "avg_trade_pnl": r.avg_trade_pnl,
                        "max_drawdown": r.max_drawdown,
                        "total_spread_cost": r.total_spread_cost,
                        "total_slippage_cost": r.total_slippage_cost,
                        "same_bar_roundtrips_blocked": r.same_bar_roundtrips_blocked,
                        "same_bar_roundtrips_allowed": r.same_bar_roundtrips_allowed,
                    }
                    for r in results
                ]
                for sym, results in all_results.items()
            },
            "fidelity_gaps": summary_rows,
            "lane_rankings": rankings,
        }
    finally:
        mt5.shutdown()


def print_text_report(data: dict) -> str:
    """Generate a human-readable text report."""
    lines = []
    lines.append("=" * 110)
    lines.append("  BACKTEST FIDELITY AUDIT")
    lines.append("  Quantifying the gap between backtest numbers and live reality")
    lines.append("=" * 110)
    lines.append("")

    # Per-symbol detail
    for symbol, results in data.get("all_results", {}).items():
        lines.append(f"  {'=' * 110}")
        lines.append(f"  {symbol}")
        lines.append(f"  {'=' * 110}")
        lines.append("")
        lines.append(
            f"  {'Mode':<22s}  {'Realized':>12s}  {'Floating':>12s}  {'Combined':>12s}  "
            f"{'Trades':>7s}  {'WR%':>5s}  {'AvgPnL':>10s}  {'MaxDD':>12s}"
        )
        lines.append("-" * 110)
        for r in results:
            extras = ""
            if r["mode"] == "spread_adjusted" and r["total_spread_cost"] != 0:
                extras = f"  spread_pen=${r['total_spread_cost']:.2f}"
            elif r["mode"] == "slippage_adjusted" and r["total_slippage_cost"] != 0:
                extras = f"  slip_pen=${r['total_slippage_cost']:.2f}"
            elif r["mode"] == "no_same_bar" and r["same_bar_roundtrips_blocked"] > 0:
                extras = f"  blocked={r['same_bar_roundtrips_blocked']}"

            lines.append(
                f"  {r['mode']:<22s}  ${r['realized_pnl']:>+11.2f}  ${r['floating_pnl']:>+11.2f}  "
                f"${r['combined_pnl']:>+11.2f}  {r['total_trades']:>7}  "
                f"{r['win_rate']:>4.1f}%  ${r['avg_trade_pnl']:>+9.3f}  "
                f"${r['max_drawdown']:>+11.2f}{extras}"
            )
        lines.append("")

    # Fidelity gaps
    lines.append(f"  {'=' * 110}")
    lines.append("  FIDELITY GAPS (naive -> adjusted)")
    lines.append(f"  {'=' * 110}")
    lines.append("")
    lines.append(
        f"  {'Symbol':<10s}  {'Mode':<20s}  {'Naive $':>12s}  {'Adj $':>12s}  "
        f"{'Gap $':>10s}  {'Gap %':>6s}  {'WR gap':>7s}  {'Edge Surv%':>10s}"
    )
    lines.append("-" * 110)
    for g in data.get("fidelity_gaps", []):
        lines.append(
            f"  {g['symbol']:<10s}  {g['mode']:<20s}  ${g['naive_combined']:>+11.2f}  "
            f"${g['adjusted_combined']:>+11.2f}  ${g['pnl_gap_usd']:>+9.2f}  "
            f"{g['pnl_gap_pct']:>+5.1f}%  {g['wr_gap_pp']:>+.1f}pp  "
            f"{g['edge_survival_pct']:>9.1f}%"
        )
    lines.append("")

    # Lane rankings
    lines.append(f"  {'=' * 110}")
    lines.append("  LANE RANKINGS by edge survival (spread-adjusted)")
    lines.append(f"  {'=' * 110}")
    lines.append("")
    lines.append(
        f"  {'Rank':>4s}  {'Symbol':<10s}  {'Naive $':>12s}  {'Spread-Adj $':>14s}  "
        f"{'Edge Surv%':>10s}  {'PnL Gap $':>10s}"
    )
    lines.append("-" * 80)
    for i, r in enumerate(data.get("lane_rankings", []), 1):
        if r["edge_survival_pct"] > 80:
            verdict = "  [GOOD - edge survives]"
        elif r["edge_survival_pct"] > 50:
            verdict = "  [MARGINAL - edge degraded]"
        elif r["edge_survival_pct"] > 0:
            verdict = "  [WEAK - edge mostly gone]"
        else:
            verdict = "  [GONE - edge erased by spread]"

        lines.append(
            f"  {i:>4}  {r['symbol']:<10s}  ${r['naive_combined']:>+11.2f}  "
            f"${r['spread_adjusted_combined']:>+13.2f}  "
            f"{r['edge_survival_pct']:>9.1f}%  ${r['pnl_gap_usd']:>+9.2f}{verdict}"
        )
    lines.append("")
    lines.append("=" * 110)
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest fidelity audit")
    parser.add_argument("--symbols", nargs="*", default=None, help="Symbols to audit (default: all configured lanes)")
    parser.add_argument("--days", type=int, default=None, help="Override days for all symbols")
    parser.add_argument("--timeframe", default=None, help="Override timeframe (M1, M5, M15, H1, H4)")
    args = parser.parse_args()

    data = run_audit(
        symbols=args.symbols,
        days_override=args.days,
        tf_override=args.timeframe,
    )

    if data is None:
        return 1

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # JSON output
    json_path = reports_dir / "backtest_fidelity_audit.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nWrote {json_path}")

    # Text output
    text = print_text_report(data)
    text_path = reports_dir / "backtest_fidelity_audit.txt"
    with text_path.open("w", encoding="utf-8") as f:
        f.write(text)
    print(f"Wrote {text_path}")

    # Print to console
    print("")
    print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
