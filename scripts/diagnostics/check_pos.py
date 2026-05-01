import MetaTrader5 as mt5
mt5.initialize()
positions = mt5.positions_get()
if positions:
    for p in positions:
        print(f"{p.ticket}:{p.symbol} P/L={p.profit:.2f} vol={p.volume}")
else:
    print("No positions")
mt5.shutdown()