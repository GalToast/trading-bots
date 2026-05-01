"""Check for pending orders and blocked funds"""
from ib_insync import *

print("Checking account restrictions...")

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=800)
print("Connected!")

# Check all open orders
orders = ib.reqOpenOrders()
print(f"\n=== OPEN ORDERS ({len(orders)}) ===")
for o in orders:
    print(f"  {o.contract.localSymbol}: {o.order.action} {o.order.totalQuantity}")

# Check all executions today
executions = ib.reqExecutions()
print(f"\n=== EXECUTIONS TODAY ({len(executions)}) ===")
for e in executions:
    print(f"  {e.contract.localSymbol}: {e.execution.side} {e.execution.shares} @ {e.execution.price}")

# Check detailed account
print("\n=== DETAILED FUNDS ===")
for v in ib.accountSummary():
    if v.tag in ['AvailableFunds', 'CashBalance', 'NetLiquidation', 
                 'EquityWithLoanValue', 'InitialMarginRequirement', 
                 'MaintenanceMarginRequirement', 'AccruedCash',
                 'FullAvailableFunds', 'FullInitMarginReq', 'FullMaintMarginReq']:
        print(f"{v.tag}: {v.value}")

ib.disconnect()
print("\nDone")