"""Check open trades"""
from ib_insync import *

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=997)
ib.sleep(0.5)

print("=== OPEN TRADES ===")
for trade in ib.openTrades():
    print(f"  {trade.contract.localSymbol}: {trade.order.action} {trade.order.totalQuantity}")
    print(f"  Status: {trade.orderStatus.status}")
    print(f"  Filled: {trade.orderStatus.filled} @ {trade.orderStatus.avgFillPrice}")
    print()

print("=== POSITIONS ===")
for pos in ib.positions():
    print(f"  {pos.contract.localSymbol}: {pos.position} @ ${pos.avgCost}")

ib.disconnect()
print("Done")