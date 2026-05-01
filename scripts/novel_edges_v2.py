#!/usr/bin/env python3
"""
Novel Edge Discovery v2 — Testing completely new structural edges.

Hypotheses:
1. Volume momentum breakout (volume spike + price direction → continuation)
2. Extreme move mean reversion (5%+ single candle → 50% reversion)
3. Opening range breakout (first 3 bars of hour set direction)
4. Multi-timeframe RSI confluence (5m + 15m both oversold)
5. Time-of-day optimization (different params per UTC hour)
6. Gap fill (candle opens above/below prev close → revert)
7. Bollinger Band squeeze breakout (low vol → expansion direction)
8. VWAP mean reversion (price far from VWAP → revert)
"""
import json, time, datetime
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "novel_edges_v2.json"

PRODUCTS = [
    "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "TROLL-USD", "NOM-USD", "CFG-USD", "DASH-USD", "IRYS-USD",
    "FARTCOIN-USD", "BOBBOB-USD", "MON-USD", "ZEC-USD", "VVV-USD",
]


def fetch_candles_72h(client, pid, granularity="FIVE_MINUTE"):
    gsec_map = {"FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900, "ONE_MINUTE": 60}
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


def backtest_signals(candles, signal_fn, starting_cash=24.0, fee_rate=0.004):
    """Generic backtester for signal-based strategies."""
    if len(candles) < 30:
        return {"error": "not enough candles"}

    cash = starting_cash
    in_position = False
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    trades = []

    for i in range(20, len(candles)):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])

        if in_position:
            signal = signal_fn(candles, i, "exit", entry_price, entry_bar)
            if signal:
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
        else:
            signal = signal_fn(candles, i, "entry", None, None)
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
    losses = [t for t in trades if t["net_pnl"] <= 0]
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins)/max(1,len(trades)), 3),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades)/starting_cash*100, 2),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades)/max(1,len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
        "median_hold_bars": sorted(t["hold_bars"] for t in trades)[len(trades)//2] if trades else 0,
    }


# ========== EDGE 1: Volume Momentum Breakout ==========
def test_volume_momentum(candles):
    """Volume 2x average + price up → continue up. Volume 2x + price down → continue down (skip for long-only)."""
    def signal_fn(candles, i, direction, entry_price, entry_bar):
        if direction == "entry":
            # Check if volume spike AND price up
            volumes = [c["volume"] for c in candles[max(0,i-20):i]]
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            curr_vol = candles[i]["volume"]
            if avg_vol > 0 and curr_vol > avg_vol * 2.0:
                # Price moved up this candle
                o = candles[i]["open"]
                cl = candles[i]["close"]
                if cl > o:  # Bullish candle
                    return cl  # Enter at close, expect continuation
            return None
        else:  # exit
            # Exit after 3 bars or 3% TP or 2% SL
            bars_held = i - entry_bar
            tp = entry_price * 1.03
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

    return backtest_signals(candles, signal_fn)


# ========== EDGE 2: Extreme Move Fade ==========
def test_extreme_move_fade(candles):
    """After 5%+ single candle, bet on 50% reversion."""
    def signal_fn(candles, i, direction, entry_price, entry_bar):
        if direction == "entry":
            prev = candles[i-1]
            o = prev["open"]
            h = prev["high"]
            l = prev["low"]
            cl = prev["close"]
            mid = (o + cl) / 2 if (o + cl) > 0 else 1
            range_pct = (h - l) / mid * 100
            if range_pct >= 5.0:
                # Fade the direction: if candle closed up, enter at close expecting pullback
                # But we can only go long, so only fade DOWN candles
                if cl < o:  # Red candle, bet on bounce
                    return candles[i]["open"]  # Enter at next open
            return None
        else:
            bars_held = i - entry_bar
            # Target 50% reversion of the big candle
            tp = entry_price * 1.025  # 2.5% target
            sl = entry_price * 0.98   # 2% SL
            h = candles[i]["high"]
            l = candles[i]["low"]
            if h >= tp:
                return tp
            elif l <= sl:
                return sl
            elif bars_held >= 6:
                return candles[i]["close"]
            return None

    return backtest_signals(candles, signal_fn)


# ========== EDGE 3: Opening Range Breakout ==========
def test_opening_range(candles):
    """First 3 bars of each UTC hour set direction. Trade breakout."""
    def signal_fn(candles, i, direction, entry_price, entry_bar):
        if direction == "entry":
            t = candles[i]["time"]
            dt = datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
            minute = dt.minute

            # First 3 bars of the hour (0-14 minutes on 5-min candles)
            if minute <= 10 and i >= 3:
                # Check if first 3 bars of this hour all moved same direction
                bars = []
                for j in range(max(0, i-2), i+1):
                    bars.append(candles[j])
                if len(bars) == 3:
                    all_up = all(b["close"] > b["open"] for b in bars)
                    if all_up:
                        return candles[i]["close"]  # Enter on confirmed uptrend
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
            elif bars_held >= 6:
                return candles[i]["close"]
            return None

    return backtest_signals(candles, signal_fn)


# ========== EDGE 4: Multi-Timeframe RSI Confluence ==========
def test_multiframe_rsi(candles_5m, candles_15m):
    """5-min RSI(7) oversold AND 15-min RSI(14) oversold → higher conviction."""
    if len(candles_5m) < 30 or len(candles_15m) < 10:
        return {"error": "not enough candles"}

    closes_5m = [c["close"] for c in candles_5m]
    closes_15m = [c["close"] for c in candles_15m]
    rsi_5m = rsi(closes_5m, 7)
    rsi_15m = rsi(closes_15m, 14)

    # Build time lookup for 15m candles
    time_15m = {int(c["time"]): i for i, c in enumerate(candles_15m)}

    fee_rate = 0.004
    cash = 24.0
    in_position = False
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    trades = []

    for i in range(20, len(candles_5m)):
        c = candles_5m[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])

        if in_position:
            tp = entry_price * 1.04
            sl = entry_price * 0.97
            exit_price = None
            if h >= tp:
                exit_price = tp
            elif l <= sl:
                exit_price = sl
            elif rsi_5m[i] >= 70:
                exit_price = cl

            if exit_price:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({"net_pnl": round(net, 4), "fee": round(entry_fee + exit_fee, 4),
                               "hold_bars": i - entry_bar})
                in_position = False
        else:
            # Check confluence: 5m RSI < 30 AND 15m RSI < 35
            rsi_5m_val = rsi_5m[i]
            t = int(c["time"])
            # Find nearest 15m candle
            rsi_15m_val = 50
            for t15 in sorted(time_15m.keys()):
                if t15 <= t:
                    rsi_15m_val = rsi_15m[time_15m[t15]]

            if rsi_5m_val < 30 and rsi_15m_val < 35:
                deploy = cash
                if deploy >= 1.0:
                    entry_price = cl
                    entry_fee = entry_price * (deploy / entry_price) * fee_rate
                    qty = (deploy - entry_fee) / entry_price
                    if qty > 0:
                        cash -= deploy
                        in_position = True
                        entry_bar = i

    if in_position:
        exit_price = float(candles_5m[-1]["close"])
        gross = (exit_price - entry_price) * qty
        exit_fee = exit_price * qty * fee_rate
        net = gross - entry_fee - exit_fee
        trades.append({"net_pnl": round(net, 4), "fee": round(entry_fee + exit_fee, 4),
                       "hold_bars": len(candles_5m)-1 - entry_bar})

    wins = [t for t in trades if t["net_pnl"] > 0]
    return {
        "trades": len(trades), "wins": len(wins),
        "win_rate": round(len(wins)/max(1,len(trades)), 3),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades)/24.0*100, 2),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades)/max(1,len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
    }


