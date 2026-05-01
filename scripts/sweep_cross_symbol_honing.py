#!/usr/bin/env python3
"""Cross-Symbol Honing Sweep — test optimal M5+H1 configs on ETHUSD, SOLUSD, XRPUSD."""
from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD", "ADAUSD", "DOTUSD"]
DAYS = 90


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


def load_bars(symbol: str, days: int, timeframe: int) -> list[dict]:
    bars_per_day = 288 if timeframe == mt5.TIMEFRAME_M5 else 24
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars_per_day * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def simulate_crypto(sym, bars, info, step, max_open, gap, alpha, momentum_gate):
    if not bars:
        return {}
    spread_px = spread_price(info)
    anchor = bars[0]["close"]
    ns = anchor + step
    nb = anchor - step
    tk = []
    rl = []
    churn = []
    crl = []
    max_seen = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        while bar["high"] >= ns and os_ < max_open:
            tk.append(Ticket(direction="SELL", entry_price=ns, opened_idx=idx))
            os_ += 1
            ns += step
        while bar["low"] <= nb and ob < max_open:
            tk.append(Ticket(direction="BUY", entry_price=nb, opened_idx=idx))
            ob += 1
            nb -= step

        closed = []
        sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > gap and bar["low"] <= sl[gap].entry_price:
            o = sl[0]
            r = sl[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            closed.append(("SELL", o.entry_price))
            tk.remove(o)
            sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]
            r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            closed.append(("BUY", o.entry_price))
            tk.remove(o)
            bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)

        if not tk and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            ns = anchor + step
            nb = anchor - step

        cos_ = sum(1 for t in churn if t.direction == "SELL")
        cob = sum(1 for t in churn if t.direction == "BUY")
        for d, cp in closed:
            c = cos_ if d == "SELL" else cob
            if c >= max_open:
                continue
            if momentum_gate:
                if d == "SELL" and bar["close"] >= cp:
                    continue
                if d == "BUY" and bar["close"] <= cp:
                    continue
            churn.append(ChurnTicket(d, cp, idx))
            if d == "SELL":
                cos_ += 1
            else:
                cob += 1

        cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            o = cs[0]
            r = cs[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]
            r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)

        max_seen = max(max_seen, len(tk) + len(churn))

    fl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]

    realized = sum(rl) + sum(crl)
    floating = sum(fl) + sum(cfl)
    combined = realized + floating

    return {
        "combined": combined,
        "realized": realized,
        "floating": floating,
        "closes": len(rl) + len(crl),
        "rearm_opens": len(crl),
        "max_seen": max_seen,
    }


def find_optimal_step(symbol: str, bars: list[dict], info, timeframe_name: str) -> dict:
    """Find optimal step size for a symbol on a given timeframe."""
    # Use symbol-appropriate step sizes
    pip_size = info.point * 10
    
    # Scale steps based on typical price range
    if timeframe_name == "M5":
        if "ETH" in symbol:
            steps = [5, 10, 15, 20, 25, 50]
        elif "SOL" in symbol:
            steps = [0.5, 1, 2, 3, 5, 10]
        elif "XRP" in symbol:
            steps = [0.01, 0.02, 0.03, 0.05, 0.10, 0.20]
        elif "DOGE" in symbol:
            steps = [0.001, 0.002, 0.003, 0.005, 0.010, 0.020]
        elif "ADA" in symbol:
            steps = [0.01, 0.02, 0.03, 0.05, 0.10, 0.20]
        elif "DOT" in symbol:
            steps = [0.1, 0.2, 0.3, 0.5, 1, 2]
        else:
            steps = [1, 2, 5, 10, 20, 50]
    else:  # H1
        if "ETH" in symbol:
            steps = [10, 20, 30, 50, 75, 100]
        elif "SOL" in symbol:
            steps = [1, 2, 3, 5, 10, 20]
        elif "XRP" in symbol:
            steps = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50]
        elif "DOGE" in symbol:
            steps = [0.002, 0.005, 0.010, 0.020, 0.030, 0.050]
        elif "ADA" in symbol:
            steps = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50]
        elif "DOT" in symbol:
            steps = [0.2, 0.5, 1, 2, 3, 5]
        else:
            steps = [2, 5, 10, 20, 50, 100]
    
    best = None
    best_val = -999999999
    
    for step in steps:
        r = simulate_crypto(symbol, bars, info, step, 60, 1, 1.0, False)
        val = r.get("combined", -999999999)
        if val > best_val:
            best_val = val
            best = {
                "step": step,
                "max_open": 60,
                "gap": 1,
                "alpha": 1.0,
                "momentum_gate": False,
                **r
            }
    
    return best


