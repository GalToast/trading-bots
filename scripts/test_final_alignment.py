#!/usr/bin/env python3
"""Final engine alignment test — all 4 combinations."""
import MetaTrader5 as mt5
from penetration_lattice_lab_v2 import Ticket, spread_price, unit_pnl_usd, dynamic_step


SYMBOL = "BTCUSD"
DAYS = 90

class ChurnTicket:
    def __init__(self, d, e, o, from_rearm=False):
        self.direction = d
        self.entry_price = e
        self.opened_idx = o
        self.from_rearm = from_rearm

def make_adapt_cfg():
    return type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()

def run_engine(bars, info, step, max_open, gap, alpha, momentum_gate, use_adapt=False, rearm_from_all=True):
    spread_px = spread_price(info)
    anchor = bars[0]["close"]
    ns = anchor + step
    nb = anchor - step
    tk = []
    rl = []
    churn = []
    crl = []
    max_seen = 0
    adapt_cfg = make_adapt_cfg()

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        current_sell_step = dynamic_step(step, os_, adapt_cfg) if use_adapt else step
        current_buy_step = dynamic_step(step, ob, adapt_cfg) if use_adapt else step

        while bar["high"] >= ns and os_ < max_open:
            tk.append(Ticket(direction="SELL", entry_price=ns, opened_idx=idx))
            os_ += 1
            current_sell_step = dynamic_step(step, os_, adapt_cfg) if use_adapt else step
            ns += current_sell_step
        while bar["low"] <= nb and ob < max_open:
            tk.append(Ticket(direction="BUY", entry_price=nb, opened_idx=idx))
            ob += 1
            current_buy_step = dynamic_step(step, ob, adapt_cfg) if use_adapt else step
            nb -= current_buy_step

        closed = []
        sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > gap and bar["low"] <= sl[gap].entry_price:
            o = sl[0]
            r = sl[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            rl.append(unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px))
            from_rearm = getattr(o, 'from_rearm', False)
            closed.append(("SELL", o.entry_price, from_rearm))
            tk.remove(o)
            sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]
            r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px))
            from_rearm = getattr(o, 'from_rearm', False)
            closed.append(("BUY", o.entry_price, from_rearm))
            tk.remove(o)
            bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)

        if not tk and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            ns = anchor + step
            nb = anchor - step

        cos_ = sum(1 for t in churn if t.direction == "SELL")
        cob = sum(1 for t in churn if t.direction == "BUY")
        for d, cp, from_rearm in closed:
            if not rearm_from_all and from_rearm:
                continue
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
print(f"  Config: step=$15, MO=80, gap=1, alpha=1.00, mom=OFF")
print(f"  {'Steps':<12} {'Rearm':<12} {'Combined':>12} {'Closes':>7} {'Rearm':>6} {'MaxSeen':>7}")
print(f"  {'-'*60}")

configs = [
    (False, True, "Fixed", "All closes"),
    (False, False, "Fixed", "Main only"),
    (True, True, "Adaptive", "All closes"),
    (True, False, "Adaptive", "Main only"),
]

for use_adapt, rearm_from_all, steps_label, rearm_label in configs:
    r = run_engine(bars, info, 15.0, 80, 1, 1.0, False, use_adapt, rearm_from_all)
    print(f"  {steps_label:<12} {rearm_label:<12} ${r['combined']:>11,.2f} {r['closes']:>7} {r['rearm_opens']:>6} {r['max_seen']:>7}")

print(f"\n  StatefulRearmRawEngine (reference):             $1,092,712.81    6734   3427      81")

mt5.shutdown()
