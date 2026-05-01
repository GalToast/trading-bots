"""
Breakeven Escape Mechanism — $0 Cost Exit from Stale Lattice Positions
Closes positions that have been open for N bars without reaching profitability.
Cost: $0 (closes at breakeven or small profit). Frees the grid to rebuild.

This is the CHEAPEST escape mechanism — it costs nothing but time.

Usage:
  python scripts/breakeven_escape.py --symbol GBPUSD --magic 941800 --max-bars 20
  python scripts/breakeven_escape.py --symbol GBPUSD --magic 941800 --max-bars 20 --execute
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def find_stale_unprofitable_positions(symbol, magic, max_bars, timeframe=mt5.TIMEFRAME_M15):
    """Find positions open for >max_bars that aren't profitable."""
    mt5.initialize()
    positions = mt5.positions_get(symbol=symbol)
    if magic:
        positions = [p for p in positions if p.magic == magic]
    if not positions:
        mt5.shutdown()
        return []

    # Get current bar count
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, max_bars + 1)
    current_bar_time = rates[-1]['time'] if rates is not None else 0
    open_bar_time = current_bar_time - (max_bars * _timeframe_seconds(timeframe))

    stale_positions = []
    for pos in positions:
        # Check how long position has been open
        pos_bar_time = pos.time  # position open time
        if pos_bar_time < open_bar_time:
            # Position has been open for more than max_bars
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                continue
            if pos.type == 0:  # BUY
                pnl = (tick.bid - pos.price_open) * pos.volume * 100000 / 100
            else:  # SELL
                pnl = (pos.price_open - tick.ask) * pos.volume * 100000 / 100

            bars_open = int((current_bar_time - pos_bar_time) / _timeframe_seconds(timeframe))

            stale_positions.append({
                'ticket': pos.ticket,
                'type': 'SELL' if pos.type else 'BUY',
                'entry': pos.price_open,
                'floating_pnl': round(pnl, 2),
                'bars_open': bars_open,
                'open_time': pos_bar_time,
                'volume': pos.volume,
            })

    mt5.shutdown()
    return stale_positions

def _timeframe_seconds(tf):
    if tf == mt5.TIMEFRAME_M1: return 60
    if tf == mt5.TIMEFRAME_M5: return 300
    if tf == mt5.TIMEFRAME_M15: return 900
    if tf == mt5.TIMEFRAME_H1: return 3600
    return 900

def execute_breakeven_escape(stale_positions, symbol, max_loss=1.0):
    """Close stale positions at breakeven (or small acceptable loss)."""
    mt5.initialize()
    results = []

    for pos in stale_positions:
        # Only close if loss is acceptable (within max_loss of breakeven)
        if pos['floating_pnl'] >= -max_loss:
            # Close at market
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue

            close_type = mt5.ORDER_TYPE_SELL if pos['type'] == 'BUY' else mt5.ORDER_TYPE_BUY
            price = tick.bid if pos['type'] == 'BUY' else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": pos['volume'],
                "type": close_type,
                "position": pos['ticket'],
                "price": price,
                "deviation": 20,
                "magic": 0,
                "comment": "BREAKEVEN_ESCAPE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                actual_pnl = pos['floating_pnl']  # approximate
                results.append({
                    'ticket': pos['ticket'],
                    'status': 'closed',
                    'pnl': actual_pnl,
                    'bars_open': pos['bars_open'],
                })
            else:
                results.append({
                    'ticket': pos['ticket'],
                    'status': f'failed: {result.comment}',
                })
        else:
            results.append({
                'ticket': pos['ticket'],
                'status': 'skipped',
                'reason': f'loss too large: ${pos["floating_pnl"]:.2f}',
            })

    mt5.shutdown()
    return results

def main():
    parser = argparse.ArgumentParser(description='Breakeven Escape Mechanism')
    parser.add_argument('--symbol', required=True, help='Symbol to check')
    parser.add_argument('--magic', type=int, required=True, help='Magic number')
    parser.add_argument('--max-bars', type=int, default=20, help='Max bars before escape')
    parser.add_argument('--max-loss', type=float, default=1.0, help='Max acceptable loss ($)')
    parser.add_argument('--execute', action='store_true', help='Actually close positions')
    args = parser.parse_args()

    stale = find_stale_unprofitable_positions(args.symbol, args.magic, args.max_bars)

    if not stale:
        print(f"No stale positions for {args.symbol} magic={args.magic}")
        return

    print(f"=== BREAKEVEN ESCAPE MECHANISM ===")
    print(f"Symbol: {args.symbol}, Magic: {args.magic}")
    print(f"Max bars: {args.max_bars}, Max loss: ${args.max_loss}")
    print(f"Stale positions found: {len(stale)}")
    for pos in stale:
        print(f"  Ticket {pos['ticket']}: {pos['type']} @ {pos['entry']}, "
              f"PnL=${pos['floating_pnl']:.2f}, Bars open: {pos['bars_open']}")

    if args.execute:
        print(f"\nExecuting breakeven escapes...")
        results = execute_breakeven_escape(stale, args.symbol, args.max_loss)
        for r in results:
            print(f"  Ticket {r['ticket']}: {r['status']}")
    else:
        print(f"\nDRY RUN — use --execute to actually close positions.")

    # Save report
    report_path = ROOT / 'reports' / 'breakeven_escape_report.json'
    report = {
        'generated_at': utc_now_iso(),
        'symbol': args.symbol,
        'magic': args.magic,
        'max_bars': args.max_bars,
        'max_loss': args.max_loss,
        'stale_count': len(stale),
        'stale_positions': stale,
        'dry_run': not args.execute,
    }
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to {report_path}")

if __name__ == '__main__':
    main()
