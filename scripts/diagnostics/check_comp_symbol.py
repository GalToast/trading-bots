import MetaTrader5 as mt5
mt5.initialize()

# Check if COMPUSD exists
info = mt5.symbol_info("COMPUSD")
if info:
    print(f"COMPUSD: bid={info.bid}, ask={info.ask}, point={info.point}")
else:
    print("COMPUSD: NOT AVAILABLE")
    # Try variations
    for sym in ["COMP-USD", "COMP_USD", "COMP"]:
        info = mt5.symbol_info(sym)
        if info:
            print(f"  Found: {sym} - bid={info.bid}, ask={info.ask}")
            break
    else:
        print("  No COMP variant found")

# List all symbols containing COMP
symbols = mt5.symbols_get()
if symbols:
    comp_syms = [s.name for s in symbols if 'COMP' in s.name.upper()]
    print(f"\nSymbols with COMP: {comp_syms}")

mt5.shutdown()
