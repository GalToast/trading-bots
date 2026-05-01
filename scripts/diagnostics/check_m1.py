import MetaTrader5 as mt5
mt5.initialize()
rates = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M1, 0, 10000)
with open('m1_info.txt', 'w') as f:
    f.write(f'rates is None: {rates is None}\n')
    if rates is not None:
        f.write(f'len: {len(rates)}\n')
        if len(rates) > 0:
            f.write(f'first_ts: {rates[0][0]}\n')
            f.write(f'last_ts: {rates[-1][0]}\n')
            f.write(f'days: {(rates[-1][0] - rates[0][0]) / 86400:.1f}\n')
mt5.shutdown()
