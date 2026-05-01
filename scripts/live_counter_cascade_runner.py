#!/usr/bin/env python3
"""
LIVE COUNTER-TREND CASCADE RUNNER — Real MT5 execution

Winning config: GBPUSD M5, step=0.5p, mo=30, volume=0.01, counter-trend ON
Auto-detects broker filling mode. Demo mode tracks synthetic inventory honestly.

Usage:
    python scripts/live_counter_cascade_runner.py --symbol GBPUSD --demo
    python scripts/live_counter_cascade_runner.py --symbol GBPUSD  (live)
"""
import MetaTrader5 as mt5
import time, json, sys, os
from pathlib import Path
from datetime import datetime, timezone

# Filling mode constants (ORDER_FILLING_* exist, SYMBOL_FILLING_* may not)
FOK = mt5.ORDER_FILLING_FOK   # 0
IOC = mt5.ORDER_FILLING_IOC   # 1
RET = mt5.ORDER_FILLING_RETURN  # 2

def detect_filling_mode(symbol):
    """Detect broker-supported filling mode by trying each one."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return FOK
    fm = info.filling_mode
    # fm is a bitmask: 1=FOK, 2=IOC, 4=RETURN (but SYMBOL_* constants may not exist)
    if fm & 1:
        return FOK
    if fm & 2:
        return IOC
    return RET

def open_position(symbol, direction, volume, price, comment, fm):
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
           "type": ot, "price": price, "deviation": 20, "magic": 942000,
           "comment": comment, "type_time": mt5.ORDER_TIME_GTC, "type_filling": fm}
    r = mt5.order_send(req)
    if r is None:
        return None, "order_send returned None"
    if r.retcode != mt5.TRADE_RETCODE_DONE:
        return None, f"rc={r.retcode} {r.comment}"
    return r.order, None

def close_position(symbol, direction, volume, ticket, comment, fm):
    ot = mt5.ORDER_TYPE_SELL if direction == "BUY" else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None, "no tick"
    price = tick.bid if direction == "BUY" else tick.ask
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
           "position": ticket, "type": ot, "price": price, "deviation": 20,
           "magic": 942000, "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
           "type_filling": fm}
    r = mt5.order_send(req)
    if r is None:
        return None, "order_send returned None"
    if r.retcode != mt5.TRADE_RETCODE_DONE:
        return None, f"rc={r.retcode} {r.comment}"
    return r, None

def get_live_positions(symbol, magic=942000):
    ps = mt5.positions_get(symbol=symbol)
    return [p for p in ps] if ps else []

def compute_ema(closes, period):
    n = len(closes)
    if n < period:
        return [0.0] * n
    e = [0.0] * n
    m = 2.0 / (period + 1)
    e[period - 1] = sum(closes[:period]) / period
    for i in range(period, n):
        e[i] = (closes[i] - e[i - 1]) * m + e[i - 1]
    return e

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="GBPUSD")
    ap.add_argument("--step", type=float, default=0.00005)
    ap.add_argument("--max-open", type=int, default=30)
    ap.add_argument("--volume", type=float, default=0.01)
    ap.add_argument("--poll-seconds", type=int, default=1)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--state-file", default=None)
    ap.add_argument("--event-log", default=None)
    ap.add_argument("--spread-limit-pips", type=float, default=None,
                    help="Skip opens when spread exceeds this many pips")
    ap.add_argument("--max-float-loss", type=float, default=None,
                    help="Force unwind all positions when floating loss exceeds this")
    ap.add_argument("--max-runtime-minutes", type=float, default=None,
                    help="Stop after this many minutes")
    args = ap.parse_args()

    if args.state_file is None:
        args.state_file = f"reports/counter_cascade_{args.symbol.lower()}_state.json"
    if args.event_log is None:
        args.event_log = f"reports/counter_cascade_{args.symbol.lower()}_events.jsonl"

    if not mt5.initialize():
        print("ERROR: MT5 init failed"); return

    info = mt5.symbol_info(args.symbol)
    if info is None:
        print(f"ERROR: {args.symbol} not found"); mt5.shutdown(); return

    fm = detect_filling_mode(args.symbol)
    spread_pips = info.spread * info.point / info.point  # raw pips
    # pip value for 0.01 lot: 1 pip = contract_size * point * volume * 10(for 5-digit)
    digits = info.digits
    point = info.point
    contract = info.trade_contract_size
    vol = args.volume
    # PnL formula: (entry - exit) * contract_size * volume
    # For GBPUSD 0.01 lot: 0.00001 move * 100000 * 0.01 = $0.01 per point
    point_pnl = contract * vol  # PnL per 1.0 price movement

    mode_str = "DEMO" if args.demo else "LIVE"
    print(f"[{mode_str}] {args.symbol} step={args.step} mo={args.max_open} vol={vol} fm={fm} spread={spread_pips:.1f}pips point_pnl=${point_pnl:.2f}")

    # Demo inventory: list of {direction, entry, volume, ticket, time}
    demo_inv = []

    realized_net = 0.0
    realized_closes = 0
    counter_opens = 0
    worst_float = 0.0
    max_open_seen = 0
    anchor_resets = 0
    start_time = time.time()

    closes_hist = []
    emas = {}
    ep = [3, 12, 24, 64, 128, 500]

    bars = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M5, 0, 500)
    if bars is None or len(bars) < 100:
        print("ERROR: not enough bars"); mt5.shutdown(); return

    for b in bars:
        closes_hist.append(float(b["close"]))
    for p in ep:
        emas[p] = compute_ema(closes_hist, p)

    anchor = closes_hist[-1]
    nsl = 1
    nbl = 1
    idx = len(closes_hist) - 1
    last_status = 0

    os.makedirs(os.path.dirname(args.state_file) or ".", exist_ok=True)
    elog = open(args.event_log, "a")

    print(f"[{mode_str}] Running. Ctrl+C to stop.")

    try:
        while True:
            tick = mt5.symbol_info_tick(args.symbol)
            if not tick:
                time.sleep(args.poll_seconds)
                continue

            bid = tick.bid
            ask = tick.ask
            ts = datetime.fromtimestamp(tick.time, tz=timezone.utc).strftime("%H:%M:%S")

            # SAFETY: Max runtime check
            elapsed_min = (time.time() - start_time) / 60
            if args.max_runtime_minutes and elapsed_min >= args.max_runtime_minutes:
                print(f"  {ts} MAX RUNTIME reached ({elapsed_min:.0f}min). Shutting down.")
                break

            # SAFETY: Spread gate
            current_spread_pips = (ask - bid) / info.point
            spread_blocked = False
            if args.spread_limit_pips and current_spread_pips > args.spread_limit_pips:
                spread_blocked = True

            # Fetch new bars
            nb = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M5, 0, max(500, idx + 10))
            if nb is not None and len(nb) > idx:
                for b in nb[idx:]:
                    closes_hist.append(float(b["close"]))
                for p in ep:
                    emas[p] = compute_ema(closes_hist, p)
                idx = len(closes_hist) - 1

            step = args.step
            span = abs(emas[3][idx] - emas[500][idx])
            compressed = span <= (step * 3.0)
            trend_up = emas[3][idx] > emas[12][idx] > emas[24][idx] > emas[64][idx] and span >= step * 4.0
            trend_down = emas[3][idx] < emas[12][idx] < emas[24][idx] < emas[64][idx] and span >= step * 4.0

            if compressed:
                s = max(step * 0.75, info.spread * info.point * 3)
            elif trend_up or trend_down:
                s = step * 1.5
            else:
                s = step

            sd = 2 if trend_up else 1
            bd = 2 if trend_down else 1

            # Live/demo positions
            if args.demo:
                ls = [p for p in demo_inv if p["d"] == "S"]
                lb = [p for p in demo_inv if p["d"] == "B"]
            else:
                lp = get_live_positions(args.symbol)
                ls = [p for p in lp if p.type == 1]
                lb = [p for p in lp if p.type == 0]

            # === Opens ===
            if not spread_blocked:
                while ask >= anchor + (nsl * s) and len(ls) < args.max_open:
                    if sd <= 1 or nsl % sd == 0:
                        entry = round(anchor + (nsl * s), digits)
                        if args.demo:
                            demo_inv.append({"d": "S", "e": entry, "v": vol, "t": 999000 + len(demo_inv), "ts": ts})
                            ls = [p for p in demo_inv if p["d"] == "S"]
                            print(f"  {ts} DEMO SELL  {entry:.5f} L{nsl}")
                        else:
                            tid, err = open_position(args.symbol, "SELL", vol, entry, f"CC-S{nsl}", fm)
                            if tid:
                                print(f"  {ts} OPEN SELL  {entry:.5f} #{tid}")
                            else:
                                print(f"  {ts} FAIL SELL  {entry:.5f} {err}")
                        elog.write(json.dumps({"t": ts, "a": "open_sell", "e": entry, "l": nsl}) + "\n")
                    nsl += 1

                while bid <= anchor - (nbl * s) and len(lb) < args.max_open:
                    if bd <= 1 or nbl % bd == 0:
                        entry = round(anchor - (nbl * s), digits)
                        if args.demo:
                            demo_inv.append({"d": "B", "e": entry, "v": vol, "t": 998000 + len(demo_inv), "ts": ts})
                            lb = [p for p in demo_inv if p["d"] == "B"]
                            print(f"  {ts} DEMO BUY   {entry:.5f} L{nbl}")
                        else:
                            tid, err = open_position(args.symbol, "BUY", vol, entry, f"CC-B{nbl}", fm)
                            if tid:
                                print(f"  {ts} OPEN BUY   {entry:.5f} #{tid}")
                            else:
                                print(f"  {ts} FAIL BUY   {entry:.5f} {err}")
                        elog.write(json.dumps({"t": ts, "a": "open_buy", "e": entry, "l": nbl}) + "\n")
                    nbl += 1

            if args.demo:
                ls = [p for p in demo_inv if p["d"] == "S"]
                lb = [p for p in demo_inv if p["d"] == "B"]

            # === Floating PnL ===
            flt = 0.0
            if args.demo:
                for p in demo_inv:
                    if p["d"] == "S":
                        flt += (p["e"] - ask) * point_pnl
                    else:
                        flt += (bid - p["e"]) * point_pnl
            else:
                for p in get_live_positions(args.symbol):
                    flt += p.profit + p.swap + p.commission
            if flt < worst_float:
                worst_float = flt

            # SAFETY: Max floating loss forced unwind
            if args.max_float_loss is not None and flt <= args.max_float_loss:
                print(f"  {ts} FORCED UNWIND flt=${flt:.2f} cap=${args.max_float_loss}")
                # Close all positions
                for p in list(demo_inv if args.demo else get_live_positions(args.symbol)):
                    entry = p["e"] if args.demo else p.price_open
                    direction = p["d"] if args.demo else ("BUY" if p.type == 0 else "SELL")
                    if direction == "S" or (not args.demo and p.type == 1):
                        pnl = (entry - bid) * point_pnl
                        realized_net += pnl
                        realized_closes += 1
                        if args.demo:
                            demo_inv.remove(p)
                        else:
                            close_position(args.symbol, "SELL", vol, p.ticket, "FORCE", fm)
                    elif direction == "B" or (not args.demo and p.type == 0):
                        pnl = (ask - entry) * point_pnl
                        realized_net += pnl
                        realized_closes += 1
                        if args.demo:
                            demo_inv.remove(p)
                        else:
                            close_position(args.symbol, "BUY", vol, p.ticket, "FORCE", fm)
                if not demo_inv:
                    anchor = bid
                    nsl = 1; nbl = 1
                    anchor_resets += 1
                continue

            # === Cascade SELL close ===
            if ls:
                low_entry = min(p["e"] if args.demo else p.price_open for p in ls)
                if bid <= low_entry:
                    for p in list(ls):
                        entry = p["e"] if args.demo else p.price_open
                        if args.demo:
                            pnl = (entry - bid) * point_pnl
                            realized_net += pnl
                            realized_closes += 1
                            demo_inv.remove(p)
                            print(f"  {ts} CLOSE SELL {entry:.5f}->{bid:.5f} ${pnl:.2f}")
                        else:
                            res, err = close_position(args.symbol, "SELL", vol, p.ticket, "CC-C", fm)
                            if res:
                                realized_net += res.profit
                                realized_closes += 1
                                print(f"  {ts} CLOSE SELL #{p.ticket} ${res.profit:.2f}")
                            else:
                                print(f"  {ts} FAIL CLO #{p.ticket} {err}")
                        elog.write(json.dumps({"t": ts, "a": "close_sell", "e": entry, "x": bid}) + "\n")

                        # Counter BUY
                        oso = len([p for p in demo_inv if p["d"] == "S"]) if args.demo else len(ls)
                        if oso < args.max_open:
                            ce = round(bid - s * 0.5, digits)
                            counter_opens += 1
                            if args.demo:
                                demo_inv.append({"d": "B", "e": ce, "v": vol, "t": 997000 + len(demo_inv), "ts": ts})
                                print(f"  {ts} CTR BUY    {ce:.5f}")
                            else:
                                tid, err = open_position(args.symbol, "BUY", vol, ce, "CC-CTR", fm)
                                if tid:
                                    print(f"  {ts} CTR BUY    {ce:.5f} #{tid}")
                            elog.write(json.dumps({"t": ts, "a": "counter_buy", "e": ce}) + "\n")

            if args.demo:
                ls = [p for p in demo_inv if p["d"] == "S"]
                lb = [p for p in demo_inv if p["d"] == "B"]

            # === Cascade BUY close ===
            if lb:
                hi_entry = max(p["e"] if args.demo else p.price_open for p in lb)
                if ask >= hi_entry:
                    for p in list(lb):
                        entry = p["e"] if args.demo else p.price_open
                        if args.demo:
                            pnl = (ask - entry) * point_pnl
                            realized_net += pnl
                            realized_closes += 1
                            demo_inv.remove(p)
                            print(f"  {ts} CLOSE BUY  {entry:.5f}->{ask:.5f} ${pnl:.2f}")
                        else:
                            res, err = close_position(args.symbol, "BUY", vol, p.ticket, "CC-C", fm)
                            if res:
                                realized_net += res.profit
                                realized_closes += 1
                                print(f"  {ts} CLOSE BUY  #{p.ticket} ${res.profit:.2f}")
                            else:
                                print(f"  {ts} FAIL CLO #{p.ticket} {err}")
                        elog.write(json.dumps({"t": ts, "a": "close_buy", "e": entry, "x": ask}) + "\n")

                        # Counter SELL
                        obo = len([p for p in demo_inv if p["d"] == "B"]) if args.demo else len(lb)
                        if obo < args.max_open:
                            ce = round(ask + s * 0.5, digits)
                            counter_opens += 1
                            if args.demo:
                                demo_inv.append({"d": "S", "e": ce, "v": vol, "t": 996000 + len(demo_inv), "ts": ts})
                                print(f"  {ts} CTR SELL   {ce:.5f}")
                            else:
                                tid, err = open_position(args.symbol, "SELL", vol, ce, "CC-CTR", fm)
                                if tid:
                                    print(f"  {ts} CTR SELL   {ce:.5f} #{tid}")
                            elog.write(json.dumps({"t": ts, "a": "counter_sell", "e": ce}) + "\n")

            if args.demo:
                lb = [p for p in demo_inv if p["d"] == "B"]
                ls = [p for p in demo_inv if p["d"] == "S"]

            n_open = len(ls) + len(lb)
            max_open_seen = max(max_open_seen, n_open)

            # Anchor reset
            if not demo_inv and not get_live_positions(args.symbol) and abs(bid - anchor) >= s:
                anchor = bid
                nsl = 1; nbl = 1
                anchor_resets += 1

            # Status every 30s
            elapsed = time.time() - start_time
            if elapsed - last_status >= 30:
                last_status = elapsed
                st = {"symbol": args.symbol, "mode": mode_str,
                      "realized_net_usd": round(realized_net, 3),
                      "realized_closes": realized_closes,
                      "counter_opens": counter_opens,
                      "worst_floating_usd": round(worst_float, 3),
                      "max_open_total": max_open_seen,
                      "anchor_resets": anchor_resets,
                      "runtime_seconds": round(elapsed, 1),
                      "open_positions": n_open}
                with open(args.state_file, "w") as f:
                    json.dump(st, f, indent=2)
                elog.flush()
                hr = realized_net / (elapsed_min / 60) if elapsed_min > 0.1 else 0
                print(f"  {ts} STAT open={n_open} net=${realized_net:.2f} closes={realized_closes} flt=${flt:.2f} rate=${hr:.2f}/hr spread={current_spread_pips:.1f}p elapsed={elapsed_min:.1f}min")

            time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        print(f"\n{'='*50}")
        print(f"SHUTDOWN {mode_str} {args.symbol}")
        print(f"Runtime:  {elapsed/60:.1f}min")
        print(f"Closes:   {realized_closes}")
        print(f"Net:      ${realized_net:.2f}")
        print(f"$/hr:     ${realized_net/(elapsed/3600):.2f}" if elapsed > 10 else "$/hr:   N/A")
        print(f"Counter:  {counter_opens}")
        print(f"Worst flt: ${worst_float:.2f}")
        print(f"Max open: {max_open_seen}")
        print(f"Resets:   {anchor_resets}")

        if not args.demo:
            for p in get_live_positions(args.symbol):
                d = "BUY" if p.type == 0 else "SELL"
                print(f"  Closing #{p.ticket} ({d})...")
                close_position(args.symbol, d, p.volume, p.ticket, "SHUTDOWN", fm)
        else:
            print(f"  Demo positions remaining: {len(demo_inv)}")

        elog.close()
        mt5.shutdown()

if __name__ == "__main__":
    main()
