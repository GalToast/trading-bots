import os
import requests
from urllib.parse import quote
API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us"
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}

r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
acct = r.json()
cash = float(acct['cash'])
equity = float(acct['portfolio_value'])

r2 = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
positions = r2.json()

print(f"Cash: ${cash:.2f}")
print(f"Equity: ${equity:.2f}")
print(f"Open orders: {len(requests.get(f'{BASE_URL}/v2/orders?status=open', headers=HEADERS).json())}")
print()

for p in positions:
    sym = p['symbol']
    qty = float(p['qty'])
    entry = float(p['avg_entry_price'])
    current = float(p['current_price'])
    mkt_val = float(p['market_value'])
    upl = float(p['unrealized_pl'])
    upl_pct = (upl / (entry * qty)) * 100 if entry * qty > 0 else 0
    print(f"{sym}: {qty:.0f} @ ${entry:.8f} | Now: ${current:.8f} | Value: ${mkt_val:.2f} | P/L: ${upl:.2f} ({upl_pct:+.2f}%)")

print(f"\nTotal: ${equity:.2f} | Net P/L: ${equity - 46.33:.2f} ({((equity - 46.33) / 46.33) * 100:+.2f}%)")