def main() -> int:
    mt5.initialize()
    
    print(f"\n{'='*120}")
    print(f"  CROSS-SYMBOL HONING SWEEP — 6 crypto symbols, M5 + H1")
    print(f"{'='*120}")
    
    results = []
    
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"\n⚠️  {sym}: No symbol info available, skipping")
            continue
        
        # M5 sweep
        m5_bars = load_bars(sym, DAYS, mt5.TIMEFRAME_M5)
        if m5_bars:
            print(f"\n{'─'*80}")
            print(f"  {sym} M5 — {len(m5_bars)} bars")
            print(f"{'─'*80}")
            best_m5 = find_optimal_step(sym, m5_bars, info, "M5")
            if best_m5:
                results.append({
                    "symbol": sym,
                    "timeframe": "M5",
                    "step": best_m5["step"],
                    "max_open": best_m5["max_open"],
                    "gap": best_m5["gap"],
                    "alpha": best_m5["alpha"],
                    "momentum": best_m5["momentum_gate"],
                    "combined": best_m5.get("combined", 0),
                    "realized": best_m5.get("realized", 0),
                    "floating": best_m5.get("floating", 0),
                    "closes": best_m5.get("closes", 0),
                    "rearm_opens": best_m5.get("rearm_opens", 0),
                    "max_seen": best_m5.get("max_seen", 0),
                })
                print(f"  🏆 Best M5: step={best_m5['step']}, ${best_m5.get('combined', 0):,.2f}, {best_m5.get('closes', 0)} closes")
        else:
            print(f"\n⚠️  {sym} M5: No bars available")
        
        # H1 sweep
        h1_bars = load_bars(sym, DAYS, mt5.TIMEFRAME_H1)
        if h1_bars:
            print(f"\n{'─'*80}")
            print(f"  {sym} H1 — {len(h1_bars)} bars")
            print(f"{'─'*80}")
            best_h1 = find_optimal_step(sym, h1_bars, info, "H1")
            if best_h1:
                results.append({
                    "symbol": sym,
                    "timeframe": "H1",
                    "step": best_h1["step"],
                    "max_open": best_h1["max_open"],
                    "gap": best_h1["gap"],
                    "alpha": best_h1["alpha"],
                    "momentum": best_h1["momentum_gate"],
                    "combined": best_h1.get("combined", 0),
                    "realized": best_h1.get("realized", 0),
                    "floating": best_h1.get("floating", 0),
                    "closes": best_h1.get("closes", 0),
                    "rearm_opens": best_h1.get("rearm_opens", 0),
                    "max_seen": best_h1.get("max_seen", 0),
                })
                print(f"  🏆 Best H1: step={best_h1['step']}, ${best_h1.get('combined', 0):,.2f}, {best_h1.get('closes', 0)} closes")
        else:
            print(f"\n⚠️  {sym} H1: No bars available")
    
    if results:
        # Sort by combined
        results.sort(key=lambda r: r["combined"], reverse=True)
        
        print(f"\n{'='*120}")
        print(f"  CROSS-SYMBOL RESULTS (sorted by profit)")
        print(f"{'='*120}")
        print(f"\n{'Symbol':<10} {'TF':<4} {'Step':>8} {'MO':>4} {'α':>5} {'Mom':>4} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7} {'MaxSeen':>7}")
        print("-" * 120)
        for r in results:
            mom_str = "ON" if r["momentum"] else "OFF"
            print(f"{r['symbol']:<10} {r['timeframe']:<4} ${r['step']:>7.4f} {r['max_open']:>4} {r['alpha']:>5.2f} {mom_str:>4} ${r['combined']:>11,.2f} ${r['realized']:>11,.2f} ${r['floating']:>11,.2f} {r['closes']:>7} {r['max_seen']:>7}")
        
        # Summary totals
        m5_total = sum(r["combined"] for r in results if r["timeframe"] == "M5")
        h1_total = sum(r["combined"] for r in results if r["timeframe"] == "H1")
        grand_total = m5_total + h1_total
        
        print(f"\n{'='*120}")
        print(f"  PORTFOLIO SUMMARY")
        print(f"{'='*120}")
        print(f"  M5 Total: ${m5_total:,.2f}")
        print(f"  H1 Total: ${h1_total:,.2f}")
        print(f"  GRAND TOTAL: ${grand_total:,.2f}")
        print(f"  + BTCUSD (M5 $663K + H1 $269K): ${grand_total + 932000:,.2f}")
        
        # Save to CSV
        out_path = ROOT / "reports" / "cross_symbol_honing.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nWrote {out_path}")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
