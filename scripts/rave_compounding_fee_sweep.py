import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
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

def get_fee_rate(total_volume):
    if total_volume >= 50000: return 0.0015
    elif total_volume >= 10000: return 0.0025
    else: return 0.0040

def run_backtest(rave_candles, btc_lookup, rsi_period, rsi_entry, rsi_exit,
                 tp_pct, sl_pct, timeout, session_gate_hours=None, btc_gate=True,
                 cash_start=48.0, deploy_pct=1.0):
    if session_gate_hours is None:
        session_gate_hours = {12, 19, 6, 0}

    cash = cash_start
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    history = []

    for i in range(len(rave_candles)):
        c = rave_candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])

        history.append(cl)
        if len(history) > 50: history.pop(0)

        btc_ok = True
        if btc_gate:
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_ok = False

        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_ok = (hour not in session_gate_hours)

        fr = get_fee_rate(total_volume)

        if pos:
            pos["hold"] += 1
            exit_p = None
            closed = False

            if h >= pos["tp"]:
                exit_p = pos["tp"]; wins += 1; closed = True
            elif l <= pos["sl"]:
                exit_p = pos["sl"]; closed = True
            elif len(history) >= rsi_period + 1:
                cur_rsi = compute_rsi(history, rsi_period)
                if cur_rsi >= rsi_exit:
                    exit_p = cl; closed = True
                    if exit_p > pos["ep"]: wins += 1
            elif pos["hold"] >= timeout:
                exit_p = cl; closed = True
                if exit_p > pos["ep"]: wins += 1

            if closed:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                closes += 1
                pos = None

        if pos is None and cash >= 10.0 and btc_ok and session_ok:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= rsi_entry:
                    ep = float(c["open"])
                    tq = cash * deploy_pct
                    if tq >= 10.0:
                        pos = {
                            "ep": ep, "quote": tq, "hold": 0,
                            "tp": ep * (1 + tp_pct / 100.0),
                            "sl": ep * (1 - sl_pct / 100.0)
                        }
                        cash -= tq

    if pos: cash += pos["quote"]
    net = cash - cash_start
    wr = wins/max(1, closes)*100
    avg_trade = net / max(1, closes)
    return {
        "net": round(net, 2), "return_pct": round(net/cash_start*100, 1),
        "trades": closes, "wr": round(wr, 1), "avg_trade": round(avg_trade, 2),
        "volume": round(total_volume, 2), "final_cash": round(cash, 2),
        "fee_rate_final": get_fee_rate(total_volume)
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for {PRODUCT} Compounding + Fee Sweep...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"Loaded {len(rave_candles)} RAVE candles, {len(btc_lookup)} BTC candles")

    # Ultimate config from frontier sweep
    RSI_ENTRY, RSI_EXIT, TP, SL, TO = 45, 95, 20, 2.75, 4
    results = {}

    # Baseline ultimate
    baseline = run_backtest(rave_candles, btc_lookup, 4, RSI_ENTRY, RSI_EXIT, TP, SL, TO)
    print(f"\n📊 ULTIMATE BASELINE: RSI(4)<{RSI_ENTRY}, TP{TP}/SL{SL}, RSI>{RSI_EXIT}")
    print(f"   ${baseline['net']:.2f} ({baseline['return_pct']}%), {baseline['trades']}t, {baseline['wr']}% WR, ${baseline['avg_trade']}/t")
    print(f"   Volume: ${baseline['volume']:.2f} -> fee rate: {baseline['fee_rate_final']*100:.2f}bps")
    results["baseline"] = baseline

    # EXP 1: Deploy % sweep (compounding)
    print(f"\n🔬 EXP 1: Deploy % Sweep (compounding effect)")
    deploy_results = []
    for dpct in [0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 1.0]:
        r = run_backtest(rave_candles, btc_lookup, 4, RSI_ENTRY, RSI_EXIT, TP, SL, TO, deploy_pct=dpct)
        r["label"] = f"deploy={dpct*100:.0f}%"
        deploy_results.append(r)
        print(f"   {dpct*100:.0f}%: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, ${r['avg_trade']}/t, vol=${r['volume']:.2f}")
    results["deploy_sweep"] = deploy_results

    # EXP 2: 14-day extended backtest on ultimate config
    print(f"\n🔬 EXP 2: 14-day extended backtest")
    start_14d = now - 14 * 24 * 3600
    rave_14d = fetch_candles(client, PRODUCT, start_14d, now)
    btc_14d = fetch_candles(client, BTC, start_14d, now, granularity="ONE_MINUTE")
    btc_lookup_14d = {int(c["start"]): float(c["close"]) for c in btc_14d}
    print(f"   Loaded {len(rave_14d)} RAVE 14d candles")

    ext_14d = run_backtest(rave_14d, btc_lookup_14d, 4, RSI_ENTRY, RSI_EXIT, TP, SL, TO)
    ext_14d["label"] = "14-day extended"
    results["extended_14d"] = ext_14d
    print(f"   14d: ${ext_14d['net']:.2f} ({ext_14d['return_pct']}%), {ext_14d['trades']}t, {ext_14d['wr']}% WR")
    print(f"   Avg per 72h: ${ext_14d['net']/4.67:.2f}")

    # EXP 3: 14-day deploy sweep
    print(f"\n🔬 EXP 3: 14-day deploy % sweep")
    deploy_14d = []
    for dpct in [0.70, 0.80, 0.90, 0.95, 1.0]:
        r = run_backtest(rave_14d, btc_lookup_14d, 4, RSI_ENTRY, RSI_EXIT, TP, SL, TO, deploy_pct=dpct)
        r["label"] = f"14d deploy={dpct*100:.0f}%"
        deploy_14d.append(r)
        print(f"   {dpct*100:.0f}%: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, ${r['avg_trade']}/t")
    results["deploy_14d"] = deploy_14d

    # EXP 4: Fee tier impact analysis
    print(f"\n🔬 EXP 4: Fee tier analysis")
    best_deploy_72h = max(deploy_results, key=lambda x: x["net"])
    print(f"   Best 72h deploy: {best_deploy_72h['label']} -> ${best_deploy_72h['net']:.2f}")
    print(f"   Volume: ${best_deploy_72h['volume']:.2f}")
    print(f"   Fee tier: {best_deploy_72h['fee_rate_final']*100:.2f}bps")
    if best_deploy_72h['volume'] >= 10000:
        print(f"   ✅ Qualifies for 25bps tier!")
    if best_deploy_72h['volume'] >= 50000:
        print(f"   ✅ Qualifies for 15bps tier!")
    results["fee_analysis"] = best_deploy_72h

    print(f"\n{'='*80}")
    print(f"🏆 COMPOUNDING + FEE SWEEP SUMMARY")
    print(f"{'='*80}")
    best_deploy = max(deploy_results, key=lambda x: x["net"])
    print(f"Best 72h config: {best_deploy['label']} -> ${best_deploy['net']:.2f} ({best_deploy['return_pct']}%)")
    print(f"14-day extended: ${ext_14d['net']:.2f} ({ext_14d['return_pct']}%), {ext_14d['trades']} trades")
    print(f"14-day per 72h avg: ${ext_14d['net']/4.67:.2f}")

    with open("reports/compounding_fee_sweep.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to reports/compounding_fee_sweep.json")

if __name__ == "__main__":
    main()
