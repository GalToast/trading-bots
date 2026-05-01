qty = 7805814.199395769
entry = 0.000003224
current = 0.000003309

cost = qty * entry
value = qty * current
pnl = qty * (current - entry)
pnl_pct = (current - entry) / entry * 100

print(f"Cost basis: ${cost:.2f}")
print(f"Current value: ${value:.2f}")
print(f"Unrealized P/L: ${pnl:.2f}")
print(f"P/L %: {pnl_pct:.2f}%")
print(f"Total equity should be: cash({21.10}) + position_value({value:.2f}) = ${21.10 + value:.2f}")