# ========== EDGE 5: Gap Fill ==========
def test_gap_fill(candles):
    """If candle opens significantly above/below previous close, bet on reversion."""
    def signal_fn(candles, i, direction, entry_price, entry_bar):
        if direction == "entry":
            if i < 2:
                return None
            prev_close = candles[i-1]["close"]
            curr_open = candles[i]["open"]
            gap_pct = abs(curr_open - prev_close) / prev_close * 100

            if gap_pct >= 1.0:  # 1%+ gap
                # Gap down → bet on bounce (long)
                if curr_open < prev_close:
                    return curr_open
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
            elif bars_held >= 4:
                return candles[i]["close"]
            return None

    return backtest_signals(candles, signal_fn)


# ========== EDGE 6: VWAP Mean Reversion ==========
def test_vwap_reversion(candles):
    """When price is >3% from VWAP, bet on reversion."""
    def signal_fn(candles, i, direction, entry_price, entry_bar):
        if direction == "entry":
            # Calculate VWAP over last 20 bars
            typical_prices = []
            volumes = []
            for j in range(max(0, i-20), i):
                c = candles[j]
                tp = (c["high"] + c["low"] + c["close"]) / 3
                typical_prices.append(tp)
                volumes.append(c["volume"])

            if not volumes or sum(volumes) == 0:
                return None

            vwap = sum(tp * v for tp, v in zip(typical_prices, volumes)) / sum(volumes)
            curr_price = candles[i]["close"]
            deviation = (curr_price - vwap) / vwap * 100

            # Price > 2% below VWAP → bet on bounce
            if deviation < -2.0:
                return curr_price
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
            elif bars_held >= 6:
                return candles[i]["close"]
            return None

    return backtest_signals(candles, signal_fn)


# ========== EDGE 7: Bollinger Band Squeeze Breakout ==========
def test_bb_squeeze(candles):
    """When BB width compresses to bottom 10%, trade the breakout direction."""
    def signal_fn(candles, i, direction, entry_price, entry_bar):
        if direction == "entry":
            if i < 25:
                return None
            # Calculate 20-bar BB
            closes = [candles[j]["close"] for j in range(i-20, i)]
            sma = sum(closes) / len(closes)
            std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5

            # BB width as % of SMA
            bb_width = (2 * std) / sma * 100

            # If BB width is very narrow (<0.5%), prepare for breakout
            if bb_width < 0.5 and i >= 2:
                # Trade direction of last candle
                if candles[i]["close"] > candles[i]["open"]:
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

    return backtest_signals(candles, signal_fn)


