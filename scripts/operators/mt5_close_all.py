import MetaTrader5 as mt5
import mt5_terminal_guard

def main() -> int:
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5, require_trade_allowed=True)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1
    try:
        positions = mt5.positions_get()
        if positions:
            print(f"Closing {len(positions)} positions...")
            for pos in positions:
                tick = mt5.symbol_info_tick(pos.symbol)
                if not tick:
                    continue

                if pos.type == 0:  # BUY
                    close_type = mt5.ORDER_TYPE_SELL
                    price = tick.bid
                else:  # SELL
                    close_type = mt5.ORDER_TYPE_BUY
                    price = tick.ask

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": pos.ticket,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": close_type,
                    "price": price,
                    "deviation": 20,
                    "magic": 123456,
                    "comment": "Close all",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_FOK,
                }
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"  Closed #{pos.ticket} {pos.symbol}")
                else:
                    print(f"  Failed #{pos.ticket}: {result.retcode if result else 'no result'}")

            # Check final
            remaining = mt5.positions_get()
            print(f"Remaining: {len(remaining) if remaining else 0}")
        else:
            print("No positions to close")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
