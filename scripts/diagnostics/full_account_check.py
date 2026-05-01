"""Check full account summary"""
from ib_insync import *

print("Connecting...")
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=500)
print("Connected!")

# Get full account summary
print("\n=== FULL ACCOUNT SUMMARY ===")
for v in ib.accountSummary():
    print(f"{v.tag}: {v.value} ({v.currency})")

# Get positions
print("\n=== POSITIONS ===")
for pos in ib.positions():
    print(f"{pos.contract.localSymbol}: {pos.position} @ {pos.avgCost}")

ib.disconnect()
print("\nDone")