#!/usr/bin/env python3
"""Quick position sizing sweep for RSI compound."""
import json, time
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent

def fetch_candles_72h(client, pid, granularity="FIVE_MINUTE"):
    gsec = 300
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_c = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(pid, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles", [])
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_c.append({"time": t, "open": float(c["open"]), "high": float(c["high"]),
                              "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0))})
        chunk_end = chunk_start - 1
        time.sleep(0.06)
    return sorted(all_c, key=lambda x: x["time"])

def rsi(closes, period):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result

def main():
    client = CoinbaseAdvancedClient()
    products = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    params_path = ROOT / "reports" / "rsi_optimal_params.json"
    params = json.loads(params_path.read_text(encoding="utf-8"))

    print("Fetching candles...")
    candles_cache = {}
    for pid in products:
        candles_cache[pid] = fetch_candles_72h(client, pid)
        print(f"  {pid}: {len(candles_cache[pid])} candles")

    all_times = set()
    time_lookup = {}
    for pid, candles in candles_cache.items():
        for c in candles:
            t = int(c["time"])
            all_times.add(t)
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c
    all_times = sorted(all_times)

    configs = [
        {"name": "gemini_95pct_1conc", "deploy": 0.95, "max_conc": 1},
        {"name": "conservative_70pct_1conc", "deploy": 0.70, "max_conc": 1},
        {"name": "conservative_50pct_1conc", "deploy": 0.50, "max_conc": 1},
        {"name": "multi_70pct_2conc", "deploy": 0.70, "max_conc": 2},
        {"name": "multi_50pct_3conc", "deploy": 0.50, "max_conc": 3},
        {"name": "ultra_conserv_30pct_3conc", "deploy": 0.30, "max_conc": 3},
    ]

    print(f"\n{'Config':35s} {'Cash':>8} {'Net':>8} {'Ret%':>7} {'Closes':>6} {'WR':>6} {'Fees':>8}")
    print("=" * 90)

    for cfg in configs:
        cash = 48.0
        positions = {}
        realized = 0.0
        closes = 0
        wins = 0
        fees_total = 0.0
        fee_rate = 0.004
        price_hist = {pid: [] for pid in products}

        for t in all_times:
            tick = time_lookup.get(t, {})

            # Exits
            for pid in list(positions.keys()):
                if pid not in tick:
                    continue
                c = tick[pid]
                cl = float(c["close"])
                h = float(c["high"])
                l = float(c["low"])
                pos = positions[pid]
                price_hist[pid].append(cl)
                if len(price_hist[pid]) > 100:
                    price_hist[pid] = price_hist[pid][-100:]

                tp = pos["entry"] * (1 + pos["tp"])
                sl = pos["entry"] * (1 - pos["sl"])
                rsi_vals = rsi(price_hist[pid], pos["p"])
                rsi_val = rsi_vals[-1] if rsi_vals else 50

                exit_price = None
                if h >= tp:
                    exit_price = tp
                elif l <= sl:
                    exit_price = sl
                elif rsi_val >= pos["ob"]:
                    exit_price = cl

                if exit_price is not None:
                    qty = pos["qty"]
                    gross = (exit_price - pos["entry"]) * qty
                    exit_fee = exit_price * qty * fee_rate
                    net = gross - pos["entry_fee"] - exit_fee
                    realized += net
                    closes += 1
                    fees_total += pos["entry_fee"] + exit_fee
                    cash += exit_price * qty - exit_fee
                    if net > 0:
                        wins += 1
                    del positions[pid]

            # Entries
            if len(positions) < cfg["max_conc"]:
                for pid in products:
                    if pid in positions or pid not in tick:
                        continue
                    c = tick[pid]
                    cl = float(c["close"])
                    price_hist[pid].append(cl)
                    if len(price_hist[pid]) > 100:
                        price_hist[pid] = price_hist[pid][-100:]

                    p = params.get(pid, {})
                    if not p:
                        continue
                    rsi_vals = rsi(price_hist[pid], p["p"])
                    rsi_val = rsi_vals[-1] if rsi_vals else 50

                    if rsi_val <= p["os"]:
                        deploy = cash * cfg["deploy"]
                        if deploy >= 1.0:
                            entry_fee = cl * (deploy / cl) * fee_rate
                            qty = (deploy - entry_fee) / cl
                            if qty > 0:
                                cash -= deploy
                                fees_total += entry_fee
                                positions[pid] = {
                                    "entry": cl, "tp": p["t"]/100.0, "sl": p["s"]/100.0,
                                    "ob": p["ob"], "p": p["p"], "qty": qty, "entry_fee": entry_fee,
                                }
                                break

        wr = wins / max(1, closes) * 100
        net_pct = realized / 48.0 * 100
        print(f"{cfg['name']:35s} ${cash:>6.2f} ${realized:>+6.2f} {net_pct:>+6.1f}% {closes:>6} {wr:>5.1f}% ${fees_total:>6.2f}")

if __name__ == "__main__":
    main()
