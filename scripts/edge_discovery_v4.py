#!/usr/bin/env python3
"""
Edge Discovery v4 — Structural advantages we haven't tested yet.

New ideas:
1. Momentum spillover with delay (RAVE pumps → BAL/BLUR follow 2-3 bars later)
2. Microcap momentum trailing (small coins with momentum keep going)
3. Weekend vs weekday edge (different volatility patterns)
4. Multi-coin rotation (rotate capital to most oversold coin)
5. Trailing stops instead of fixed TP (capture more upside)
6. Volume confirmation on RSI entries (capitulation volume + oversold)
7. Bollinger Band bounce (price at lower band + RSI oversold = double signal)
8. Stochastic RSI (faster signal than regular RSI)
"""
import json, time, datetime
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "edge_discovery_v4.json"

PRODUCTS = [
    "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "TROLL-USD", "NOM-USD", "CFG-USD", "DASH-USD", "IRYS-USD",
    "FARTCOIN-USD", "BOBBOB-USD", "MON-USD", "ZEC-USD", "VVV-USD",
]


def fetch_candles_72h(client, pid, granularity="FIVE_MINUTE"):
    gsec_map = {"FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
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
        time.sleep(0.05)
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


def stochastic_rsi(closes, rsi_period=14, stoch_period=14):
    """Stochastic RSI — faster signal than regular RSI."""
    rsi_vals = rsi(closes, rsi_period)
    if len(rsi_vals) < stoch_period + 1:
        return [50.0] * len(rsi_vals)
    
    result = []
    for i in range(len(rsi_vals)):
        if i < stoch_period:
            result.append(50.0)
        else:
            window = rsi_vals[i-stoch_period:i+1]
            lowest = min(window)
            highest = max(window)
            if highest == lowest:
                result.append(50.0)
            else:
                result.append((rsi_vals[i] - lowest) / (highest - lowest) * 100)
    return result


def backtest_generic(candles, signal_fn, starting_cash=24.0, fee_rate=0.004):
    """Generic backtester."""
    if len(candles) < 30:
        return {"error": "not enough candles"}

    cash = starting_cash
    in_position = False
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    trades = []
    trail_high = 0

    for i in range(20, len(candles)):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])

        if in_position:
            signal = signal_fn(candles, i, "exit", entry_price, entry_bar, trail_high)
            if signal:
                if isinstance(signal, tuple):
                    exit_price, trail_high = signal
                else:
                    exit_price = signal
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({
                    "entry_bar": entry_bar, "exit_bar": i,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "gross_pnl": round(gross, 4), "fee": round(entry_fee + exit_fee, 4),
                    "net_pnl": round(net, 4), "hold_bars": i - entry_bar,
                })
                in_position = False
                trail_high = 0
        else:
            signal = signal_fn(candles, i, "entry", None, None, 0)
            if signal:
                entry_price = signal
                deploy = cash
                if deploy >= 1.0:
                    entry_fee = entry_price * (deploy / entry_price) * fee_rate
                    qty = (deploy - entry_fee) / entry_price
                    if qty > 0:
                        cash -= deploy
                        in_position = True
                        entry_bar = i
                        trail_high = entry_price

    if in_position:
        exit_price = float(candles[-1]["close"])
        gross = (exit_price - entry_price) * qty
        exit_fee = exit_price * qty * fee_rate
        net = gross - entry_fee - exit_fee
        trades.append({"entry_bar": entry_bar, "exit_bar": len(candles)-1,
                       "entry_price": entry_price, "exit_price": exit_price,
                       "gross_pnl": round(gross, 4), "fee": round(entry_fee + exit_fee, 4),
                       "net_pnl": round(net, 4), "hold_bars": len(candles)-1 - entry_bar})

    wins = [t for t in trades if t["net_pnl"] > 0]
    return {
        "trades": len(trades), "wins": len(wins),
        "win_rate": round(len(wins)/max(1,len(trades)), 3),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades)/starting_cash*100, 2),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades)/max(1,len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
    }


