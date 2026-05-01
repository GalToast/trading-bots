import MetaTrader5 as mt5
mt5.initialize()
positions = mt5.positions_get()
with open('reports/momentum_broker_check.txt', 'w') as f:
    if positions is None:
        f.write("No positions from MT5\n")
    else:
        mom_positions = [p for p in positions if p.magic == 941778]
        f.write(f'Momentum 941778: {len(mom_positions)} positions\n')
        total_profit = sum(p.profit for p in mom_positions)
        buys = [p for p in mom_positions if p.type == 0]
        sells = [p for p in mom_positions if p.type == 1]
        f.write(f'  BUYs: {len(buys)}, SELLs: {len(sells)}\n')
        f.write(f'  Total floating PnL: ${total_profit:.2f}\n')
        for p in mom_positions[:10]:
            f.write(f'  {p.symbol} {"BUY" if p.type==0 else "SELL"} {p.volume} lots, entry={p.price_open:.5f}, current={p.price_current:.5f}, PnL=${p.profit:.2f}\n')
        if len(mom_positions) > 10:
            f.write(f'  ...and {len(mom_positions)-10} more\n')

        # Also check other live magics
        for magic in [941777, 941781, 941782]:
            magic_pos = [p for p in positions if p.magic == magic]
            magic_profit = sum(p.profit for p in magic_pos)
            f.write(f'\nMagic {magic}: {len(magic_pos)} positions, PnL=${magic_profit:.2f}\n')

mt5.shutdown()
