import MetaTrader5 as mt5
from mt5_config import LOGIN, PASSWORD, SERVER

mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER)
info = mt5.account_info()
positions = mt5.positions_get()
print(f"Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f} | Positions: {len(positions) if positions else 0}")
if positions:
    for p in positions:
        direction = "BUY" if p.type == 0 else "SELL"
        print(f"  {p.symbol} {direction} {p.volume:.2f} @ {p.price_open:.5f} P/L: ${p.profit:.2f} SL: {p.sl:.2f} TP: {p.tp:.2f}")
mt5.shutdown()
