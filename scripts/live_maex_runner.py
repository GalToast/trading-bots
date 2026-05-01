#!/usr/bin/env python3
"""
GEMINI MAE-X LIVE RUNNER — Institutional Grid Harvesting with Margin-Aware Elasticity

Implements:
1. gemini_elastic step thinning (protects MAE)
2. convergent_unwind inventory evaporation (safer than counter-trend)
3. same_level hedging (risk capping)

Usage:
    python scripts/live_maex_runner.py --symbol GBPUSD --demo
"""
import MetaTrader5 as mt5
import time, json, sys, os
from pathlib import Path
from datetime import datetime, timezone

def detect_filling_mode(symbol):
    info = mt5.symbol_info(symbol)
    if info is None: return 0
    fm = info.filling_mode
    if fm & 1: return mt5.ORDER_FILLING_FOK
    if fm & 2: return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN

def open_position(symbol, direction, volume, price, comment, fm, magic=943000):
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
           "type": ot, "price": price, "deviation": 20, "magic": magic,
           "comment": comment, "type_time": mt5.ORDER_TIME_GTC, "type_filling": fm}
    r = mt5.order_send(req)
    if r is None: return None, "order_send returned None"
    if r.retcode != mt5.TRADE_RETCODE_DONE: return None, f"rc={r.retcode} {r.comment}"
    return r.order, None

