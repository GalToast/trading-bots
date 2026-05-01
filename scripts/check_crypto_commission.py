import MetaTrader5 as mt5
mt5.initialize()
with open('reports/crypto_commission.txt', 'w') as f:
    for sym in ['BTCUSD', 'ETHUSD', 'SOLUSD']:
        info = mt5.symbol_info(sym)
        if info:
            f.write(f'{sym}:\n')
            f.write(f'  Spread: {info.spread} points = {info.spread * info.point:.2f}\n')
            f.write(f'  Point: {info.point}\n')
            f.write(f'  Trade contract size: {info.trade_contract_size}\n')
            f.write(f'  Commission: {info.commission}\n')
            f.write(f'  Commission type: {info.commission_type}\n')
            f.write(f'  Margin initial: {info.margin_initial}\n')
            f.write(f'  Margin maintenance: {info.margin_maintenance}\n')
            f.write(f'  Tick size: {info.trade_tick_size}\n')
            f.write(f'  Tick value: {info.trade_tick_value}\n')
            f.write(f'  Volume limit: {info.volume_limit}\n')
            f.write(f'  Volume max: {info.volume_max}\n')
            f.write(f'  Volume min: {info.volume_min}\n')
            f.write(f'  Volume step: {info.volume_step}\n')
            f.write('\n')
        else:
            f.write(f'{sym}: NOT FOUND\n\n')
mt5.shutdown()

with open('reports/crypto_commission.txt') as f:
    print(f.read())
