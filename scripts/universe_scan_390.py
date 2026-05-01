"""390-Coin Universe Scan — find ALL coins with RAVE-like edges."""
import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"

def fetch(client, pid, start, end, gran="FIVE_MINUTE"):
    chunk = 300*5*60
    all_c, cs = [], start
    while cs < end:
        ce = min(cs + chunk, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=gran)
            cands = resp.get("candles", [])
            all_c.extend(cands); cs = ce
            if not cands: break
            time.sleep(0.05)
        except:
            cs = ce; time.sleep(0.2)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def rsi(closes, p=3):
    if len(closes) < p+1: return 50.0
    d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g = [x if x>0 else 0 for x in d[-p:]]
    l = [-x if x<0 else 0 for x in d[-p:]]
    ag, al = sum(g)/p, sum(l)/p
    if al > 0: return 100 - 100/(1+ag/al)
    return 100.0

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt_rsi_mr(candles, btc_lk, cash_start=48.0):
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; pk=cash_start; mdd=0.0; h=[]
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        h.append(close)
        if len(h)>100: h.pop(0)
        boc=True
        pt,pt3=ts-60,ts-180
        if pt in btc_lk and pt3 in btc_lk:
            mom=(btc_lk[pt]-btc_lk[pt3])/btc_lk[pt3]
            if mom<-0.001: boc=False
        hr=datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0,6,12,19}: continue
        fr=get_fee(vol)
        if pos:
            pos["h"]+=1
            if hi>=pos["tp"]:
                exit_p=pos["tp"]; w+=1
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and len(h)>=5:
            rv=rsi(h[:-1],3)
            if rv<30:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*1.25}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-cash_start; wr=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,
            "wr":round(wr,1),"avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),
            "vol":round(vol,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s7 = now - 7*24*3600  # 7-day scan for speed

    # Step 1: Get all products
    print("📡 Fetching all Coinbase products...")
    try:
        products_resp = client.list_products(limit=500)
        products = products_resp.get("products", [])
        print(f"  Total products: {len(products)}")

        # Filter: USD pairs, online, exclude stablecoins and BTC/ETH
        exclude_prefix = {"USDC", "USDT", "DAI", "PYUSD", "USD", "EUR", "GBP", "SGD", "BRL", "ARS", "UAH", "KRW", "TRY", "INR", "IDR", "NGN", "AED", "JPY", "PLN", "RON", "CHF"}
        exclude_exact = {"BTC-USD", "ETH-USD", "BTC-USDC", "ETH-USDC"}
        coins = []
        for p in products:
            pid = p.get("product_id", "")
            status = p.get("status", "")
            if status != "online": continue
            if pid in exclude_exact: continue
            # Exclude fiat pairs
            base, quote = pid.split("-") if "-" in pid else ("", "")
            if quote not in {"USD", "USDC"}: continue
            if quote == "USDC": continue  # Only want USD pairs
            if base in exclude_prefix: continue
            # Exclude leveraged tokens, weird symbols
            if any(x in base for x in ["BULL", "BEAR", "DOWN", "UP", "2L", "2S", "3L", "3S"]): continue
            coins.append(pid)

        coins = sorted(set(coins))
        print(f"  Filtered to {len(coins)} USD pairs (excluded stablecoins, BTC, ETH, fiat, leveraged)")
        print(f"  Top 20: {coins[:20]}")
    except Exception as e:
        print(f"  ERROR: {e}")
        coins = []

    if not coins:
        # Fallback: known coins
        coins = [
            "SOL-USD", "DOGE-USD", "XRP-USD", "ADA-USD", "AVAX-USD",
            "LINK-USD", "DOT-USD", "MATIC-USD", "SHIB-USD", "UNI-USD",
            "LTC-USD", "BCH-USD", "FIL-USD", "ETC-USD", "NEAR-USD",
            "APT-USD", "ARB-USD", "OP-USD", "ATOM-USD", "ALGO-USD",
            "AAVE-USD", "GRT-USD", "MKR-USD", "SNX-USD", "RUNE-USD",
            "INJ-USD", "TIA-USD", "SUI-USD", "SEI-USD", "FET-USD",
            "RENDER-USD", "WLD-USD", "IMX-USD", "STX-USD", "ONDO-USD",
            "VIRTUAL-USD", "TRUMP-USD", "FARTCOIN-USD", "PEPE-USD",
            "WIF-USD", "BONK-USD", "MOG-USD", "POPCAT-USD", "BRETT-USD",
            "IOTX-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "RAVE-USD"
        ]
        print(f"  Using fallback list: {len(coins)} coins")

    # Step 2: Fetch BTC lookup
    print(f"\n📡 Fetching BTC M5 (7d)...")
    btc = fetch(client, BTC, s7, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"  {len(btc_lk)} candles")

    # Step 3: Scan each coin
    print(f"\n🔬 Scanning {len(coins)} coins with RSI(3)<30 TP25 (7-day)...")
    results = []
    scanned = 0
    for i, coin in enumerate(coins):
        if i % 10 == 0 and i > 0:
            print(f"  Progress: {i}/{len(coins)} ({scanned} scanned, {len([r for r in results if r['net']>0])} profitable)")
        try:
            candles = fetch(client, coin, s7, now)
            if len(candles) < 100:
                continue
            r = bt_rsi_mr(candles, btc_lk)
            r["coin"] = coin
            r["candles"] = len(candles)
            results.append(r)
            scanned += 1
            flag = "🔥" if r["net"] > 20 else "✅" if r["net"] > 0 else "❌"
            if r["net"] > 0:
                print(f"    {flag} {coin}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
        except Exception as e:
            pass

    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\n{'='*80}")
    print(f"🏆 UNIVERSE SCAN RESULTS — Top 20 coins")
    print(f"{'='*80}")
    for i, r in enumerate(results[:20]):
        flag = "🔥" if r["net"] > 20 else "✅" if r["net"] > 0 else "❌"
        print(f"  {i+1:>3}. {flag} {r['coin']:<20} ${r['net']:>8.2f} ({r['rpct']:>6.1f}%), {r['trades']:>3}t, {r['wr']:>5.1f}% WR, DD={r['mdd']:.1f}%")

    profitable = [r for r in results if r["net"] > 0]
    print(f"\n  Total scanned: {scanned}/{len(coins)}")
    print(f"  Profitable: {len(profitable)}/{scanned}")
    print(f"  Total profit if all had $48: ${sum(r['net'] for r in profitable):.2f}")
    print(f"  Total capital needed: ${len(profitable)*48}")

    with open("reports/universe_scan_390.json", "w") as f:
        json.dump({"coins": coins, "results": results}, f, indent=2)
    print(f"\nSaved to reports/universe_scan_390.json")

if __name__ == "__main__":
    main()
