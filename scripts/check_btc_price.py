import MetaTrader5 as mt5
mt5.initialize()
info = mt5.symbol_info('BTCUSD')
with open('reports/btc_price.txt', 'w') as f:
    if info:
        f.write(f'BTCUSD current: bid={info.bid}, ask={info.ask}\n')
        f.write(f'Spread: {info.spread * info.point:.2f}\n')
        f.write(f'Anchor: 72889.73\n')
        f.write(f'Distance from anchor: bid={info.bid - 72889.73:.2f}, ask={info.ask - 72889.73:.2f}\n')
        f.write(f'Next sell level: {72889.73 + 50}\n')
        f.write(f'Next buy level: {72889.73 - 50}\n')
    else:
        f.write('BTCUSD not found\n')
mt5.shutdown()

with open('reports/btc_price.txt') as f:
    print(f.read())
