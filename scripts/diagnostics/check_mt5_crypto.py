"""Check crypto availability in MT5"""
import MetaTrader5 as mt5

if not mt5.initialize():
    print(f"MT5 initialize failed: {mt5.last_error()}")
    exit()

symbols = mt5.symbols_get()
crypto_names = ['BTC', 'ETH', 'XRP', 'DOGE', 'SOL', 'ADA', 'LTC']
crypto = [s for s in symbols if any(c in s.name for c in crypto_names)]

print(f"Found {len(crypto)} crypto symbols:")
for s in crypto[:20]:
    print(f"  {s.name}: visible={s.visible} trade_mode={s.trade_mode}")

# Also check what the 15 active symbols are
all_active = [s for s in symbols if s.trade_mode == 2 and s.visible]
print(f"\nAll active/tradeable symbols ({len(all_active)}):")
for s in all_active[:20]:
    print(f"  {s.name}")

mt5.shutdown()