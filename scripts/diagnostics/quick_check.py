import MetaTrader5 as mt5
mt5.initialize()
acct = mt5.account_info()
pos = mt5.positions_get()
total_pl = sum(p.profit for p in pos) if pos else 0
print(f"Balance: ${acct.balance:.2f} | Equity: ${acct.equity:.2f}")
print(f"Positions: {len(pos) if pos else 0} | Floating P/L: ${total_pl:+.2f}")
if pos:
    for p in pos:
        direction = "BUY" if p.type == 0 else "SELL"
        print(f"  {p.symbol} {direction} {p.volume} @ {p.price_open:.5f} => ${p.profit:+.2f}")
mt5.shutdown()