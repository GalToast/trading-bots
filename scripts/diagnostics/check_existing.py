"""Check existing SPY PUT position"""
from ib_insync import *

print("Connecting...")
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=99)
print("Connected!")

# Get positions
positions = ib.positions()
print(f"\n=== POSITIONS ({len(positions)}) ===")
for pos in positions:
    print(f"  {pos.contract.localSymbol}: {pos.position} @ ${pos.avgCost:.4f}")
    print(f"    Strike: {pos.contract.strike}")
    print(f"    Right: {pos.contract.right}")
    print(f"    Expiry: {pos.contract.lastTradeDateOrContractMonth}")

# Get account
account = ib.accountValues()
for v in account:
    if v.tag in ['AvailableFunds', 'CashBalance', 'NetLiquidation']:
        print(f"{v.tag}: {v.value}")

ib.disconnect()
print("\nDone")