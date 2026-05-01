"""Check and close existing position"""
from ib_insync import *

print("Connecting...")
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=999)
print("Connected!")

# Get positions
positions = ib.positions()
print(f"\nPositions: {len(positions)}")

for pos in positions:
    print(f"\n{pos.contract.localSymbol}:")
    print(f"  Qty: {pos.position}")
    print(f"  Avg Cost: ${pos.avgCost:.4f}")
    print(f"  Strike: {pos.contract.strike}")
    print(f"  Right: {pos.contract.right}")
    print(f"  Expiry: {pos.contract.lastTradeDateOrContractMonth}")
    
    # Try to get current price (delayed data)
    ticker = ib.reqMktData(pos.contract, '', False, False)
    ib.sleep(3)
    
    print(f"  Bid: {ticker.bid}")
    print(f"  Ask: {ticker.ask}")
    print(f"  Last: {ticker.last}")
    
    if ticker.bid and ticker.bid > 0:
        value = ticker.bid * 100 * float(pos.position)
        cost = pos.avgCost * 100 * float(pos.position)
        pnl = value - cost
        print(f"  Value: ${value:.2f}")
        print(f"  P/L: ${pnl:+.2f}")
    
    # Close it
    print(f"\n  Closing position...")
    order = MarketOrder('SELL', abs(int(pos.position)))
    trade = ib.placeOrder(pos.contract, order)
    ib.sleep(2)
    
    print(f"  Status: {trade.orderStatus.status}")
    print(f"  Fill: ${trade.orderStatus.avgFillPrice or 0:.4f}")

# Final account check
ib.sleep(1)
for v in ib.accountValues():
    if v.tag in ['AvailableFunds', 'CashBalance', 'NetLiquidation', 'EquityWithLoanValue']:
        print(f"\n{v.tag}: {v.value}")

ib.disconnect()
print("\nDone")