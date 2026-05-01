#!/usr/bin/env python3
"""Final cross-engine validation: M15 $20 mom=ON on my simplified engine."""
import MetaTrader5 as mt5
from penetration_lattice_lab_v2 import Ticket, spread_price, unit_pnl_usd

SYMBOL = "BTCUSD"
DAYS = 90

class ChurnTicket:
    def __init__(self, d, e, o, from_rearm=False):
        self.direction = d
        self.entry_price = e
        self.opened_idx = o
        self.from_rearm = from_rearm

def run_engine(bars, info, step, max_open, gap, alpha, momentum_gate):
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
            churn.append(ChurnTicket(d, cp, idx, from_rearm=False))
            if d == "SELL":
                cos_ += 1
            else:
                cob += 1

        cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            o = cs[0]
            r = cs[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            t = Ticket(direction="SELL", entry_price=o.entry_price, opened_idx=o.opened_idx)
            setattr(t, 'from_rearm', True)
            crl.append(unit_pnl_usd(SYMBOL, "SELL", t.entry_price, close_px, spread_px))
            churn.remove(o)
            cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]
            r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            t = Ticket(direction="BUY", entry_price=o.entry_price, opened_idx=o.opened_idx)
            setattr(t, 'from_rearm', True)
            crl.append(unit_pnl_usd(SYMBOL, "BUY", t.entry_price, close_px, spread_px))
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

mt5.initialize()
info = mt5.symbol_info(SYMBOL)
rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, 96 * DAYS)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]

print(f"\n  BTCUSD M15 {DAYS}d, {len(bars)} bars")
print(f"  Config: step=$20, MO=60, gap=1, alpha=1.00, mom=ON")

r = run_engine(bars, info, 20.0, 60, 1, 1.0, True)
print(f"  Combined: ${r['combined']:,.2f}")
print(f"  Realized: ${r['realized']:,.2f}")
print(f"  Floating: ${r['floating']:,.2f}")
print(f"  Closes:   {r['closes']}")
print(f"  Rearm:    {r['rearm_opens']}")
print(f"  MaxSeen:  {r['max_seen']}")
print(f"\n  @qwen's StatefulRearmRawEngine: $4,517,000")
print(f"  My simplified engine:            ${r['combined']:,.2f}")
diff = r['combined'] - 4517000
print(f"  Difference:                      ${diff:,.2f} ({r['combined']/4517000*100:.1f}%)")

mt5.shutdown()
