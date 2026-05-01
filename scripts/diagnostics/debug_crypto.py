"""Debug crypto orders and positions"""
from ib_insync import *

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=888)
ib.sleep(1)

print("=== POSITIONS ===")
for pos in ib.positions():
    if pos.contract.secType == 'CRYPTO' and float(pos.position) != 0:
        print(f"  {pos.contract.localSymbol}: {pos.position}")

print("\n=== OPEN ORDERS ===")
for o in ib.openOrders():
    print(f"  {o.contract.localSymbol}: {o.action} {o.totalQuantity} @ {o.lmtPrice} | Status: {o.status}")

print("\n=== OPEN TRADES ===")
for t in ib.openTrades():
    print(f"  {t.contract.localSymbol}: {t.order.action} {t.order.totalQuantity} | Status: {t.orderStatus.status}")

print("\n=== FILLS ===")
for f in ib.fills():
    print(f"  {f.contract.localSymbol}: {f.execution.side} {f.execution.shares} @ {f.execution.avgPrice}")

print("\n=== ACCOUNT ===")
for v in ib.accountValues():
    if v.tag in ['AvailableFunds', 'CashBalance', 'NetLiquidation']:
        print(f"  {v.tag}: {v.value}")

print("\n=== TRY MARKET ORDER ===")
contract = Crypto(symbol='BTC', exchange='PAXOS', currency='USD')
ib.qualifyContracts(contract)
print(f"Contract: {contract}")

# Try a tiny market buy
order = MarketOrder('BUY', 0.0001)
order.exchange = 'PAXOS'
print(f"Order: {order.action} {order.totalQuantity} BTC, exchange={order.exchange}")
trade = ib.placeOrder(contract, order)
ib.sleep(3)
print(f"Status: {trade.orderStatus.status}")
print(f"Filled: {trade.orderStatus.filled}")
print(f"AvgFillPrice: {trade.orderStatus.avgFillPrice}")

ib.disconnect()
print("Done")
