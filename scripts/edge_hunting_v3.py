#!/usr/bin/env python3
"""
Edge Hunting v3 — Creative variations on the only thing that works (RSI).

Instead of testing new strategy families (all failed), let's IMPROVE the RSI system:
1. Asymmetric position sizing — bigger on stronger signals
2. Partial exits — take 50% at TP, trail the rest
3. Volume climax reversal — volume spike + down candle → bounce
4. Consecutive down candle reversal — 3+ red → bet on bounce
5. RSI divergence — price lower low, RSI higher low
6. Dynamic coin selection — rotate based on recent volatility
7. Session filtering — only trade during high-volatility hours
8. Adaptive RSI thresholds — adjust based on recent volatility regime
"""
import json, time, datetime
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "edge_hunting_v3.json"

# All coins we have data for
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


def backtest_rsi_variations(candles, params, starting_cash=24.0, fee_rate=0.004,
                             partial_exit=False, asymmetric_sizing=False,
                             session_filter=False, adaptive_threshold=False):
    """
    RSI backtester with advanced features.
    """
    if len(candles) < 30:
        return {"error": "not enough candles"}

    rsi_period = params.get("p", 7)
    os_level = params.get("os", 30)
    ob_level = params.get("ob", 75)
    tp_pct = params.get("t", 5.0) / 100.0
    sl_pct = params.get("s", 3.0) / 100.0
    max_hold = params.get("h", 24)

    closes_list = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    rsi_values = rsi(closes_list, rsi_period)

    cash = starting_cash
    in_position = False
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    partial_taken = False
    trades = []

    # For adaptive threshold: track recent volatility
    recent_returns = []

    for i in range(rsi_period + 10, len(candles)):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_values[i]

        # Session filter: only trade during high-vol hours (14-22 UTC = US/EU overlap)
        if session_filter:
            dt = datetime.datetime.fromtimestamp(c["time"], tz=datetime.timezone.utc)
            if dt.hour < 14 or dt.hour > 22:
                continue

        # Adaptive threshold: adjust OS based on recent volatility
        effective_os = os_level
        if adaptive_threshold:
            if len(recent_returns) >= 20:
                vol = sum(abs(r) for r in recent_returns[-20:]) / 20
                if vol > 0.02:  # High vol → lower OS (harder to enter)
                    effective_os = max(20, os_level - 5)
                elif vol < 0.005:  # Low vol → higher OS (easier to enter)
                    effective_os = min(40, os_level + 5)

        # Track returns for adaptive threshold
        if i > 0 and closes_list[i-1] > 0:
            ret = (cl - closes_list[i-1]) / closes_list[i-1]
            recent_returns.append(ret)

        # Exit
        if in_position:
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
            bars_held = i - entry_bar

            exit_price = None
            exit_reason = None

            if partial_exit and not partial_taken:
                half_tp = entry_price * (1 + tp_pct * 0.5)
                if h >= half_tp:
                    # Take 50% profit, trail rest with tighter stop
                    half_qty = qty * 0.5
                    half_gross = (half_tp - entry_price) * half_qty
                    half_fee = half_tp * half_qty * fee_rate
                    half_net = half_gross - half_fee
                    cash += half_tp * half_qty - half_fee
                    qty = half_qty
                    partial_taken = True
                    # Tighten stop to breakeven
                    sl = entry_price * 1.001  # Just above entry

            if h >= tp:
                exit_price = tp
                exit_reason = "tp"
            elif l <= sl:
                exit_price = sl
                exit_reason = "sl"
            elif current_rsi >= ob_level:
                exit_price = cl
                exit_reason = "rsi_ob"
            elif bars_held >= max_hold:
                exit_price = cl
                exit_reason = "timeout"

            if exit_price:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({
                    "entry_bar": entry_bar, "exit_bar": i,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "gross_pnl": round(gross, 4), "fee": round(entry_fee + exit_fee, 4),
                    "net_pnl": round(net, 4), "hold_bars": i - entry_bar,
                    "partial_exit": partial_taken,
                })
                in_position = False
                partial_taken = False

        # Entry
        if not in_position:
            # Volume climax reversal: volume 3x avg + price down 3%+ → bounce
            vol_signal = False
            if i >= 20:
                avg_vol = sum(volumes[i-20:i]) / 20
                if avg_vol > 0 and volumes[i] > avg_vol * 3.0:
                    candle_drop = (cl - float(candles[i]["open"])) / float(candles[i]["open"])
                    if candle_drop < -0.03:  # 3%+ down candle on huge volume
                        vol_signal = True

            # Consecutive down candles: 3+ red → bounce
            consec_down = False
            if i >= 3:
                consec_down = all(candles[i-j]["close"] < candles[i-j]["open"] for j in range(3))

            # RSI divergence: price lower low, RSI higher low (simplified)
            rsi_divergence = False
            if i >= 30:
                # Check if price made lower low in last 10 bars but RSI made higher low
                recent_lows = [candles[i-j]["low"] for j in range(10)]
                recent_rsi = [rsi_values[i-j] for j in range(10)]
                if len(recent_lows) >= 5:
                    price_trend = sum(1 for j in range(4) if recent_lows[j+1] < recent_lows[j])
                    rsi_trend = sum(1 for j in range(4) if recent_rsi[j+1] > recent_rsi[j])
                    if price_trend >= 3 and rsi_trend >= 3:
                        rsi_divergence = True

            # Base RSI signal
            rsi_signal = current_rsi <= effective_os

            # Asymmetric sizing: stronger signals get bigger positions
            deploy = cash
            if asymmetric_sizing and rsi_signal:
                if current_rsi <= 20:
                    deploy = cash  # Full deploy for very oversold
                elif current_rsi <= 25:
                    deploy = cash * 0.8
                else:
                    deploy = cash * 0.5

            # Enter on ANY signal (RSI, vol climax, consec down, divergence)
            if rsi_signal or vol_signal or consec_down or rsi_divergence:
                if deploy >= 1.0:
                    entry_price = cl
                    entry_fee = entry_price * (deploy / entry_price) * fee_rate
                    qty = (deploy - entry_fee) / entry_price
                    if qty > 0:
                        cash -= deploy
                        in_position = True
                        entry_bar = i
                        entry_fee = entry_fee

    if in_position:
        exit_price = float(candles[-1]["close"])
        gross = (exit_price - entry_price) * qty
        exit_fee = exit_price * qty * fee_rate
        net = gross - entry_fee - exit_fee
        trades.append({"entry_bar": entry_bar, "exit_bar": len(candles)-1,
                       "entry_price": entry_price, "exit_price": exit_price,
                       "exit_reason": "end_of_data",
                       "gross_pnl": round(gross, 4), "fee": round(entry_fee + exit_fee, 4),
                       "net_pnl": round(net, 4), "hold_bars": len(candles)-1 - entry_bar,
                       "partial_exit": partial_taken})

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

    # Load optimal params
    params_path = ROOT / "reports" / "rsi_optimal_params.json"
    all_params = json.loads(params_path.read_text(encoding="utf-8"))

    all_results = {}

    # Test variations on RSI for top coins
    test_coins = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]

    variations = [
        {"name": "baseline", "kwargs": {}},
        {"name": "partial_exit", "kwargs": {"partial_exit": True}},
        {"name": "asymmetric_sizing", "kwargs": {"asymmetric_sizing": True}},
        {"name": "session_filter", "kwargs": {"session_filter": True}},
        {"name": "adaptive_threshold", "kwargs": {"adaptive_threshold": True}},
        {"name": "partial_exit+asymmetric", "kwargs": {"partial_exit": True, "asymmetric_sizing": True}},
        {"name": "session_filter+adaptive", "kwargs": {"session_filter": True, "adaptive_threshold": True}},
        {"name": "ALL_features", "kwargs": {"partial_exit": True, "asymmetric_sizing": True,
                                            "session_filter": True, "adaptive_threshold": True}},
    ]

    print(f"\n{'='*130}")
    for pid in test_coins:
        params = all_params.get(pid, {"p": 7, "os": 30, "ob": 75, "t": 5.0, "s": 3.0, "h": 24})
        print(f"\n=== {pid} ===")

        for var in variations:
            r = backtest_rsi_variations(candles_cache[pid], params, **var["kwargs"])
            if "error" not in r:
                print(f"  {var['name']:25s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']}c, {r['win_rate']:.1%} WR, fees=${r['total_fees']:.2f}")

            if pid == test_coins[0]:
                if var["name"] not in all_results:
                    all_results[var["name"]] = {"total_net": 0, "total_trades": 0, "total_fees": 0, "coins_tested": 0}
                all_results[var["name"]]["total_net"] += r.get("realized_net", 0)
                all_results[var["name"]]["total_trades"] += r.get("trades", 0)
                all_results[var["name"]]["total_fees"] += r.get("total_fees", 0)
                all_results[var["name"]]["coins_tested"] += 1

    # Summary
    print(f"\n{'='*130}")
    print(f"VARIATION SUMMARY (across {len(test_coins)} coins):")
    print(f"{'='*130}")
    for name, data in sorted(all_results.items(), key=lambda x: x[1]["total_net"], reverse=True):
        print(f"  {name:25s}: ${data['total_net']:+.2f} total, {data['total_trades']} trades, ${data['total_fees']:.2f} fees, {data['coins_tested']} coins")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
