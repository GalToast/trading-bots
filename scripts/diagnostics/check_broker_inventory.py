"""Full broker inventory by magic number."""
import MetaTrader5 as mt5

mt5.initialize()

positions = mt5.positions_get()
if not positions:
    print("No positions")
    mt5.shutdown()
    exit()

print(f"Total broker positions: {len(positions)}")
print()

# Group by magic
by_magic = {}
for p in positions:
    magic = p.magic
    if magic not in by_magic:
        by_magic[magic] = {'buys': 0, 'sells': 0, 'symbols': set(), 'total': 0}
    by_magic[magic]['total'] += 1
    if p.type == 0:
        by_magic[magic]['buys'] += 1
    else:
        by_magic[magic]['sells'] += 1
    by_magic[magic]['symbols'].add(p.symbol)

print(f"{'Magic':<15} {'Total':>6} {'Buys':>5} {'Sells':>6} {'Symbols'}")
print("-" * 60)
for magic in sorted(by_magic.keys()):
    d = by_magic[magic]
    symbols = ", ".join(sorted(d['symbols']))
    print(f"{magic:<15} {d['total']:>6} {d['buys']:>5} {d['sells']:>6} {symbols}")

print(f"\nTotal unique magics: {len(by_magic)}")

# Count by symbol
by_symbol = {}
for p in positions:
    sym = p.symbol
    if sym not in by_symbol:
        by_symbol[sym] = {'buys': 0, 'sells': 0, 'total': 0}
    by_symbol[sym]['total'] += 1
    if p.type == 0:
        by_symbol[sym]['buys'] += 1
    else:
        by_symbol[sym]['sells'] += 1

print(f"\nBy Symbol:")
print(f"{'Symbol':<15} {'Total':>6} {'Buys':>5} {'Sells':>6}")
print("-" * 35)
for sym in sorted(by_symbol.keys()):
    d = by_symbol[sym]
    print(f"{sym:<15} {d['total']:>6} {d['buys']:>5} {d['sells']:>6}")

mt5.shutdown()