# ========== EDGE 1: Momentum Spillover with Delay ==========
def test_momentum_spillover(candles_by_pid, leader="RAVE-USD"):
    """When leader pumps 3%+, bet on followers catching up in next 2-3 bars."""
    results = {}
    leader_candles = candles_by_pid.get(leader, [])
    if len(leader_candles) < 20:
        return results

    for pid, candles in candles_by_pid.items():
        if pid == leader or len(candles) < 20:
            continue

        # Build time lookup for leader
        leader_times = {int(c["time"]): i for i, c in enumerate(leader_candles)}

        def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
            if direction == "entry":
                t = int(candles[i]["time"])
                # Check if leader pumped 3%+ in last 1-2 bars
                for lt in sorted(leader_times.keys()):
                    if lt <= t:
                        leader_idx = leader_times[lt]
                        if leader_idx >= 1 and leader_idx < len(leader_candles):
                            leader_ret = (leader_candles[leader_idx]["close"] - leader_candles[leader_idx-1]["close"]) / leader_candles[leader_idx-1]["close"]
                            if leader_ret > 0.03:  # Leader pumped 3%+
                                # Check if this coin hasn't moved yet
                                if i >= 1:
                                    this_ret = (candles[i]["close"] - candles[i-1]["close"]) / candles[i-1]["close"]
                                    if this_ret < 0.01:  # This coin hasn't caught up yet
                                        return candles[i]["close"]
                return None
            else:
                bars_held = i - entry_bar
                tp = entry_price * 1.02
                sl = entry_price * 0.98
                h = candles[i]["high"]
                l = candles[i]["low"]
                if h >= tp:
                    return tp
                elif l <= sl:
                    return sl
                elif bars_held >= 3:
                    return candles[i]["close"]
                return None

        results[pid] = backtest_generic(candles, signal_fn)

    return results


# ========== EDGE 2: Microcap Momentum Trailing ==========
def test_momentum_trailing(candles):
    """When price moves up 2%+, trail with 1.5% stop. Let winners run."""
    def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
        if direction == "entry":
            # Enter on momentum: price up 2%+ from previous bar
            if i >= 1:
                prev = candles[i-1]["close"]
                curr = candles[i]["close"]
                if (curr - prev) / prev > 0.02:  # 2%+ move up
                    return curr
            return None
        else:
            # Trail with 1.5% stop from highest point
            h = candles[i]["high"]
            l = candles[i]["low"]
            if h > trail_high:
                trail_high = h
            trail_stop = trail_high * 0.985  # 1.5% trail
            if l <= trail_stop:
                return trail_stop
            # Also exit after 6 bars if no trail hit
            if i - entry_bar >= 6:
                return candles[i]["close"]
            return None

    return backtest_generic(candles, signal_fn)


# ========== EDGE 3: Weekend vs Weekday ==========
def test_weekend_vs_weekday(candles):
    """Test if weekend trades are more profitable."""
    weekend_candles = []
    weekday_candles = []
    for c in candles:
        dt = datetime.datetime.fromtimestamp(c["time"], tz=datetime.timezone.utc)
        if dt.weekday() >= 5:  # Sat=5, Sun=6
            weekend_candles.append(c)
        else:
            weekday_candles.append(c)

    def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
        if direction == "entry":
            if i < 10:
                return None
            closes = [c["close"] for c in candles[max(0,i-10):i]]
            rsi_val = rsi(closes, 7)[-1]
            if rsi_val < 30:
                return candles[i]["close"]
            return None
        else:
            bars_held = i - entry_bar
            tp = entry_price * 1.03
            sl = entry_price * 0.97
            h = candles[i]["high"]
            l = candles[i]["low"]
            if h >= tp:
                return tp
            elif l <= sl:
                return sl
            elif bars_held >= 6:
                return candles[i]["close"]
            return None

    weekend_result = backtest_generic(weekend_candles, signal_fn) if len(weekend_candles) > 20 else {"error": "not enough weekend candles"}
    weekday_result = backtest_generic(weekday_candles, signal_fn) if len(weekday_candles) > 20 else {"error": "not enough weekday candles"}

    return {"weekend": weekend_result, "weekday": weekday_result}


