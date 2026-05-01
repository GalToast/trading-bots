import os
import requests
from urllib.parse import quote

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us"
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}

for sym in ["PEPE/USD", "DOGE/USD", "SOL/USD"]:
    r = requests.get(f'{DATA_URL}/latest/trades?symbols={quote(sym, safe="/")}', headers=HEADERS, timeout=8)
    if r.status_code == 200:
        data = r.json().get("trades", {})
        if sym in data:
            print(f"{sym}: ${data[sym]['p']}")
        else:
            print(f"{sym}: no trade data, keys={list(data.keys())}")
    else:
        print(f"{sym}: HTTP {r.status_code}")
