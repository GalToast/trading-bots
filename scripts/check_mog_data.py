#!/usr/bin/env python3
"""Check MOG candle data for $0.00 open issue."""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

client = CoinbaseAdvancedClient()
now = int(time.time())
start = now - 3600

try:
    resp = client.market_candles('MOG-USD', start=start, end=now, granularity='FIVE_MINUTE')
    candles = resp.get('candles', [])
    print(f'MOG-USD candles fetched: {len(candles)}')
    if candles:
        c = candles[-1]
        print(f'Latest candle: open={c.get("open", "?")}, close={c.get("close", "?")}, high={c.get("high", "?")}, low={c.get("low", "?")}')
        zero_opens = sum(1 for c in candles if float(c.get('open', 0)) == 0)
        print(f'Candles with open=0: {zero_opens}/{len(candles)}')
        # Print first 3 candles
        for c in candles[:3]:
            print(f'  open={c.get("open")}, close={c.get("close")}, high={c.get("high")}, low={c.get("low")}')
    else:
        print('No MOG candles returned from API')
except Exception as e:
    print(f'Error fetching MOG: {e}')
