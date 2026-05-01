import sys, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()

# Directly check the pairs we know have wide spreads
targets = ['DUCKUSD', 'SHAPEUSD', 'CQTUSD', 'ACAUSD', 'HONEYUSD', 'IDEXUSD', 'EDUUSD', 'FORTHUSD', 'PLANCKUSD', 'ANLOGUSD', 'AI3USD', 'TRACUSD', 'BADGERUSD', 'CHEXUSD']

print('Quick sweep of known candidates (2 snapshots, 3s apart):')
print()

for pair in targets:
    try:
        t1 = c.ticker([pair])
        if not t1 or pair not in t1:
            # Try alternate key
            for k in list(t1.keys()) if t1 else []:
                if k.upper().replace('/','') == pair:
                    t1 = {pair: t1[k]}
                    break
        if pair not in t1:
            print(f'  ?  {pair:15s} NOT FOUND')
            continue
        tk = t1[pair]
        bid = to_float((tk.get('b') or [None])[0])
        ask = to_float((tk.get('a') or [None])[0])
        last1 = to_float((tk.get('c') or [None])[0])
        sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0

        time.sleep(3)

        t2 = c.ticker([pair])
        if pair in t2:
            tk2 = t2[pair]
        else:
            for k in list(t2.keys()) if t2 else []:
                if k.upper().replace('/','') == pair:
                    tk2 = t2[k]
                    break
            else:
                tk2 = {}
        last2 = to_float((tk2.get('c') or [None])[0])

        if last1 > 0 and last2 > 0:
            move = abs(last2 - last1) / ((last1 + last2) / 2) * 10000
            direction = 'UP' if last2 > last1 else 'DOWN' if last2 < last1 else 'FLAT'
            flag = '🔥' if move > 20 else '⏳' if move > 5 else '➡️'
        else:
            flag = '?'
            move = 0
            direction = 'N/A'

        print(f'{flag} {pair:15s} spread={sp:6.0f}bps move={move:6.1f}bps {direction}')
    except Exception as e:
        print(f'?  {pair:15s} err: {str(e)[:50]}')
