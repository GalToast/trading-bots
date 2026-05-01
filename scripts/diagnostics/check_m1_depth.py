import MetaTrader5 as mt5
mt5.initialize()
with open('m1_depth.txt', 'w') as f:
    for offset in [0, 10000, 50000, 100000, 200000]:
        rates = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M1, offset, 10000)
        if rates is not None and len(rates) > 0:
            f.write(f'offset={offset}: got {len(rates)} bars, last_ts={rates[-1][0]}\n')
        else:
            f.write(f'offset={offset}: no data\n')
            break
mt5.shutdown()
