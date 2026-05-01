"""Quick check - what positions are open?"""
import MetaTrader5 as mt5
from mt5_config import LOGIN, PASSWORD, SERVER

mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER)

positions = mt5.positions_get()
if positions:
    print(f"Open positions: {len(positions)}")
    for p in positions:
        print(f"  {p.symbol}: {('BUY' if p.type == 0 else 'SELL')} {p.volume} @ {p.price_open} | P/L: ${p.profit:+.2f} | Comment: {p.comment}")
else:
    print("No open positions")

acct = mt5.account_info()
print(f"\nBalance: ${acct.balance:.2f}")
print(f"Equity: ${acct.equity:.2f}")

mt5.shutdown()