# ========== EDGE 4: Multi-Coin Rotation ==========
def test_multi_coin_rotation(candles_by_pid, params):
    """Rotate capital to the most oversold coin at any time."""
    products = list(candles_by_pid.keys())
    if len(products) < 3:
        return {"error": "need at least 3 coins"}

    # Build timeline
    all_times = set()
    time_lookup = {}
    for pid, candles in candles_by_pid.items():
        for c in candles:
            t = int(c["time"])
            all_times.add(t)
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c
    all_times = sorted(all_times)

    cash = 48.0
    in_position = False
    position_pid = None
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    price_history = {pid: [] for pid in products}
    trades = []
    fee_rate = 0.004

    for t in all_times:
        tick = time_lookup.get(t, {})

        # Update price history
        for pid in products:
            if pid in tick:
                price_history[pid].append(float(tick[pid]["close"]))
                if len(price_history[pid]) > 100:
                    price_history[pid] = price_history[pid][-100:]

        # Exit
        if in_position and position_pid in tick:
            c = tick[position_pid]
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])

            p = params.get(position_pid, {})
            tp = entry_price * (1 + p.get("t", 5.0) / 100.0)
            sl = entry_price * (1 - p.get("s", 3.0) / 100.0)

            rsi_vals = rsi(price_history[position_pid], p.get("p", 7))
            rsi_val = rsi_vals[-1] if rsi_vals else 50

            exit_price = None
            if h >= tp:
                exit_price = tp
            elif l <= sl:
                exit_price = sl
            elif rsi_val >= p.get("ob", 75):
                exit_price = cl

            if exit_price:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({"net_pnl": round(net, 4), "fee": round(entry_fee + exit_fee, 4)})
                in_position = False
                position_pid = None

        # Entry: find most oversold coin
        if not in_position:
            best_pid = None
            best_rsi = 999
            for pid in products:
                if pid in tick and pid in params:
                    p = params[pid]
                    ph = price_history[pid]
                    if len(ph) > p.get("p", 7):
                        rsi_val = rsi(ph, p.get("p", 7))[-1]
                        if rsi_val < best_rsi and rsi_val <= p.get("os", 30):
                            best_rsi = rsi_val
                            best_pid = pid

            if best_pid and cash >= 1.0:
                cl = float(tick[best_pid]["close"])
                entry_price = cl
                deploy = cash
                entry_fee = entry_price * (deploy / entry_price) * fee_rate
                qty = (deploy - entry_fee) / entry_price
                if qty > 0:
                    cash -= deploy
                    in_position = True
                    position_pid = best_pid
                    entry_bar = t

    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    return {
        "trades": len(trades), "wins": wins,
        "win_rate": round(wins/max(1,len(trades)), 3),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades)/48.0*100, 2),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades)/max(1,len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
    }


# ========== EDGE 5: Trailing Stops ==========
def test_trailing_stops(candles, params):
    """Use trailing stops instead of fixed TP."""
    def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
        if direction == "entry":
            if i < 10:
                return None
            closes = [c["close"] for c in candles[max(0,i-10):i]]
            rsi_val = rsi(closes, params.get("p", 7))[-1]
            if rsi_val < params.get("os", 30):
                return candles[i]["close"]
            return None
        else:
            h = candles[i]["high"]
            l = candles[i]["low"]
            
            # Update trail
            if h > trail_high:
                trail_high = h
            
            # Trail at 2% below highest point
            trail_stop = trail_high * 0.98
            if l <= trail_stop:
                return trail_stop
            
            # Hard stop at 3% below entry
            sl = entry_price * 0.97
            if l <= sl:
                return sl
            
            # Timeout after 12 bars
            if i - entry_bar >= 12:
                return candles[i]["close"]
            
            return None

    return backtest_generic(candles, signal_fn)


