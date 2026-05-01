"""Check crypto and futures available on IBKR"""
from ib_insync import *

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=888)
ib.sleep(0.5)

print("=== CHECKING CRYPTO ===")
# Try PAXOS crypto
btc = Crypto('BTC', 'PAXOS', 'USD')
ib.qualifyContracts(btc)
print(f"BTC contract: {btc}")

eth = Crypto('ETH', 'PAXOS', 'USD')
ib.qualifyContracts(eth)
print(f"ETH contract: {eth}")

print("\n=== CHECKING FUTURES ===")
# Try ES futures
es = Future(symbol='ES', exchange='CME')
try:
    chains = ib.reqContractDetails(es)
    print(f"ES futures found: {len(chains)}")
    for c in chains[:3]:
        print(f"  {c.contract.localSymbol}")
except Exception as e:
    print(f"ES error: {e}")

# Try BTC futures
btc_fut = Future(symbol='BTC', exchange='CME')
try:
    chains = ib.reqContractDetails(btc_fut)
    print(f"BTC futures found: {len(chains)}")
except Exception as e:
    print(f"BTC futures error: {e}")

print("\n=== ACCOUNT ===")
for v in ib.accountSummary():
    if v.tag in ['AvailableFunds', 'NetLiquidation', 'CashBalance']:
        print(f"{v.tag}: ${float(v.value):.2f}")

ib.disconnect()
print("Done")