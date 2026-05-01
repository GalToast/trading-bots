import os
import requests

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}

# Open orders
print("=== OPEN ORDERS ===")
r = requests.get(f"{BASE_URL}/v2/orders?status=open", headers=HEADERS, timeout=10)
for o in r.json():
    print(f"  {o['symbol']} {o['side']} {o['type']} qty={o.get('qty')} notional={o.get('notional')} status={o['status']}")

# Recent closed orders
print("\n=== RECENT CLOSED (last 5) ===")
r = requests.get(f"{BASE_URL}/v2/orders?status=closed&limit=5", headers=HEADERS, timeout=10)
for o in r.json():
    print(f"  {o['symbol']} {o['side']} filled@{o.get('filled_avg_price')} qty={o.get('filled_qty')}")

# Positions
print("\n=== POSITIONS ===")
r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
for p in r.json():
    print(f"  {p['symbol']}: {float(p['qty']):.0f} @ {p['avg_entry_price']}")

# Account
r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
a = r.json()
print(f"\nCash: ${float(a['cash']):.2f}")
print(f"Equity: ${float(a['portfolio_value']):.2f}")