# ========== EDGE 6: Volume Confirmation on RSI ==========
def test_volume_confirmed_rsi(candles, params):
    """RSI oversold + volume spike = capitulation signal."""
    def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
        if direction == "entry":
            if i < 20:
                return None
            closes = [c["close"] for c in candles[max(0,i-10):i]]
            rsi_val = rsi(closes, params.get("p", 7))[-1]
            
            # Volume confirmation: current volume > 1.5x average
            volumes = [c["volume"] for c in candles[max(0,i-20):i]]
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            curr_vol = candles[i]["volume"]
            
            if rsi_val < params.get("os", 30) and avg_vol > 0 and curr_vol > avg_vol * 1.5:
                return candles[i]["close"]
            return None
        else:
            bars_held = i - entry_bar
            tp = entry_price * (1 + params.get("t", 5.0) / 100.0)
            sl = entry_price * (1 - params.get("s", 3.0) / 100.0)
            h = candles[i]["high"]
            l = candles[i]["low"]
            if h >= tp:
                return tp
            elif l <= sl:
                return sl
            elif bars_held >= params.get("h", 24):
                return candles[i]["close"]
            return None

    return backtest_generic(candles, signal_fn)


# ========== EDGE 7: Bollinger Band + RSI Confluence ==========
def test_bb_rsi_confluence(candles):
    """Price at lower BB + RSI oversold = double signal."""
    def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
        if direction == "entry":
            if i < 25:
                return None
            # Calculate 20-bar BB
            closes = [candles[j]["close"] for j in range(i-20, i)]
            sma = sum(closes) / len(closes)
            std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
            lower_bb = sma - 2 * std
            
            # RSI
            rsi_vals = rsi(closes, 7)
            rsi_val = rsi_vals[-1]
            
            # Both signals: price at/near lower BB AND RSI oversold
            curr_price = candles[i]["close"]
            if curr_price <= lower_bb * 1.005 and rsi_val < 30:
                return curr_price
            return None
        else:
            bars_held = i - entry_bar
            tp = entry_price * 1.03
            sl = entry_price * 0.97
            h = candles[i]["high"]
            l = candles[i]["low"]
            if h >= tp:
                return tp
            elif l <= sl:
                return sl
            elif bars_held >= 12:
                return candles[i]["close"]
            return None

    return backtest_generic(candles, signal_fn)


