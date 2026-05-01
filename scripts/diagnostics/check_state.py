"""Check exact account state"""
import MetaTrader5 as mt5
from mt5_config import LOGIN, PASSWORD, SERVER

mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER)

acct = mt5.account_info()
print(f"Balance: ${acct.balance:.2f}")
print(f"Equity:  ${acct.equity:.2f}")
print(f"Margin:  ${acct.margin:.2f}")
print(f"Free:    ${acct.margin_free:.2f}")
print(f"Leverage: 1:{acct.leverage}")

positions = mt5.positions_get()
print(f"\nOpen positions: {len(positions) if positions else 0}")
total_pnl = 0
for p in (positions or []):
    side = "BUY" if p.type == 0 else "SELL"
    print(f"  {p.symbol}: {side} {p.volume} @ {p.price_open} | P/L: ${p.profit:+.2f}")
    total_pnl += p.profit

print(f"\nTotal open P/L: ${total_pnl:+.2f}")

# Check trade history
history = mt5.history_deals_get(from_position=0)
if history:
    print(f"\nRecent deals: {len(history)}")
    for d in history[-10:]:
        if d.entry == 1:  # Close
            print(f"  {d.symbol}: {d.profit:+.2f} | {d.comment}")

mt5.shutdown()
