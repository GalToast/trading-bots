import MetaTrader5 as mt5
from datetime import datetime, timezone

def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    symbol = "NOM-USD"
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print(f"Could not get tick for {symbol}")
        mt5.shutdown()
        return
        
    print(f"NOM-USD Current: Bid={tick.bid}, Ask={tick.ask}, Time={datetime.fromtimestamp(tick.time, tz=timezone.utc)}")
    
    # Check last 40 bars for session ready
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 40)
    if rates is not None:
        print(f"NOM-USD M5 Bars: {len(rates)}")
        last_bar = rates[-1]
        print(f"Last Bar: Time={datetime.fromtimestamp(last_bar[0], tz=timezone.utc)}, Close={last_bar[4]}")
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
