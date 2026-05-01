#!/usr/bin/env python3
"""
Novel edge discovery engine — testing multiple structural patterns.

Tests:
1. Cross-product lead-lag (BTC moves → alt overreaction with delay)
2. Multi-timeframe RSI divergence (5-min vs 15-min RSI disagreement)
3. Candle sequence patterns (N green/red in a row → reversal)
4. Time-of-day profitability (which UTC hours are most profitable?)
5. Volatility compression → expansion (squeeze breakout direction)
6. Correlation breakdowns (BTC-ETH correlation breaks → alt mean reversion)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "novel_edge_discovery.json"

# Products to test
PRODUCTS = ["BTC-USD", "ETH-USD", "ARB-USD", "SOL-USD", "COMP-USD", "CHECK-USD", "BAL-USD", "WIF-USD"]


def fetch_candles_72h(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "FIVE_MINUTE") -> list[dict]:
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append({
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.15)
    return sorted(all_candles, key=lambda x: x["time"])


def rsi(closes: list[float], period: int) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
        else:
            result.append(100.0)
    return result


def test_candle_sequence_pattern(candles: list[dict], product_id: str, min_sequence: int = 4) -> dict:
    """Test: N consecutive green/red candles → reversal in next M bars."""
    if len(candles) < min_sequence + 10:
        return {"error": "not enough candles"}

    signals = []
    for i in range(min_sequence, len(candles) - 3):
        # Count consecutive green/red candles
        green = 0
        red = 0
        for j in range(min_sequence):
            if candles[i - min_sequence + j]["close"] >= candles[i - min_sequence + j]["open"]:
                green += 1
            else:
                red += 1

        if green >= min_sequence or red >= min_sequence:
            # Signal: reversal expected
            direction = "SELL" if green >= min_sequence else "BUY"
            entry_price = candles[i]["close"]

            # Check next 3 bars for reversal
            exit_price = candles[min(i + 2, len(candles) - 1)]["close"]
            if direction == "BUY":
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price

            signals.append({
                "bar": i,
                "sequence_length": green if green >= min_sequence else red,
                "direction": direction,
                "pnl_pct": round(pnl_pct * 100, 4),
                "win": pnl_pct > 0,
            })

    wins = sum(1 for s in signals if s["win"])
    return {
        "product_id": product_id,
        "strategy": f"candle_sequence_{min_sequence}",
        "total_signals": len(signals),
        "wins": wins,
        "win_rate": round(wins / len(signals), 3) if signals else 0,
        "avg_pnl_pct": round(sum(s["pnl_pct"] for s in signals) / len(signals), 4) if signals else 0,
        "best_signal": max(signals, key=lambda s: s["pnl_pct"]) if signals else None,
        "worst_signal": min(signals, key=lambda s: s["pnl_pct"]) if signals else None,
    }


def test_multitimeframe_rsi_divergence(candles_5m: list[dict], candles_15m: list[dict], product_id: str) -> dict:
    """Test: 5-min RSI oversold while 15-min RSI still falling → reversal signal."""
    if len(candles_5m) < 50 or len(candles_15m) < 20:
        return {"error": "not enough candles"}

    closes_5m = [c["close"] for c in candles_5m]
    closes_15m = [c["close"] for c in candles_15m]
    rsi_5m = rsi(closes_5m, 7)
    rsi_15m = rsi(closes_15m, 14)

    signals = []
    for i in range(20, min(len(rsi_5m), len(candles_5m))):
        # 5-min RSI is oversold (<30) but 15-min RSI is also oversold
        # This means the move is confirmed on both timeframes → potential reversal
        if rsi_5m[i] < 30 and i < len(rsi_15m) and rsi_15m[min(i // 3, len(rsi_15m) - 1)] < 35:
            entry_price = candles_5m[i]["close"]
            # Exit after 6 bars (30 min)
            exit_bar = min(i + 6, len(candles_5m) - 1)
            exit_price = candles_5m[exit_bar]["close"]
            pnl_pct = (exit_price - entry_price) / entry_price * 100

            signals.append({
                "bar": i,
                "rsi_5m": round(rsi_5m[i], 1),
                "rsi_15m": round(rsi_15m[min(i // 3, len(rsi_15m) - 1)], 1),
                "pnl_pct": round(pnl_pct, 4),
                "win": pnl_pct > 0,
            })

    wins = sum(1 for s in signals if s["win"])
    return {
        "product_id": product_id,
        "strategy": "multitimeframe_rsi_divergence",
        "total_signals": len(signals),
        "wins": wins,
        "win_rate": round(wins / len(signals), 3) if signals else 0,
        "avg_pnl_pct": round(sum(s["pnl_pct"] for s in signals) / len(signals), 4) if signals else 0,
    }


def test_time_of_day(candles_5m: list[dict], product_id: str) -> dict:
    """Test: Which UTC hours have the highest average return per 5-min bar?"""
    import datetime
    hour_returns: dict[int, list[float]] = {h: [] for h in range(24)}

    for i in range(1, len(candles_5m)):
        ret = (candles_5m[i]["close"] - candles_5m[i - 1]["close"]) / candles_5m[i - 1]["close"]
        dt = datetime.datetime.fromtimestamp(candles_5m[i]["time"], tz=datetime.timezone.utc)
        hour_returns[dt.hour].append(ret)

    hour_stats = {}
    for h in range(24):
        rets = hour_returns[h]
        if len(rets) > 10:
            avg_ret = sum(rets) / len(rets)
            pos_pct = sum(1 for r in rets if r > 0) / len(rets)
            hour_stats[str(h)] = {
                "bars": len(rets),
                "avg_return_bps": round(avg_ret * 10000, 2),
                "positive_pct": round(pos_pct * 100, 1),
            }

    # Find most profitable hours
    sorted_hours = sorted(hour_stats.items(), key=lambda x: abs(x[1]["avg_return_bps"]), reverse=True)

    return {
        "product_id": product_id,
        "strategy": "time_of_day_analysis",
        "hour_stats": hour_stats,
        "most_profitable_hours": [h for h, _ in sorted_hours[:5]],
        "least_profitable_hours": [h for h, _ in sorted_hours[-5:]],
    }


def test_volatility_squeeze(candles: list[dict], product_id: str) -> dict:
    """Test: Low volatility periods (squeeze) → expansion direction signal."""
    if len(candles) < 30:
        return {"error": "not enough candles"}

    # Calculate rolling 10-bar volatility (std of returns)
    closes = [c["close"] for c in candles]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]

    signals = []
    for i in range(15, len(returns) - 5):
        # Calculate 10-bar rolling std
        window = returns[max(0, i - 10):i]
        if len(window) < 5:
            continue
        mean_ret = sum(window) / len(window)
        std = (sum((r - mean_ret) ** 2 for r in window) / len(window)) ** 0.5

        # Squeeze: std is in bottom 20% of all stds
        # For simplicity, use absolute threshold
        if std < 0.001:  # Very low volatility
            # Check next 5 bars for expansion
            future_returns = returns[i:min(i + 5, len(returns))]
            if not future_returns:
                continue

            # Direction: use the sign of the first move
            first_move = future_returns[0]
            total_move = sum(future_returns)

            signals.append({
                "bar": i,
                "squeeze_std": round(std * 100, 4),
                "first_move_pct": round(first_move * 100, 4),
                "total_move_5bars_pct": round(total_move * 100, 4),
                "win": total_move > 0,
            })

    wins = sum(1 for s in signals if s["win"])
    return {
        "product_id": product_id,
        "strategy": "volatility_squeeze",
        "total_signals": len(signals),
        "wins": wins,
        "win_rate": round(wins / len(signals), 3) if signals else 0,
        "avg_move_5bars_pct": round(sum(s["total_move_5bars_pct"] for s in signals) / len(signals), 4) if signals else 0,
    }


def test_cross_product_lead_lag(client: CoinbaseAdvancedClient, products: list[str]) -> dict:
    """Test: When BTC moves X%, which alts overreact with a Y-bar delay?"""
    print("  Fetching BTC candles...")
    btc_candles = fetch_candles_72h(client, "BTC-USD", "FIVE_MINUTE")
    if len(btc_candles) < 20:
        return {"error": "not enough BTC candles"}

    btc_closes = [c["close"] for c in btc_candles]
    btc_returns = [(btc_closes[i] - btc_closes[i - 1]) / btc_closes[i - 1] for i in range(1, len(btc_closes))]

    results = {}
    for pid in products:
        if pid == "BTC-USD":
            continue
        print(f"  Testing {pid} vs BTC lead-lag...")
        try:
            alt_candles = fetch_candles_72h(client, pid, "FIVE_MINUTE")
            if len(alt_candles) < 20:
                continue

            # Align by time (find matching bars)
            btc_times = {c["time"]: i for i, c in enumerate(btc_candles)}
            alt_closes = [c["close"] for c in alt_candles]

            signals = []
            for i in range(1, len(alt_candles)):
                alt_time = alt_candles[i]["time"]
                if alt_time not in btc_times:
                    continue

                btc_idx = btc_times[alt_time]
                if btc_idx < 2 or btc_idx >= len(btc_returns):
                    continue

                # BTC moved >0.3% in previous bar
                btc_move = abs(btc_returns[btc_idx - 1])
                if btc_move > 0.003:
                    # Alt's reaction in same bar and next 2 bars
                    alt_move_1 = (alt_closes[i] - alt_closes[i - 1]) / alt_closes[i - 1]
                    alt_move_2 = (alt_closes[min(i + 1, len(alt_closes) - 1)] - alt_closes[i]) / alt_closes[i] if i + 1 < len(alt_closes) else 0

                    # Check if alt overreacts (moves more than BTC)
                    overreaction = abs(alt_move_1) > btc_move * 1.5

                    signals.append({
                        "time": alt_time,
                        "btc_move_pct": round(btc_move * 100, 3),
                        "alt_move_1bar_pct": round(alt_move_1 * 100, 3),
                        "alt_move_2bar_pct": round(alt_move_2 * 100, 3),
                        "overreaction": overreaction,
                    })

            overreactions = [s for s in signals if s["overreaction"]]
            results[pid] = {
                "total_btc_moves": len(signals),
                "overreactions": len(overreactions),
                "overreaction_rate": round(len(overreactions) / len(signals), 3) if signals else 0,
                "avg_alt_move_1bar_pct": round(sum(s["alt_move_1bar_pct"] for s in signals) / len(signals), 4) if signals else 0,
                "avg_overreaction_pct": round(sum(s["alt_move_1bar_pct"] for s in overreactions) / len(overreactions), 4) if overreactions else 0,
            }
        except Exception as e:
            results[pid] = {"error": str(e)}

    return {
        "strategy": "cross_product_lead_lag",
        "btc_as_leader": "BTC-USD",
        "results": results,
    }


def main() -> None:
    client = CoinbaseAdvancedClient()
    all_results = []

    # Fetch candles once per product
    print("Fetching candles for all products...")
    candles_cache = {}
    for pid in PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid, "FIVE_MINUTE")
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    # Test 1: Candle sequence patterns
    print("\n=== Test 1: Candle Sequence Patterns ===")
    for pid in PRODUCTS:
        if pid not in candles_cache or len(candles_cache[pid]) < 20:
            continue
        result = test_candle_sequence_pattern(candles_cache[pid], pid, min_sequence=4)
        print(f"  {pid}: {result.get('total_signals', 0)} signals, {result.get('win_rate', 0):.1%} win, avg pnl {result.get('avg_pnl_pct', 0):+.4f}%")
        all_results.append(result)

    # Test 2: Multi-timeframe RSI divergence (requires both 5m and 15m)
    print("\n=== Test 2: Multi-Timeframe RSI Divergence ===")
    candles_15m_cache = {}
    for pid in PRODUCTS:
        try:
            candles_15m_cache[pid] = fetch_candles_72h(client, pid, "FIFTEEN_MINUTE")
        except Exception:
            pass

    for pid in PRODUCTS:
        if pid not in candles_cache or pid not in candles_15m_cache:
            continue
        result = test_multitimeframe_rsi_divergence(candles_cache[pid], candles_15m_cache[pid], pid)
        print(f"  {pid}: {result.get('total_signals', 0)} signals, {result.get('win_rate', 0):.1%} win, avg pnl {result.get('avg_pnl_pct', 0):+.4f}%")
        all_results.append(result)

    # Test 3: Time of day
    print("\n=== Test 3: Time-of-Day Analysis ===")
    for pid in PRODUCTS:
        if pid not in candles_cache:
            continue
        result = test_time_of_day(candles_cache[pid], pid)
        print(f"  {pid}: Most profitable hours: {result.get('most_profitable_hours', [])}")
        all_results.append(result)

    # Test 4: Volatility squeeze
    print("\n=== Test 4: Volatility Squeeze ===")
    for pid in PRODUCTS:
        if pid not in candles_cache:
            continue
        result = test_volatility_squeeze(candles_cache[pid], pid)
        print(f"  {pid}: {result.get('total_signals', 0)} squeezes, {result.get('win_rate', 0):.1%} win, avg move {result.get('avg_move_5bars_pct', 0):+.4f}%")
        all_results.append(result)

    # Test 5: Cross-product lead-lag
    print("\n=== Test 5: Cross-Product Lead-Lag (BTC → Alts) ===")
    result = test_cross_product_lead_lag(client, PRODUCTS)
    print(f"  Results:")
    for pid, r in result.get("results", {}).items():
        if "error" not in r:
            print(f"  {pid}: {r['overreactions']}/{r['total_btc_moves']} overreactions ({r['overreaction_rate']:.1%}), avg move {r['avg_overreaction_pct']:+.4f}%")
    all_results.append(result)

    # Write report
    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "products_tested": PRODUCTS,
        "results": all_results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