# ========== EDGE 8: Stochastic RSI ==========
def test_stochastic_rsi(candles):
    """Stochastic RSI for faster signals."""
    def signal_fn(candles, i, direction, entry_price, entry_bar, trail_high):
        if direction == "entry":
            if i < 30:
                return None
            closes = [c["close"] for c in candles[max(0,i-30):i]]
            stoch_rsi = stochastic_rsi(closes, 14, 14)
            stoch_val = stoch_rsi[-1]
            
            if stoch_val < 20:  # Oversold on stoch RSI
                return candles[i]["close"]
            return None
        else:
            bars_held = i - entry_bar
            tp = entry_price * 1.03
            sl = entry_price * 0.97
            h = candles[i]["high"]
            l = candles[i]["low"]
            if h >= tp:
                return tp
            elif l <= sl:
                return sl
            elif bars_held >= 12:
                return candles[i]["close"]
            return None

    return backtest_generic(candles, signal_fn)


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles...")
    candles_cache = {}
    for pid in PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    params_path = ROOT / "reports" / "rsi_optimal_params.json"
    all_params = json.loads(params_path.read_text(encoding="utf-8"))

    all_results = {}

    # Edge 1: Momentum Spillover
    print(f"\n=== Edge 1: Momentum Spillover (RAVE leader) ===")
    e1 = test_momentum_spillover(candles_cache)
    for pid, r in sorted(e1.items(), key=lambda x: x[1].get("realized_net", -999), reverse=True):
        if "error" not in r and r.get("trades", 0) >= 3:
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["momentum_spillover"] = e1

    # Edge 2: Momentum Trailing
    print(f"\n=== Edge 2: Microcap Momentum Trailing ===")
    e2 = {}
    for pid in PRODUCTS:
        if pid not in candles_cache:
            continue
        r = test_momentum_trailing(candles_cache[pid])
        if "error" not in r and r.get("trades", 0) >= 3:
            e2[pid] = r
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["momentum_trailing"] = e2

    # Edge 3: Weekend vs Weekday
    print(f"\n=== Edge 3: Weekend vs Weekday ===")
    e3 = {}
    for pid in PRODUCTS[:5]:
        if pid not in candles_cache:
            continue
        r = test_weekend_vs_weekday(candles_cache[pid])
        e3[pid] = r
        weekend = r.get("weekend", {})
        weekday = r.get("weekday", {})
        print(f"  {pid:15s}: Weekend ${weekend.get('realized_net', 0):+.2f} ({weekend.get('trades', 0)}c) vs Weekday ${weekday.get('realized_net', 0):+.2f} ({weekday.get('trades', 0)}c)")
    all_results["weekend_vs_weekday"] = e3

    # Edge 4: Multi-Coin Rotation
    print(f"\n=== Edge 4: Multi-Coin Rotation ===")
    rotation_coins = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    rotation_params = {pid: all_params[pid] for pid in rotation_coins if pid in all_params}
    rotation_candles = {pid: candles_cache[pid] for pid in rotation_coins if pid in candles_cache}
    e4 = test_multi_coin_rotation(rotation_candles, rotation_params)
    print(f"  Rotation: ${e4.get('realized_net', 0):+.2f} ({e4.get('return_pct', 0):+.1f}%), {e4.get('trades', 0)}c, {e4.get('win_rate', 0):.1%} WR")
    all_results["multi_coin_rotation"] = e4

    # Edge 5: Trailing Stops
    print(f"\n=== Edge 5: Trailing Stops ===")
    e5 = {}
    for pid in PRODUCTS[:5]:
        if pid not in candles_cache:
            continue
        p = all_params.get(pid, {"p": 7, "os": 30, "ob": 75, "t": 5.0, "s": 3.0, "h": 24})
        r = test_trailing_stops(candles_cache[pid], p)
        if "error" not in r and r.get("trades", 0) >= 3:
            e5[pid] = r
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["trailing_stops"] = e5

    # Edge 6: Volume Confirmed RSI
    print(f"\n=== Edge 6: Volume Confirmed RSI ===")
    e6 = {}
    for pid in PRODUCTS[:5]:
        if pid not in candles_cache:
            continue
        p = all_params.get(pid, {"p": 7, "os": 30, "ob": 75, "t": 5.0, "s": 3.0, "h": 24})
        r = test_volume_confirmed_rsi(candles_cache[pid], p)
        if "error" not in r and r.get("trades", 0) >= 3:
            e6[pid] = r
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["volume_confirmed_rsi"] = e6

    # Edge 7: BB + RSI Confluence
    print(f"\n=== Edge 7: BB + RSI Confluence ===")
    e7 = {}
    for pid in PRODUCTS[:5]:
        if pid not in candles_cache:
            continue
        r = test_bb_rsi_confluence(candles_cache[pid])
        if "error" not in r and r.get("trades", 0) >= 3:
            e7[pid] = r
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["bb_rsi_confluence"] = e7

    # Edge 8: Stochastic RSI
    print(f"\n=== Edge 8: Stochastic RSI ===")
    e8 = {}
    for pid in PRODUCTS[:5]:
        if pid not in candles_cache:
            continue
        r = test_stochastic_rsi(candles_cache[pid])
        if "error" not in r and r.get("trades", 0) >= 3:
            e8[pid] = r
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["stochastic_rsi"] = e8

    # Summary
    print(f"\n{'='*110}")
    print(f"TOP EDGES BY TOTAL NET:")
    print(f"{'='*110}")

    edge_totals = []
    for edge_name, edge_data in all_results.items():
        if isinstance(edge_data, dict) and "realized_net" in edge_data:
            total_net = edge_data.get("realized_net", 0)
            total_trades = edge_data.get("trades", 0)
            edge_totals.append((edge_name, edge_name, total_net, total_trades))
        elif isinstance(edge_data, dict):
            total_net = sum(r.get("realized_net", 0) for r in edge_data.values() if isinstance(r, dict) and "error" not in r)
            total_trades = sum(r.get("trades", 0) for r in edge_data.values() if isinstance(r, dict) and "error" not in r)
            edge_totals.append((edge_name, edge_name, total_net, total_trades))

    for edge_name, label, total_net, total_trades in sorted(edge_totals, key=lambda x: x[2], reverse=True):
        print(f"  {label:35s}: ${total_net:+.2f} total, {total_trades} trades")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
