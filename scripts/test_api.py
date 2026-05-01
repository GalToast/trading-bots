import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

print("Starting test...", flush=True)
client = CoinbaseAdvancedClient()
print("Client created", flush=True)

try:
    # Test candle fetch first
    import time
    now = int(time.time())
    start = now - 3600
    candles = client.market_candles("RAVE-USD", start=start, end=now, granularity="FIVE_MINUTE")
    print(f"Candles: {len(candles.get('candles', []))}", flush=True)
    
    # Now try list_products
    resp = client.list_products(product_type='SPOT', limit=20)
    print(f"Response type: {type(resp)}", flush=True)
    if isinstance(resp, dict):
        print(f"Keys: {list(resp.keys())}", flush=True)
        if 'products' in resp:
            print(f"Products: {len(resp['products'])}", flush=True)
            for p in resp['products'][:5]:
                print(f"  {p.get('product_id')}: {p.get('quote_currency_id')}", flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()

print("Done", flush=True)
