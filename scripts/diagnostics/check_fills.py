"""Check actual fills and costs"""
from ib_insync import *
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=502)

# Get fills from today
fills = ib.fills()
print('=== FILLS TODAY ===')
for f in fills:
    print(f"{f.contract.localSymbol}: {f.execution.side} {f.execution.shares} @ ${f.execution.price}")

print('\n=== POSITIONS ===')
for pos in ib.positions():
    if float(pos.position) != 0:
        print(f"{pos.contract.localSymbol}: qty={pos.position} avgCost=${pos.avgCost:.4f}")

print('\n=== ACCOUNT ===')
for v in ib.accountSummary():
    if v.tag in ['AvailableFunds', 'CashBalance', 'NetLiquidation', 'RealizedPnL']:
        print(f"{v.tag}: ${float(v.value):.2f}")

ib.disconnect()
print("Done")