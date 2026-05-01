import MetaTrader5 as mt5
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mt5_config

ok = mt5.initialize(
    login=mt5_config.LOGIN,
    password=mt5_config.PASSWORD,
    server=mt5_config.SERVER,
)
print(f"MT5 init: {ok}", file=sys.stderr)
if not ok:
    print(f"Error: {mt5.last_error()}", file=sys.stderr)
    sys.exit(1)

positions = mt5.positions_get(symbol="GBPUSD")
if positions:
    print(f"GBPUSD positions: {len(positions)}")
    for p in positions:
        ptype = "BUY" if p.type == 0 else "SELL"
        print(f"  Ticket={p.ticket} Type={ptype} Vol={p.volume} Price={p.price_open} Profit={p.profit} Magic={p.magic} Comment={p.comment}")
    total_float = sum(p.profit for p in positions)
    print(f"Total floating P/L: {total_float:.2f}")
else:
    print("No GBPUSD positions (clean)")

# Also check total positions
all_pos = mt5.positions_get()
if all_pos:
    print(f"\nTotal MT5 positions: {len(all_pos)}")
    symbols = set(p.symbol for p in all_pos)
    print(f"Symbols: {symbols}")

mt5.shutdown()
