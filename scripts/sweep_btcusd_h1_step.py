#!/usr/bin/env python3
"""BTCUSD H1 step-size sweep — is live step=$45 too tight?

Tests step sizes against 120 days of H1 BTCUSD data to answer:
  Is the live_btcusd_exc2_tight lane bleeding because step=$45
  causes over-churning, while backtest-optimal was $50?

Fixed params (lvl2_exc2 rearm variant):
  max_open_per_side=50, close_gap=2, alpha=1.0, rearm_variant=lvl2_exc2

Step sizes tested:
  $45  — current live (suspect: too tight, over-churning)
  $50  — backtest optimal
  $60  — wider candidate
  $75  — moderate-wide
  $100 — current M5 warp
  $150 — very wide, low-churn baseline
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "BTCUSD"
DAYS = 120
TIMEFRAME = mt5.TIMEFRAME_H1

# Fixed params for lvl2_exc2 rearm variant
MAX_OPEN_PER_SIDE = 50
CLOSE_GAP = 2
ALPHA = 1.0
REARM_VARIANT = "lvl2_exc2"

STEP_SIZES = [45.0, 50.0, 60.0, 75.0, 100.0, 150.0]


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


def sim_btcusd_h1(bars, info, step, mop, gap, alpha):
    """H1 penetration lattice with lvl2_exc2 rearm churn."""
    if not bars:
        return {}

    spread_px = spread_price(info)
    base_step = step

    anchor = bars[0]["close"]
    ns = anchor + base_step
    nb = anchor - base_step
    tk = []       # main lattice tickets
    rl = []       # main realized PnL
    churn = []    # rearm churn tickets
    crl = []      # churn realized PnL

    # Track per-trade PnL for win rate and equity curve for drawdown
    all_trade_pnls = []  # individual closed trade PnLs
    equity_curve = [0.0]  # cumulative equity over time

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        # Lattice entries
        while bar["high"] >= ns and os_ < mop:
            tk.append(Ticket(direction="SELL", entry_price=ns, opened_idx=idx))
            os_ += 1
            ns += base_step
        while bar["low"] <= nb and ob < mop:
            tk.append(Ticket(direction="BUY", entry_price=nb, opened_idx=idx))
            ob += 1
            nb -= base_step

        # Lattice closes (lvl2_exc2: gap-based penetration)
        closed = []
        sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > gap and bar["low"] <= sl[gap].entry_price:
            o = sl[0]
            r = sl[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
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
            pnl = unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px)
            rl.append(pnl)
            all_trade_pnls.append(pnl)
            closed.append(("BUY", o.entry_price))
            tk.remove(o)
            bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Anchor reset when flat and price moves >= step
        if not tk and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]
            ns = anchor + base_step
            nb = anchor - base_step

        # Rearm churn entries at closed levels
        cos_ = sum(1 for t in churn if t.direction == "SELL")
        cob = sum(1 for t in churn if t.direction == "BUY")
        for d, cp in closed:
            c = cos_ if d == "SELL" else cob
            if c >= mop:
                continue
            # lvl2_exc2: no momentum gate, rearm at every closed level
            churn.append(ChurnTicket(d, cp, idx))
            if d == "SELL":
                cos_ += 1
            else:
                cob += 1

        # Churn closes (same gap-based logic)
        cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            o = cs[0]
            r = cs[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
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
            pnl = unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px)
            crl.append(pnl)
            all_trade_pnls.append(pnl)
            churn.remove(o)
            cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Equity curve: realized + current floating
        fl_now = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bar["close"], spread_px) for t in tk]
        cfl_now = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bar["close"], spread_px) for t in churn]
        equity_curve.append(sum(rl) + sum(crl) + sum(fl_now) + sum(cfl_now))

    # Floating PnL at end of sample
    fl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]

    realized_pnl = sum(rl) + sum(crl)
    floating_pnl = sum(fl) + sum(cfl)
    combined_pnl = realized_pnl + floating_pnl

    # Win rate
    total_trades = len(all_trade_pnls)
    winning_trades = sum(1 for p in all_trade_pnls if p > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    # Max drawdown from equity curve
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    return {
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

    print(f"\n{'='*110}")
    print(f"  BTCUSD H1 STEP-SIZE SWEEP — {len(bars)} bars, {DAYS} days, rearm=lvl2_exc2")
    print(f"  Fixed: max_open={MAX_OPEN_PER_SIDE}, gap={CLOSE_GAP}, alpha={ALPHA}")
    print(f"{'='*110}")

    results = []
    for step in STEP_SIZES:
        r = sim_btcusd_h1(bars, info, step, MAX_OPEN_PER_SIDE, CLOSE_GAP, ALPHA)
        if not r:
            print(f"  Step ${step:>6.0f}: no result")
            continue
        results.append({
            "step": int(step),
            "realized_pnl": round(r["realized_pnl"], 2),
            "floating_pnl": round(r["floating_pnl"], 2),
            "combined_pnl": round(r["combined_pnl"], 2),
            "closes": r["closes"],
            "lattice_closes": r["lattice_closes"],
            "rearm_opens": r["rearm_opens"],
            "lattice_realized": round(r["lattice_realized"], 2),
            "churn_realized": round(r["churn_realized"], 2),
            "lattice_floating": round(r["lattice_floating"], 2),
            "churn_floating": round(r["churn_floating"], 2),
            "lattice_left": r["lattice_tickets_left"],
            "churn_left": r["churn_tickets_left"],
            "win_rate": r["win_rate"],
            "total_trades": r["total_trades"],
            "winning_trades": r["winning_trades"],
            "losing_trades": r["losing_trades"],
            "max_drawdown": r["max_drawdown"],
            "avg_trade_pnl": r["avg_trade_pnl"],
        })

    results.sort(key=lambda r: r["combined_pnl"], reverse=True)

    # Summary table
    print(f"\n{'Step':>6}  {'Realized':>14}  {'Floating':>14}  {'Combined':>14}  {'Closes':>7}  {'Win%':>5}  {'Trades':>7}  {'MaxDD':>12}  {'AvgPnL':>10}")
    print("-" * 120)
    for r in results:
        marker = " <-- LIVE" if r["step"] == 45 else ""
        marker += " <-- BT OPT" if r["step"] == 50 else ""
        marker += " <-- M5 WARP" if r["step"] == 100 else ""
        print(
            f"${r['step']:>5.0f}  ${r['realized_pnl']:>13,.2f}  ${r['floating_pnl']:>13,.2f}  ${r['combined_pnl']:>13,.2f}"
            f"  {r['closes']:>7}  {r['win_rate']:>4.1f}%  {r['total_trades']:>7}  ${r['max_drawdown']:>11,.2f}  ${r['avg_trade_pnl']:>9,.2f}{marker}"
        )

    # Diagnosis: churn ratio by step
    print(f"\n{'Churn analysis:'}")
    print(f"{'Step':>6}  {'Total closes':>12}  {'Rearm opens':>11}  {'Rearm%':>7}  {'Combined PnL':>14}")
    print("-" * 70)
    for r in sorted(results, key=lambda x: x["step"]):
        rearm_pct = (r["rearm_opens"] / r["closes"] * 100) if r["closes"] > 0 else 0
        print(
            f"${r['step']:>5.0f}  {r['closes']:>12}  {r['rearm_opens']:>11}  {rearm_pct:>6.1f}%"
            f"  ${r['combined_pnl']:>13,.2f}"
        )

    best = results[0]
    live_45 = next((r for r in results if r["step"] == 45), None)
    bt_50 = next((r for r in results if r["step"] == 50), None)

    print(f"\n{'='*110}")
    print(f"  Top: step=${best['step']} -> ${best['combined_pnl']:,.2f} combined ({best['closes']} closes, {best['rearm_opens']} rearm opens)")
    if live_45 and bt_50:
        delta = bt_50["combined_pnl"] - live_45["combined_pnl"]
        churn_diff = live_45["rearm_opens"] - bt_50["rearm_opens"]
        print(f"  $45 vs $50: Δ combined = ${delta:+,.2f}")
        print(f"  $45 rearm opens vs $50: +{churn_diff} ({'more churn' if churn_diff > 0 else 'less churn'})")
        if live_45["closes"] > bt_50["closes"] * 1.2:
            print(f"  ⚠️  step=$45 generates {live_45['closes']/bt_50['closes']:.1f}x more closes than $50 — over-churning confirmed")
        if live_45["floating_pnl"] < bt_50["floating_pnl"]:
            print(f"  ⚠️  step=$45 floating is ${live_45['floating_pnl']:,.2f} vs $50 ${bt_50['floating_pnl']:,.2f} — wider trap exposure")
    print(f"{'='*110}")

    # Write detailed CSV
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    detailed_path = reports_dir / "btcusd_h1_step_sweep.csv"
    fieldnames_detailed = [
        "step", "realized_pnl", "floating_pnl", "combined_pnl",
        "closes", "lattice_closes", "rearm_opens",
        "lattice_realized", "churn_realized",
        "lattice_floating", "churn_floating",
        "lattice_left", "churn_left",
        "win_rate", "total_trades", "winning_trades", "losing_trades",
        "max_drawdown", "avg_trade_pnl",
    ]
    with detailed_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames_detailed)
        w.writeheader()
        w.writerows(results)
    print(f"\nWrote {detailed_path}")

    # Write summary CSV (only the headline metrics)
    summary_path = reports_dir / "btcusd_h1_step_summary.csv"
    fieldnames_summary = [
        "step", "realized_pnl", "floating_pnl", "combined_pnl",
        "closes", "win_rate", "total_trades", "max_drawdown", "avg_trade_pnl",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames_summary, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    print(f"Wrote {summary_path}")

    # Write JSONL for programmatic consumption
    jsonl_path = reports_dir / "btcusd_h1_step_sweep.jsonl"
    import json
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {jsonl_path}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