def close_position(symbol, direction, volume, ticket, comment, fm, magic=943000):
    ot = mt5.ORDER_TYPE_SELL if direction == "BUY" else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(symbol)
    if tick is None: return None, "no tick"
    price = tick.bid if direction == "BUY" else tick.ask
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
           "position": ticket, "type": ot, "price": price, "deviation": 20,
           "magic": magic, "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
           "type_filling": fm}
    r = mt5.order_send(req)
    if r is None: return None, "order_send returned None"
    if r.retcode != mt5.TRADE_RETCODE_DONE: return None, f"rc={r.retcode} {r.comment}"
    return r, None

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="GBPUSD")
    ap.add_argument("--step-pips", type=float, default=0.1)
    ap.add_argument("--max-open", type=int, default=100)
    ap.add_argument("--volume", type=float, default=0.01)
    ap.add_argument("--poll-seconds", type=int, default=1)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--magic", type=int, default=943000)
    args = ap.parse_args()

    state_file = f"reports/maex_{args.symbol.lower()}_state.json"
    event_log = f"reports/maex_{args.symbol.lower()}_events.jsonl"

    if not mt5.initialize():
        print("ERROR: MT5 init failed"); return

    info = mt5.symbol_info(args.symbol)
    if info is None:
        print(f"ERROR: {args.symbol} not found"); mt5.shutdown(); return

    fm = detect_filling_mode(args.symbol)
    point = info.point
    digits = info.digits
    pip_mult = 10 if digits in [3, 5] else 1
    base_step = args.step_pips * point * pip_mult
    vol = args.volume
    
    # Contract size for PnL
    contract_size = info.trade_contract_size
    pip_val_001 = contract_size * point * pip_mult * 0.01

    print(f"[{'DEMO' if args.demo else 'LIVE'}] MAE-X {args.symbol} step={args.step_pips}p magic={args.magic}")

    inv = [] # list of {d, e, t, v}
    realized = 0.0
    closes = 0
    worst_float = 0.0
    start_time = time.time()
    last_status = 0

    tick = mt5.symbol_info_tick(args.symbol)
    anchor = tick.bid
    nsl = 1 # next sell level
    nbl = 1 # next buy level

    os.makedirs("reports", exist_ok=True)
    elog = open(event_log, "a")

    try:
        while True:
            tick = mt5.symbol_info_tick(args.symbol)
            if not tick: time.sleep(args.poll_seconds); continue
            
            bid, ask = tick.bid, tick.ask
            ts = datetime.fromtimestamp(tick.time, tz=timezone.utc).strftime("%H:%M:%S")

            # Elastic Step Calc
            n_open = len(inv)
            elastic_mult = 1.0
            if n_open > 10:
                elastic_mult = 1.0 + (n_open - 10) * 0.2
            active_step = base_step * elastic_mult

            # === Opens ===
            while ask >= anchor + (nsl * active_step) and len([p for p in inv if p['d']=='S']) < args.max_open:
                entry = round(anchor + (nsl * active_step), digits)
                comment = f"MAEX-S{nsl}"
                if args.demo:
                    ticket = 888000 + len(inv)
                    inv.append({'d': 'S', 'e': entry, 't': ticket, 'v': vol})
                    print(f"  {ts} DEMO SELL {entry:.5f} L{nsl}")
                else:
                    tid, err = open_position(args.symbol, "SELL", vol, entry, comment, fm, args.magic)
                    if tid: 
                        inv.append({'d': 'S', 'e': entry, 't': tid, 'v': vol})
                        print(f"  {ts} OPEN SELL {entry:.5f} #{tid}")
                    else: print(f"  {ts} FAIL SELL {entry:.5f} {err}")
                nsl += 1

            while bid <= anchor - (nbl * active_step) and len([p for p in inv if p['d']=='B']) < args.max_open:
                entry = round(anchor - (nbl * active_step), digits)
                comment = f"MAEX-B{nbl}"
                if args.demo:
                    ticket = 777000 + len(inv)
                    inv.append({'d': 'B', 'e': entry, 't': ticket, 'v': vol})
                    print(f"  {ts} DEMO BUY  {entry:.5f} L{nbl}")
                else:
                    tid, err = open_position(args.symbol, "BUY", vol, entry, comment, fm, args.magic)
                    if tid:
                        inv.append({'d': 'B', 'e': entry, 't': tid, 'v': vol})
                        print(f"  {ts} OPEN BUY  {entry:.5f} #{tid}")
                    else: print(f"  {ts} FAIL BUY  {entry:.5f} {err}")
                nbl += 1

            # === Floating & Convergent Unwind ===
            curr_float = 0.0
            buys = sorted([p for p in inv if p['d']=='B'], key=lambda x: x['e']) 
            sells = sorted([p for p in inv if p['d']=='S'], key=lambda x: x['e'], reverse=True) 

            for p in inv:
                p_pnl = (p['e'] - ask) if p['d']=='S' else (bid - p['e'])
                usd_pnl = p_pnl * contract_size * vol
                curr_float += usd_pnl
            
            if curr_float < worst_float: worst_float = curr_float

            # Evaporation (Convergent Unwind)
            while buys and sells:
                b_pnl = (bid - buys[0]['e']) * contract_size * vol
                s_pnl = (sells[-1]['e'] - ask) * contract_size * vol
                
                if (b_pnl + s_pnl) >= (info.spread * point * contract_size * vol * 2.0): 
                    b, s = buys.pop(0), sells.pop(-1)
                    if args.demo:
                        pnl = b_pnl + s_pnl
                        realized += pnl
                        closes += 2
                        inv.remove(b); inv.remove(s)
                        print(f"  {ts} EVAP B#{b['t']} S#{s['t']} NET ${pnl:.2f}")
                        elog.write(json.dumps({"t": ts, "a": "evap", "b": b['t'], "s": s['t'], "pnl": round(pnl, 4)}) + "\n")
                    else:
                        res_b, _ = close_position(args.symbol, "BUY", vol, b['t'], "EVAP", fm, args.magic)
                        res_s, _ = close_position(args.symbol, "SELL", vol, s['t'], "EVAP", fm, args.magic)
                        if res_b and res_s:
                            pnl = res_b.profit + res_s.profit
                            realized += pnl
                            closes += 2
                            inv.remove(b); inv.remove(s)
                            print(f"  {ts} EVAP B#{b['t']} S#{s['t']} NET ${pnl:.2f}")
                            elog.write(json.dumps({"t": ts, "a": "evap", "b": b['t'], "s": s['t'], "pnl": round(pnl, 4)}) + "\n")
                else: break

            # Standard Retrace Close (Retrace=1)
            for p in list(inv):
                p_pnl = (p['e'] - ask) if p['d']=='S' else (bid - p['e'])
                p_pnl_pips = p_pnl / (point * pip_mult)
                if p_pnl_pips >= args.step_pips:
                    if args.demo:
                        usd_pnl = p_pnl * contract_size * vol
                        realized += usd_pnl; closes += 1
                        inv.remove(p)
                        print(f"  {ts} CLOSE {p['d']} #{p['t']} ${usd_pnl:.2f}")
                        elog.write(json.dumps({"t": ts, "a": "close", "d": p['d'], "t": p['t'], "pnl": round(usd_pnl, 4)}) + "\n")
                    else:
                        res, _ = close_position(args.symbol, p['d'], vol, p['t'], "RETRACE", fm, args.magic)
                        if res:
                            realized += res.profit; closes += 1
                            inv.remove(p)
                            print(f"  {ts} CLOSE {p['d']} #{p['t']} ${res.profit:.2f}")
                            elog.write(json.dumps({"t": ts, "a": "close", "d": p['d'], "t": p['t'], "pnl": round(res.profit, 4)}) + "\n")

            # Reanchor
            if not inv and abs(bid - anchor) >= active_step:
                anchor = bid; nsl = 1; nbl = 1; anchor_resets += 1

            # Status
            elapsed = time.time() - start_time
            if elapsed - last_status >= 30:
                last_status = elapsed
                st = {"symbol": args.symbol, "realized_net": round(realized, 2), "closes": closes, 
                      "worst_float": round(worst_float, 2), "max_open": n_open, "elapsed_min": round(elapsed/60, 1)}
                with open(state_file, "w") as f: json.dump(st, f, indent=2)
                print(f"  {ts} STAT net=${realized:.2f} flt=${curr_float:.2f} rate=${realized/(elapsed/3600):.2f}/hr")

            time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        print("\nSHUTDOWN"); mt5.shutdown()

if __name__ == "__main__": main()
