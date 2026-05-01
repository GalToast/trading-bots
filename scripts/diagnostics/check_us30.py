import MetaTrader5 as mt5
import sys

if not mt5.initialize():
    print("MT5 initialization failed")
    sys.exit()

sym = mt5.symbol_info("US30")
if sym:
    print(f"US30: tick_size={sym.trade_tick_size}, tick_value={sym.trade_tick_value}, contract_size={sym.trade_contract_size}, volume_min={sym.volume_min}, volume_step={sym.volume_step}")
else:
    print("US30 not found")
mt5.shutdown()
