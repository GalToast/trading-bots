#!/usr/bin/env python3
"""Direct engine comparison — run both engines on EXACT same BTCUSD M15 bars."""
from __future__ import annotations

import sys
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from live_penetration_lattice_shadow import StatefulRearmRawEngine, REARM_VARIANTS, RearmVariant
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import Ticket, spread_price, unit_pnl_usd


SYMBOL = "BTCUSD"
DAYS = 90
TIMEFRAME = mt5.TIMEFRAME_M15


def load_bars() -> list[dict]:
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 96 * DAYS)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def run_my_engine(bars, info, step, max_open, gap, alpha, momentum_gate):
    """My simplified engine — the one that produced $1.79M."""
    spread_px = spread_price(info)
    
    class ChurnTicket:
        def __init__(self, d, e, o):
            self.direction = d
            self.entry_price = e
            self.opened_idx = o
    
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

    return {
        "combined": sum(rl) + sum(crl) + sum(fl) + sum(cfl),
        "realized": sum(rl) + sum(crl),
        "floating": sum(fl) + sum(cfl),
        "closes": len(rl) + len(crl),
        "rearm_opens": len(crl),
        "max_seen": max_seen,
    }


def run_stateful_engine(bars, info, step, max_open, gap, alpha, momentum_gate):
    """StatefulRearmRawEngine — the one @qwen patched to $1.09M."""
    # CRITICAL: For crypto, step is in price units, NOT pips
    from dataclasses import replace
    cfg = RawConfig(step_pips=step, max_open_per_side=max_open, close_mode="two_level", step_is_price_units=True)
    variant = REARM_VARIANTS["rearm_lvl2_exc1"]
    
    engine = StatefulRearmRawEngine(
        SYMBOL, cfg, info, variant=variant,
        close_alpha=alpha, cooldown_bars=0, momentum_gate=momentum_gate,
        sell_gap=gap, buy_gap=gap,
    )
    
    # Disable event emission for speed
    for bar in bars:
        engine.process_bar(bar, event_path=None, emit=False)
    
    # Calculate floating from open tickets
    last_close = bars[-1]["close"]
    spread_px = spread_price(info)
    floating = sum(
        unit_pnl_usd(SYMBOL, t["direction"], t["entry_price"], last_close, spread_px)
        for t in engine.state.open_tickets
    )
    
    return {
        "combined": engine.state.realized_net_usd + floating,
        "realized": engine.state.realized_net_usd,
        "floating": floating,
        "closes": engine.state.realized_closes,
        "rearm_opens": engine.state.rearm_opens,
        "max_seen": engine.state.max_open_total,
    }


def main():
    mt5.initialize()
    
    info = mt5.symbol_info(SYMBOL)
    bars = load_bars()
    
    if not bars:
        print("No bars loaded")
        return 1
    
    print(f"\n{'='*100}")
    print(f"  DIRECT ENGINE COMPARISON — {SYMBOL} M15 {DAYS}d, {len(bars)} bars")
    print(f"  Config: step=$15, MO=80, gap=1, alpha=1.00, mom=OFF")
    print(f"{'='*100}")
    
    step = 15.0
    max_open = 80
    gap = 1
    alpha = 1.0
    mom = False
    
    print(f"\n  Running my engine...")
    my_r = run_my_engine(bars, info, step, max_open, gap, alpha, mom)
    print(f"  Combined: ${my_r['combined']:,.2f}")
    print(f"  Realized: ${my_r['realized']:,.2f}")
    print(f"  Floating: ${my_r['floating']:,.2f}")
    print(f"  Closes:   {my_r['closes']}")
    print(f"  Rearm:    {my_r['rearm_opens']}")
    print(f"  MaxSeen:  {my_r['max_seen']}")
    
    print(f"\n  Running StatefulRearmRawEngine...")
    sf_r = run_stateful_engine(bars, info, step, max_open, gap, alpha, mom)
    print(f"  Combined: ${sf_r['combined']:,.2f}")
    print(f"  Realized: ${sf_r['realized']:,.2f}")
    print(f"  Floating: ${sf_r['floating']:,.2f}")
    print(f"  Closes:   {sf_r['closes']}")
    print(f"  Rearm:    {sf_r['rearm_opens']}")
    print(f"  MaxSeen:  {sf_r['max_seen']}")
    
    # Compare
    print(f"\n{'='*100}")
    print(f"  DISCREPANCY ANALYSIS")
    print(f"{'='*100}")
    
    combined_diff = my_r['combined'] - sf_r['combined']
    combined_pct = (my_r['combined'] / sf_r['combined'] * 100) if sf_r['combined'] != 0 else 0
    closes_diff = my_r['closes'] - sf_r['closes']
    rearm_diff = my_r['rearm_opens'] - sf_r['rearm_opens']
    maxseen_diff = my_r['max_seen'] - sf_r['max_seen']
    
    print(f"  Combined:   ${my_r['combined']:>12,.2f} vs ${sf_r['combined']:>12,.2f} = {combined_pct:.1f}% ({combined_diff:+,.2f})")
    print(f"  Realized:   ${my_r['realized']:>12,.2f} vs ${sf_r['realized']:>12,.2f}")
    print(f"  Floating:   ${my_r['floating']:>12,.2f} vs ${sf_r['floating']:>12,.2f}")
    print(f"  Closes:     {my_r['closes']:>12} vs {sf_r['closes']:>12} ({closes_diff:+})")
    print(f"  Rearm Ops:  {my_r['rearm_opens']:>12} vs {sf_r['rearm_opens']:>12} ({rearm_diff:+})")
    print(f"  MaxSeen:    {my_r['max_seen']:>12} vs {sf_r['max_seen']:>12} ({maxseen_diff:+})")
    
    if abs(combined_diff) < 1000:
        print(f"\n  ✅ ENGINES ALIGNED — difference < $1K")
    else:
        print(f"\n  ⚠️  ENGINES DIVERGE by ${combined_diff:,.2f} ({combined_pct:.1f}%)")
        if my_r['closes'] > sf_r['closes']:
            print(f"      My engine produces {closes_diff} more closes")
        if my_r['max_seen'] > sf_r['max_seen']:
            print(f"      My engine reaches {maxseen_diff} more max positions")
        if my_r['rearm_opens'] > sf_r['rearm_opens']:
            print(f"      My engine creates {rearm_diff} more rearm opens")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
