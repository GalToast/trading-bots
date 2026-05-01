"""Check all symbols and trade modes"""
import MetaTrader5 as mt5
from mt5_config import LOGIN, PASSWORD, SERVER

mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER)

symbols = mt5.symbols_get()
print(f'Total symbols: {len(symbols)}')

modes = {}
for s in symbols:
    modes[s.trade_mode] = modes.get(s.trade_mode, 0) + 1
print(f'Trade modes: {modes}')

# Mode 2 = SYMBOL_TRADE_MODE_FULL (full trading)
# Mode 3 = SYMBOL_TRADE_MODE_CLOSEONLY (close only)
# Mode 4 = SYMBOL_TRADE_MODE_NO_LONG (no longs)
# Mode 5 = SYMBOL_TRADE_MODE_NO_SHORT (no shorts)

tradable = [s for s in symbols if s.trade_mode == 2]
print(f'Fully tradable (mode 2): {len(tradable)}')
for s in tradable:
    print(f'  {s.name}')

mt5.shutdown()
