import os
import requests
API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY
}

r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")

if r.status_code == 200:
    a = r.json()
    print(f"Cash: ${float(a.get('cash', 0)):.2f}")
    print(f"Equity: ${float(a.get('equity', 0)):.2f}")
    print(f"Buying Power: ${float(a.get('buying_power', 0)):.2f}")
