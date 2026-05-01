#!/usr/bin/env python3
"""Multi-Timeframe Stacking Test — M15+M5+H1 on BTCUSD to check for interference."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "BTCUSD"
DAYS = 90


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


def load_bars(timeframe: int) -> list[dict]:
    bars_per_day = 96 if timeframe == mt5.TIMEFRAME_M15 else 288 if timeframe == mt5.TIMEFRAME_M5 else 24
    rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, bars_per_day * DAYS)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def run_engine(bars, info, step, max_open, gap, alpha, momentum_gate):
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
            rl.append(unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px))
            closed.append(("SELL", o.entry_price))
            tk.remove(o)
            sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]
            r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px))
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
            crl.append(unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]
            r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            crl.append(unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)

        max_seen = max(max_seen, len(tk) + len(churn))

    fl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]

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


def main() -> int:
    mt5.initialize()
    
    info = mt5.symbol_info(SYMBOL)
    
    m15_bars = load_bars(mt5.TIMEFRAME_M15)
    m5_bars = load_bars(mt5.TIMEFRAME_M5)
    h1_bars = load_bars(mt5.TIMEFRAME_H1)
    
    print(f"\n{'='*100}")
    print(f"  MULTI-TIMEFRAME STACKING TEST — {SYMBOL} {DAYS}d")
    print(f"  M15: {len(m15_bars)} bars | M5: {len(m5_bars)} bars | H1: {len(h1_bars)} bars")
    print(f"{'='*100}")
    
    # Run each timeframe independently
    m15_r = run_engine(m15_bars, info, step=15.0, max_open=80, gap=1, alpha=1.0, momentum_gate=False)
    m5_r = run_engine(m5_bars, info, step=100.0, max_open=60, gap=1, alpha=1.0, momentum_gate=False)
    h1_r = run_engine(h1_bars, info, step=25.0, max_open=60, gap=1, alpha=1.0, momentum_gate=False)
    
    # Combined (simple sum since they run on different timeframes = non-overlapping)
    m15_c = m15_r.get("combined", 0)
    m5_c = m5_r.get("combined", 0)
    h1_c = h1_r.get("combined", 0)
    total = m15_c + m5_c + h1_c
    
    m15_close = m15_r.get("closes", 0)
    m5_close = m5_r.get("closes", 0)
    h1_close = h1_r.get("closes", 0)
    total_closes = m15_close + m5_close + h1_close
    
    print(f"\n{'TF':<6} {'Step':>6} {'MO':>4} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7} {'MaxSeen':>7}")
    print("-" * 100)
    print(f"{'M15':<6} ${15:>5.0f} {80:>4} ${m15_c:>11,.2f} ${m15_r.get('realized', 0):>11,.2f} ${m15_r.get('floating', 0):>11,.2f} {m15_close:>7} {m15_r.get('max_seen', 0):>7}")
    print(f"{'M5':<6} ${100:>5.0f} {60:>4} ${m5_c:>11,.2f} ${m5_r.get('realized', 0):>11,.2f} ${m5_r.get('floating', 0):>11,.2f} {m5_close:>7} {m5_r.get('max_seen', 0):>7}")
    print(f"{'H1':<6} ${25:>5.0f} {60:>4} ${h1_c:>11,.2f} ${h1_r.get('realized', 0):>11,.2f} ${h1_r.get('floating', 0):>11,.2f} {h1_close:>7} {h1_r.get('max_seen', 0):>7}")
    print("-" * 100)
    print(f"{'TOTAL':<6} {'':>6} {'':>4} ${total:>11,.2f} ${m15_r.get('realized',0)+m5_r.get('realized',0)+h1_r.get('realized',0):>11,.2f} ${m15_r.get('floating',0)+m5_r.get('floating',0)+h1_r.get('floating',0):>11,.2f} {total_closes:>7} {'':>7}")
    
    # Save to CSV
    results = [
        {"tf": "M15", "step": 15.0, "max_open": 80, **m15_r},
        {"tf": "M5", "step": 100.0, "max_open": 60, **m5_r},
        {"tf": "H1", "step": 25.0, "max_open": 60, **h1_r},
        {"tf": "TOTAL", "step": 0, "max_open": 0, "combined": total, "realized": m15_r.get('realized',0)+m5_r.get('realized',0)+h1_r.get('realized',0), "floating": m15_r.get('floating',0)+m5_r.get('floating',0)+h1_r.get('floating',0), "closes": total_closes, "rearm_opens": m15_r.get('rearm_opens',0)+m5_r.get('rearm_opens',0)+h1_r.get('rearm_opens',0), "max_seen": max(m15_r.get('max_seen',0), m5_r.get('max_seen',0), h1_r.get('max_seen',0))},
    ]
    
    out_path = ROOT / "reports" / "multi_tf_stacking_test.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["tf", "step", "max_open", "combined", "realized", "floating", "closes", "rearm_opens", "max_seen"])
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nWrote {out_path}")
    print(f"\n🏆 MULTI-TIMEFRAME TOTAL: ${total:,.2f} in {DAYS} days")
    print(f"   = ${total/90:,.2f}/day = ${total/90*365:,.2f}/year")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
