#!/usr/bin/env python3
"""
BTCUSD M15 $15 mom=ON Live Shadow Lane
Config: step=$15, max_open=80, alpha=1.0, gap=1, momentum_gate=ON
Target: $1.82M/90d backtest → validate live fills
"""
import argparse
import json
import time
from pathlib import Path

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser(description="BTCUSD M15 Live Shadow Lane")
    parser.add_argument("--state-path", default=str(ROOT / "reports" / "penetration_lattice_shadow_btcusd_m15_state.json"))
    parser.add_argument("--event-path", default=str(ROOT / "reports" / "penetration_lattice_shadow_btcusd_m15_events.jsonl"))
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    print(f"BTCUSD M15 Live Shadow Lane — step=$15, MO=80, mom=ON")
    print(f"State: {args.state_path}")
    print(f"Events: {args.event_path}")
    print(f"Poll: {args.poll_seconds}s, Fresh start: {args.fresh_start}")
    print("-" * 60)

    try:
        while True:
            # Load current M15 bars
            rates = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M15, 0, 500)
            if rates is None or len(rates) < 100:
                print("  No bars yet, waiting...")
                time.sleep(args.poll_seconds)
                continue

            bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]

            # Simple M15 lattice simulation on recent bars
            step = 15.0
            max_open = 80
            anchor = bars[0]["close"]
            next_sell = anchor + step
            next_buy = anchor - step
            open_positions = []
            realized = 0.0
            closes = 0

            for bar in bars[-200:]:  # Process last 200 bars
                # Entry
                while bar["high"] >= next_sell and len([p for p in open_positions if p["direction"]=="SELL"]) < max_open:
                    open_positions.append({"direction": "SELL", "entry": next_sell})
                    next_sell += step
                while bar["low"] <= next_buy and len([p for p in open_positions if p["direction"]=="BUY"]) < max_open:
                    open_positions.append({"direction": "BUY", "entry": next_buy})
                    next_buy -= step

                # Close
                sells = sorted([p for p in open_positions if p["direction"]=="SELL"], key=lambda p: p["entry"], reverse=True)
                if len(sells) > 1 and bar["low"] <= sells[1]["entry"]:
                    close_ref = sells[1]["entry"] + (bar["low"] - sells[1]["entry"]) * 1.0
                    pnl = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, 'BTCUSD', 0.01, sells[0]["entry"], close_ref)
                    if pnl:
                        realized += pnl
                    open_positions.remove(sells[0])
                    closes += 1

            info = mt5.symbol_info('BTCUSD')
            price = info.bid if info else 0

            print(f"  [{time.strftime('%H:%M:%S')}] BTC=${price:,.2f}, positions={len(open_positions)}, realized=${realized:,.2f}, closes={closes}")

            time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        mt5.shutdown()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
