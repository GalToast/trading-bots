"""IBKR 0DTE Options - Single iteration test"""
from ib_insync import *
from datetime import datetime

PORT = 4002
CLIENT_ID = 500

print("Connecting...")
ib = IB()
ib.connect('127.0.0.1', PORT, clientId=CLIENT_ID)
print("Connected!")

# Get account
account = ib.managedAccounts()[0]
print(f"Account: {account}")

# Get positions
positions = ib.positions()
print(f"\nPositions: {len(positions)}")
for p in positions:
    print(f"  {p.contract.localSymbol}: {p.position} @ ${p.avgCost:.4f}")
    print(f"    Strike: {p.contract.strike}, Right: {p.contract.right}")
    print(f"    Expiry: {p.contract.lastTradeDateOrContractMonth}")

# Get account values
ib.reqAccountSummary()
ib.sleep(1)
summary = ib.accountSummary()
for s in summary:
    if s.tag in ['AvailableFunds', 'CashBalance', 'NetLiquidation', 'BuyingPower']:
        print(f"{s.tag}: ${float(s.value):.2f}")

# Get 0DTE chain for SPY
print("\n=== SPY 0DTE OPTIONS ===")
spy = Stock('SPY', 'SMART', 'USD')
ib.qualifyContracts(spy)

chains = ib.reqSecDefOptParams(spy.symbol, '', spy.secType, spy.conId)
for chain in chains:
    if chain.exchange == 'NASDAQOM':
        today = datetime.now().strftime('%Y%m%d')
        if today in chain.expirations:
            print(f"Exchange: {chain.exchange}")
            print(f"Expirations: {chain.expirations[:5]}")
            print(f"Strike count: {len(chain.strikes)}")
            print(f"Strike range: {min(chain.strikes)} - {max(chain.strikes)}")
            break

ib.disconnect()
print("\nDone")