# ========== EDGE 8: Time-of-Day Optimization ==========
def test_time_of_day(candles):
    """Test each UTC hour separately to find profitable windows."""
    results = {}
    for hour in range(24):
        # Filter candles by hour
        hour_candles = []
        for c in candles:
            dt = datetime.datetime.fromtimestamp(c["time"], tz=datetime.timezone.utc)
            if dt.hour == hour:
                hour_candles.append(c)

        if len(hour_candles) < 20:
            continue

        # Simple RSI mean reversion during this hour
        def signal_fn(candles, i, direction, entry_price, entry_bar):
            if direction == "entry":
                if i < 10:
                    return None
                closes = [c["close"] for c in candles[max(0,i-10):i]]
                rsi_val = rsi(closes, 7)[-1]
                if rsi_val < 25:
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

        result = backtest_signals(hour_candles, signal_fn)
        if result.get("trades", 0) >= 3:
            results[str(hour)] = result

    return results


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles...")
    candles_cache = {}
    candles_15m_cache = {}
    for pid in PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid, "FIVE_MINUTE")
            print(f"  {pid}: {len(candles_cache[pid])} 5m candles")
        except Exception as e:
            print(f"  {pid}: 5m ERROR {e}")
        try:
            candles_15m_cache[pid] = fetch_candles_72h(client, pid, "FIFTEEN_MINUTE")
            print(f"  {pid}: {len(candles_15m_cache[pid])} 15m candles")
        except Exception as e:
            print(f"  {pid}: 15m ERROR {e}")

    all_results = {}

    # Test edges 1, 2, 3, 6, 7 (single-timeframe)
    for edge_name, edge_fn, label in [
        ("volume_momentum", test_volume_momentum, "Volume Momentum Breakout"),
        ("extreme_move_fade", test_extreme_move_fade, "Extreme Move Fade (5%+)"),
        ("opening_range", test_opening_range, "Opening Range Breakout"),
        ("gap_fill", test_gap_fill, "Gap Fill"),
        ("vwap_reversion", test_vwap_reversion, "VWAP Mean Reversion"),
        ("bb_squeeze", test_bb_squeeze, "BB Squeeze Breakout"),
    ]:
        print(f"\n=== {label} ===")
        edge_results = {}
        for pid in PRODUCTS:
            if pid not in candles_cache:
                continue
            r = edge_fn(candles_cache[pid])
            if "error" not in r and r.get("trades", 0) >= 3:
                edge_results[pid] = r
                print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
        all_results[edge_name] = {"label": label, "results": edge_results}

    # Edge 4: Multi-timeframe RSI confluence
    print(f"\n=== Multi-Frame RSI Confluence ===")
    edge_results = {}
    for pid in PRODUCTS:
        if pid not in candles_cache or pid not in candles_15m_cache:
            continue
        r = test_multiframe_rsi(candles_cache[pid], candles_15m_cache[pid])
        if "error" not in r and r.get("trades", 0) >= 3:
            edge_results[pid] = r
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR")
    all_results["multiframe_rsi"] = {"label": "Multi-Frame RSI Confluence", "results": edge_results}

    # Edge 8: Time-of-day
    print(f"\n=== Time-of-Day Optimization ===")
    edge_results = {}
    for pid in PRODUCTS:
        if pid not in candles_cache:
            continue
        r = test_time_of_day(candles_cache[pid])
        if r:
            edge_results[pid] = r
            best_hour = max(r.items(), key=lambda x: x[1].get("return_pct", 0))
            print(f"  {pid:15s}: Best hour {best_hour[0]}: ${best_hour[1]['realized_net']:+.2f} ({best_hour[1]['return_pct']:+.1f}%), {best_hour[1]['trades']}c")
    all_results["time_of_day"] = {"label": "Time-of-Day", "results": edge_results}

    # Summary: Top edges
    print(f"\n{'='*110}")
    print(f"TOP EDGES (by total realized net across all coins):")
    print(f"{'='*110}")

    edge_totals = []
    for edge_name, edge_data in all_results.items():
        total_net = sum(r.get("realized_net", 0) for r in edge_data["results"].values())
        total_trades = sum(r.get("trades", 0) for r in edge_data["results"].values())
        profitable_coins = sum(1 for r in edge_data["results"].values() if r.get("realized_net", 0) > 0)
        edge_totals.append((edge_name, edge_data["label"], total_net, total_trades, profitable_coins, len(edge_data["results"])))

    for edge_name, label, total_net, total_trades, profitable, total_coins in sorted(edge_totals, key=lambda x: x[2], reverse=True):
        print(f"  {label:35s}: ${total_net:+.2f} total, {total_trades} trades, {profitable}/{total_coins} coins profitable")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edges": all_results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
