import MetaTrader5 as mt5
mt5.initialize()

with open('reports/crypto_commission.txt', 'w') as f:
    info = mt5.symbol_info('BTCUSD')
    if info:
        f.write(f'BTCUSD:\n')
        f.write(f'  trade_contract_size: {info.trade_contract_size}\n')
        f.write(f'  trade_calc_mode: {info.trade_calc_mode}\n')
        f.write(f'  trade_tick_value: {info.trade_tick_value}\n')
        f.write(f'  trade_tick_value_profit: {info.trade_tick_value_profit}\n')
        f.write(f'  trade_tick_value_loss: {info.trade_tick_value_loss}\n')
        f.write(f'  currency_base: {info.currency_base}\n')
        f.write(f'  currency_profit: {info.currency_profit}\n')
        f.write(f'  spread: {info.spread}\n')
        f.write(f'  point: {info.point}\n')
        f.write(f'  spread_cost: {info.spread * info.point:.2f}\n')
        f.write(f'  swap_long: {info.swap_long}\n')
        f.write(f'  swap_short: {info.swap_short}\n')
        f.write(f'  trade_mode: {info.trade_mode}\n')
        f.write(f'  trade_exemode: {info.trade_exemode}\n')
        f.write(f'  trade_liquidity_rate: {info.trade_liquidity_rate}\n')

        # Test order_calc_profit
        profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, 'BTCUSD', 0.01, 100000, 100100)
        f.write(f'\nTest: BUY 0.01 BTC at 100000, sell at 100100 = profit ${profit}\n')

        # Check account info
        acct = mt5.account_info()
        if acct:
            f.write(f'\nAccount:\n')
            f.write(f'  Trade mode: {acct.trade_mode}\n')
            f.write(f'  Leverage: {acct.leverage}\n')
            f.write(f'  Margin call mode: {acct.margin_so_mode}\n')
            f.write(f'  Limit orders: {acct.limit_orders}\n')
            f.write(f'  Margin trade: {acct.margin_trade}\n')
            f.write(f'  Balance: {acct.balance}\n')
            f.write(f'  Equity: {acct.equity}\n')
    else:
        f.write('BTCUSD not found\n')

mt5.shutdown()

with open('reports/crypto_commission.txt') as f:
    print(f.read())
