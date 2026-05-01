"""Check our SPY PUT position"""
from ib_insync import *

print("Connecting...")
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=105)
print("Connected!\n")

# Check positions
positions = ib.positions()
print(f"=== POSITIONS ({len(positions)}) ===")
for pos in positions:
    print(f"  {pos.contract.localSymbol}: {pos.position} @ avg ${pos.avgCost:.4f}")
    
# Check account
print(f"\n=== ACCOUNT ===")
ib.reqAccountSummary()
ib.sleep(1)
for s in ib.accountSummary():
    if s.tag in ['CashBalance', 'BuyingPower', 'NetLiquidation', 'AvailableFunds']:
        print(f"  {s.tag}: ${float(s.value):.2f}")

ib.disconnect()
print("\nDone")