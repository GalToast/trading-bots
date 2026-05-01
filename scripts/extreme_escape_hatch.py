"""
Extreme Escape Hatch — Cheapest Exit from Negative Lattice Positions
When the lattice is at extremes and positions go negative, this cuts the worst
positions at a defined loss threshold, freeing the grid to rebuild.

This is SURGICAL — not the max_floating_loss kill switch that kills everything.
It cuts the worst 1-3 positions and lets the rest continue.

Usage:
  python scripts/extreme_escape_hatch.py --symbol GBPUSD --timeframe M15
  python scripts/extreme_escape_hatch.py --symbol GBPUSD --timeframe M15 --dry-run
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

def get_open_positions(symbol, magic=None):
    """Get all open positions for a symbol."""
    mt5.initialize()
    if magic:
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            positions = [p for p in positions if p.magic == magic]
    else:
        positions = mt5.positions_get(symbol=symbol)
    mt5.shutdown()
    return positions or []

def compute_position_floating_pnl(position):
    """Compute floating PnL for a single position."""
    mt5.initialize()
    tick = mt5.symbol_info_tick(position.symbol)
    mt5.shutdown()
    if tick is None:
        return 0.0

    if position.type == 0:  # BUY
        pnl = (tick.bid - position.price_open) * position.volume * 100000 / 100  # rough pip value
    else:  # SELL
        pnl = (position.price_open - tick.ask) * position.volume * 100000 / 100
    return pnl

def find_worst_positions(positions, n=1):
    """Find the N worst positions by floating PnL."""
    pnls = []
    mt5.initialize()
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            continue
        if pos.type == 0:  # BUY
            pnl = (tick.bid - pos.price_open) * pos.volume * 100000 / 100
        else:  # SELL
            pnl = (pos.price_open - tick.ask) * pos.volume * 100000 / 100
        pnls.append((pos, pnl))
    mt5.shutdown()

    pnls.sort(key=lambda x: x[1])  # worst first
    return pnls[:n]

def escape_extreme_positions(symbol, magic, cut_count=1, max_loss_per_position=5.0, dry_run=True):
    """
    Cut the worst N positions at extremes.

    Args:
        symbol: Symbol to check
        magic: Magic number of the lane
        cut_count: Number of worst positions to cut (1-3)
        max_loss_per_position: Maximum loss to accept per cut position ($5 default)
        dry_run: If True, just report; if False, actually close
    """
    positions = get_open_positions(symbol, magic)
    if not positions:
        print(f"No open positions for {symbol} magic={magic}")
        return {'status': 'no_positions'}

    total_floating = sum(compute_position_floating_pnl(p) for p in positions)
    worst = find_worst_positions(positions, cut_count)

    print(f"=== EXTREME ESCAPE HATCH ===")
    print(f"Symbol: {symbol}, Magic: {magic}")
    print(f"Total positions: {len(positions)}")
    print(f"Total floating PnL: ${total_floating:.2f}")
    print(f"Worst {cut_count} positions:")

    escape_results = []
    for pos, pnl in worst:
        action = "CUT" if pnl < -max_loss_per_position else "KEEP"
        print(f"  Ticket {pos.ticket}: {'SELL' if pos.type else 'BUY'} "
              f"@ {pos.price_open}, floating ${pnl:.2f} -> {action}")
        escape_results.append({
            'ticket': pos.ticket,
            'type': 'SELL' if pos.type else 'BUY',
            'entry': pos.price_open,
            'floating_pnl': round(pnl, 2),
            'action': action,
        })

    # Count how many are worth cutting
    cuts_needed = sum(1 for _, pnl in worst if pnl < -max_loss_per_position)

    if cuts_needed == 0:
        print(f"\nNo positions exceed loss threshold of -${max_loss_per_position}. No action needed.")
        return {'status': 'no_cuts_needed', 'results': escape_results}

    print(f"\nPositions to cut: {cuts_needed}")
    print(f"Estimated total loss from cuts: ${sum(pnl for _, pnl in worst if pnl < -max_loss_per_position):.2f}")

    if not dry_run:
        print("\nExecuting cuts...")
        mt5.initialize()
        cuts_executed = 0
        for pos, pnl in worst:
            if pnl < -max_loss_per_position:
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
                    "position": pos.ticket,
                    "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == 0 else mt5.symbol_info_tick(pos.symbol).ask,
                    "deviation": 20,
                    "magic": pos.magic,
                    "comment": "EXTREME_ESCAPE",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"  Cut ticket {pos.ticket} at ${pnl:.2f} loss")
                    cuts_executed += 1
                else:
                    print(f"  Failed to cut ticket {pos.ticket}: {result.comment}")
        mt5.shutdown()
        print(f"\nCuts executed: {cuts_executed}/{cuts_needed}")
        return {'status': 'cuts_executed', 'executed': cuts_executed, 'results': escape_results}
    else:
        print(f"\nDRY RUN — no positions were closed.")
        return {'status': 'dry_run', 'results': escape_results}

def main():
    parser = argparse.ArgumentParser(description='Extreme Escape Hatch')
    parser.add_argument('--symbol', required=True, help='Symbol to check')
    parser.add_argument('--magic', type=int, required=True, help='Magic number')
    parser.add_argument('--cut-count', type=int, default=1, help='Worst positions to cut (1-3)')
    parser.add_argument('--max-loss', type=float, default=5.0, help='Max loss per position ($)')
    parser.add_argument('--dry-run', action='store_true', help='Report only, don\'t close')
    args = parser.parse_args()

    result = escape_extreme_positions(
        args.symbol, args.magic, args.cut_count, args.max_loss, args.dry_run
    )

    # Save report
    report_path = ROOT / 'reports' / 'extreme_escape_hatch_report.json'
    report = {
        'generated_at': utc_now_iso(),
        'symbol': args.symbol,
        'magic': args.magic,
        'cut_count': args.cut_count,
        'max_loss_per_position': args.max_loss,
        'dry_run': args.dry_run,
        'result': result,
    }
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to {report_path}")

if __name__ == '__main__':
    main()
