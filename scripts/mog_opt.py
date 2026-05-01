import sys, os, time, json
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd() + '/scripts')
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = 'MOG-USD'
FEE_RATE = 0.0040

def fetch_candles(client, pid, start, end, granularity='FIVE_MINUTE'):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get('candles', [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c['start']))
    return all_c

def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def run_backtest(mog_candles, btc_lookup, tp_pct, sl_pct):
    cash = 48.0
    pos = None
    closes = 0
    history = []
    win = 0
    for c in mog_candles:
        ts = int(c['start'])
        h = float(c['high']); l = float(c['low']); cl = float(c['close'])
        history.append(cl)
        if len(history) > 50: history.pop(0)

        if pos:
            pos['hold'] += 1
            exit_p = None
            if h >= pos['tp']: exit_p = pos['tp']; win+=1
            elif sl_pct > 0 and l <= pos['sl']: exit_p = pos['sl']
            elif pos['hold'] >= 24: 
                exit_p = cl
                if exit_p >= pos['ep']: win+=1
            
            if exit_p:
                units = pos['quote'] / pos['ep']
                pnl = (exit_p - pos['ep']) * units - (pos['quote'] * FEE_RATE) - (exit_p * units * FEE_RATE)
                cash += pos['quote'] + pnl
                closes += 1
                pos = None

        if pos is None and cash >= 10.0:
            if len(history) >= 6:
                rsi_val = compute_rsi(history[:-1], 4)
                if rsi_val <= 30:
                    ep = float(c['open'])
                    pos = {
                        'ep': ep, 'quote': cash * 0.95, 'hold': 0,
                        'tp': ep * (1 + tp_pct / 100.0),
                        'sl': ep * (1 - sl_pct / 100.0) if sl_pct > 0 else 0
                    }
                    cash -= pos['quote']
    if pos: cash += pos['quote']
    return cash - 48.0, closes, win

client = CoinbaseAdvancedClient()
now = int(time.time())
start = now - 72 * 3600
cands = fetch_candles(client, PRODUCT, start, now)

best_net = -999; best_p = None
results = []
for tp in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0]:
    for sl in [0.0, 0.5, 1.0, 2.0, 3.0, 5.0]:
        net, c, w = run_backtest(cands, {}, tp, sl)
        if c > 0:
            results.append({'tp': tp, 'sl': sl, 'net': net, 'closes': c, 'wr': w/c*100})
            if net > best_net:
                best_net = net; best_p = (tp, sl)

results.sort(key=lambda x: x['net'], reverse=True)
with open('reports/mog_opt.txt', 'w') as f:
    for r in results[:20]:
        f.write(f"MOG TP {r['tp']}% SL {r['sl']}% -> Net: ${r['net']:.2f} Closes: {r['closes']} WR: {r['wr']:.1f}%\n")
print('Done. Check reports/mog_opt.txt')
