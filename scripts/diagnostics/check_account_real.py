import os
import requests

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY
}

# Check account
r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
print("=== ACCOUNT ===")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    acc = r.json()
    print(f"Cash: ${acc.get('cash')}")
    print(f"Equity: ${acc.get('equity')}")
    print(f"Buying Power: ${acc.get('buying_power')}")
    print(f"Account: {acc.get('account_number')}")
    print(f"Status: {acc.get('status')}")
    print(f"Trade suspended: {acc.get('trade_suspended')}")
    print(f"Multiplier: {acc.get('multiplier')}")
    print(f"Daytrade count: {acc.get('daytrade_count')}")
    print(f"Pattern day trader: {acc.get('pattern_day_trader')}")

# Check open orders
r = requests.get(f"{BASE_URL}/v2/orders?status=open", headers=HEADERS)
print("\n=== OPEN ORDERS ===")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    orders = r.json()
    print(f"Count: {len(orders)}")
    for o in orders:
        print(f"  {o['symbol']} {o['side']} {o.get('notional') or o.get('qty')} @ {o.get('limit_price') or o.get('stop_price')} | {o['type']}")

# Check positions
r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
print("\n=== POSITIONS ===")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    positions = r.json()
    print(f"Count: {len(positions)}")
    for p in positions:
        print(f"  {p['symbol']}: {p['qty']} @ ${p.get('avg_entry_price')} (Current: ${p.get('current_price')})")