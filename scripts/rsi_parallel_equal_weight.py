#!/usr/bin/env python3
"""
RSI Parallel Equal-Weight System — NO compounding.

Instead of compounding (which amplifies losses), deploy $48/N per coin
and keep position sizes constant. Wins increase cash but don't increase
the next trade size — each trade is a fixed fraction of starting capital.

This preserves the edge without the compounding death spiral.
"""
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

    # Test parallel equal-weight systems
    configs = [
        {"name": "parallel_5coins_fixed", "coins": products, "coins_count": 5},
        {"name": "parallel_3coins_fixed", "coins": products[:3], "coins_count": 3},
        {"name": "parallel_2coins_fixed", "coins": products[:2], "coins_count": 2},
    ]

    print(f"\n{'Config':30s} {'Cash':>8} {'Net':>8} {'Ret%':>7} {'Closes':>6} {'WR':>6} {'Fees':>8}")
    print("=" * 85)

    for cfg in configs:
        starting_cash = 48.0
        coins = cfg["coins"]
        per_coin = starting_cash / len(coins)  # Equal weight
        fee_rate = 0.004

        # Per-coin state
        coin_state = {}
        for pid in coins:
            coin_state[pid] = {
                "cash": per_coin,
                "realized": 0.0,
                "closes": 0,
                "wins": 0,
                "fees": 0.0,
                "in_position": False,
                "price_hist": [],
                "entry_price": 0,
                "entry_fee": 0,
                "qty": 0,
                "tp": 0,
                "sl": 0,
                "ob": 0,
                "p": 0,
                "entry_bar": 0,
                "current_bar": 0,
            }

        for t in all_times:
            tick = time_lookup.get(t, {})

            for pid in coins:
                if pid not in tick:
                    continue
                c = tick[pid]
                cl = float(c["close"])
                h = float(c["high"])
                l = float(c["low"])
                st = coin_state[pid]
                st["price_hist"].append(cl)
                if len(st["price_hist"]) > 100:
                    st["price_hist"] = st["price_hist"][-100:]
                st["current_bar"] += 1

                p = params.get(pid, {})
                if not p:
                    continue

                # Exit
                if st["in_position"]:
                    rsi_vals = rsi(st["price_hist"], st["p"])
                    rsi_val = rsi_vals[-1] if rsi_vals else 50
                    tp = st["entry_price"] * (1 + st["tp"])
                    sl = st["entry_price"] * (1 - st["sl"])

                    exit_price = None
                    if h >= tp:
                        exit_price = tp
                    elif l <= sl:
                        exit_price = sl
                    elif rsi_val >= st["ob"]:
                        exit_price = cl

                    if exit_price is not None:
                        gross = (exit_price - st["entry_price"]) * st["qty"]
                        exit_fee = exit_price * st["qty"] * fee_rate
                        net = gross - st["entry_fee"] - exit_fee
                        st["realized"] += net
                        st["closes"] += 1
                        st["fees"] += st["entry_fee"] + exit_fee
                        st["cash"] += exit_price * st["qty"] - exit_fee
                        if net > 0:
                            st["wins"] += 1
                        st["in_position"] = False

                # Entry
                if not st["in_position"]:
                    rsi_vals = rsi(st["price_hist"], p["p"])
                    rsi_val = rsi_vals[-1] if rsi_vals else 50

                    if rsi_val <= p["os"]:
                        deploy = st["cash"]  # Use all coin's cash
                        if deploy >= 1.0:
                            entry_fee = cl * (deploy / cl) * fee_rate
                            qty = (deploy - entry_fee) / cl
                            if qty > 0:
                                st["cash"] -= deploy
                                st["fees"] += entry_fee
                                st["in_position"] = True
                                st["entry_price"] = cl
                                st["entry_fee"] = entry_fee
                                st["qty"] = qty
                                st["tp"] = p["t"] / 100.0
                                st["sl"] = p["s"] / 100.0
                                st["ob"] = p["ob"]
                                st["p"] = p["p"]
                                st["entry_bar"] = st["current_bar"]

        total_cash = sum(st["cash"] for st in coin_state.values())
        total_realized = sum(st["realized"] for st in coin_state.values())
        total_closes = sum(st["closes"] for st in coin_state.values())
        total_wins = sum(st["wins"] for st in coin_state.values())
        total_fees = sum(st["fees"] for st in coin_state.values())
        wr = total_wins / max(1, total_closes) * 100
        ret = total_realized / starting_cash * 100

        print(f"{cfg['name']:30s} ${total_cash:>6.2f} ${total_realized:>+6.2f} {ret:>+6.1f}% {total_closes:>6} {wr:>5.1f}% ${total_fees:>6.2f}")

        # Per-coin breakdown
        for pid in coins:
            st = coin_state[pid]
            coin_wr = st["wins"] / max(1, st["closes"]) * 100
            coin_ret = st["realized"] / per_coin * 100
            print(f"    {pid:15s}: ${st['realized']:+.2f} ({coin_ret:+.1f}%), {st['closes']}c, {coin_wr:.1f}% WR")

if __name__ == "__main__":
    main()
