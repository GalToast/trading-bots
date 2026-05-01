import MetaTrader5 as mt5
import os

LOGIN = int(os.environ.get('MT5_LOGIN', 0))
PASSWORD = os.environ.get('MT5_PASSWORD', '')
SERVER = os.environ.get('MT5_SERVER', '')

def main():
    if not mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    symbols = ["NAS100", "US30", "GBPUSD", "USDCHF", "USDJPY", "AUDCHF", "NZDCAD"]
    print(f"{'Symbol':<10} {'TickSize':<10} {'TickValue':<10} {'LotMin':<10} {'LotMax':<10} {'StopsLvl':<10}")
    print("-" * 70)
    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        if info:
            print(f"{symbol:<10} {info.trade_tick_size:<10.5f} {info.trade_tick_value:<10.2f} {info.volume_min:<10.2f} {info.volume_max:<10.2f} {info.trade_stops_level:<10}")
        else:
            print(f"{symbol:<10} NOT FOUND")

    mt5.shutdown()

if __name__ == "__main__":
    main()
