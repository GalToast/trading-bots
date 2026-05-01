import MetaTrader5 as mt5
from brain import TradingBrain
import time

mt5.initialize()
brain = TradingBrain()

# Check brain methods
print(f'Brain methods: {[m for m in dir(brain) if not m.startswith("_")]}')

sym = 'GBPUSD'
if hasattr(brain, 'get_symbol_data'):
    data = brain.get_symbol_data(sym)
    print(f'Data for {sym}: {data}')
else:
    print('Brain does NOT have get_symbol_data method!')

mt5.shutdown()