import sys, json, time
sys.path.insert(0, 'scripts')
from coinbase_advanced_client import CoinbaseAdvancedClient
client = CoinbaseAdvancedClient()

end = int(time.time())
start = end - (72 * 3600)

# Test exact same call the burst scanner makes
try:
    resp = client.market_candles('BTC-USD', start=start, end=end, granularity='FIFTEEN_MINUTE')
    candles = resp.get('candles', [])
    print(f'72h FIFTEEN_MINUTE: {len(candles)} candles')
except Exception as e:
    print(f'ERROR: {e}')

# Test with ONE_DAY granularity to be safe
try:
    resp = client.market_candles('BTC-USD', start=start, end=end, granularity='ONE_DAY')
    candles = resp.get('candles', [])
    print(f'72h ONE_DAY: {len(candles)} candles')
    if candles:
        print(f'  First: {candles[0]}')
except Exception as e:
    print(f'ERROR: {e}')
