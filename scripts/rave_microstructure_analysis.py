#!/usr/bin/env python3
"""
Lane 1: RAVE Microstructure Cross-Reference

Why does RSI(3) < 30 mean-reversion work on RAVE but lead-lag doesn't?

This script analyzes:
1. RAVE's RSI oversold frequency vs other alts
2. Bounce magnitude distribution after RSI < 30
3. RAVE's volume/liquidity profile
4. Whether RAVE's behavior is unique vs other microcaps
5. Session-based patterns (when does RAVE mean-revert best?)
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

COINS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "BTC-USD", "ETH-USD"]
FEE_RATE = 0.0040


def compute_rsi(closes, period=3):
    """Compute RSI for a list of closes."""
    if len(closes) < period + 1:
        return []

    rsi_values = []
    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(0, change)
        loss = max(0, -change)

        gains.append(gain)
        losses.append(loss)

        if len(gains) < period:
            rsi_values.append(None)
            continue

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            rsi_values.append(rsi)

    return rsi_values


def analyze_rsi_oversold(candles, coin_name, rsi_period=3, oversold_threshold=30):
    """Analyze RSI oversold events and subsequent bounce behavior."""
    closes = [float(c["close"]) for c in candles]
    rsi = compute_rsi(closes, rsi_period)

    # Find oversold events
    oversold_events = []
    for i in range(len(rsi)):
        if rsi[i] is not None and rsi[i] < oversold_threshold:
            # Don't count consecutive oversold bars as separate events
            if not oversold_events or i - oversold_events[-1]["bar"] > 3:
                oversold_events.append({"bar": i, "rsi": rsi[i], "price": closes[i]})

    # For each oversold event, measure the bounce
    bounce_results = []
    for event in oversold_events:
        entry_bar = event["bar"] + 1  # Enter next bar
        if entry_bar >= len(closes) - 5:
            continue

        entry_price = closes[entry_bar - 1] if entry_bar > 0 else closes[entry_bar]
        best_exit = 0
        worst_exit = 0
        exit_bar = None

        for b in range(1, 6):
            idx = entry_bar + b
            if idx >= len(closes):
                break
            exit_price = closes[idx]
            ret = (exit_price - entry_price) / entry_price * 100
            if ret > best_exit:
                best_exit = ret
            if ret < worst_exit:
                worst_exit = ret

        # 5-bar hold return
        hold_bars = min(5, len(closes) - entry_bar - 1)
        if hold_bars > 0:
            final_ret = (closes[entry_bar + hold_bars] - entry_price) / entry_price * 100
        else:
            final_ret = 0

        bounce_results.append({
            "entry_rsi": round(event["rsi"], 1),
            "entry_price": round(entry_price, 6),
            "best_bounce_pct": round(best_exit, 3),
            "worst_drawdown_pct": round(worst_exit, 3),
            "hold_5bar_return_pct": round(final_ret, 3),
        })

    if not bounce_results:
        return {"coin": coin_name, "oversold_events": 0, "total_bars": len(closes)}

    wins = [r for r in bounce_results if r["hold_5bar_return_pct"] > 0]
    losses = [r for r in bounce_results if r["hold_5bar_return_pct"] <= 0]

    return {
        "coin": coin_name,
        "oversold_events": len(bounce_results),
        "total_bars": len(closes),
        "oversold_frequency_pct": round(len(bounce_results) / max(1, len(closes)) * 100, 2),
        "win_rate_pct": round(len(wins) / max(1, len(bounce_results)) * 100, 1),
        "avg_best_bounce_pct": round(statistics.mean([r["best_bounce_pct"] for r in bounce_results]), 3),
        "avg_worst_dd_pct": round(statistics.mean([r["worst_drawdown_pct"] for r in bounce_results]), 3),
        "avg_hold_return_pct": round(statistics.mean([r["hold_5bar_return_pct"] for r in bounce_results]), 3),
        "avg_win_return_pct": round(statistics.mean([r["hold_5bar_return_pct"] for r in wins]), 3) if wins else 0,
        "avg_loss_return_pct": round(statistics.mean([r["hold_5bar_return_pct"] for r in losses]), 3) if losses else 0,
        "median_best_bounce_pct": round(statistics.median([r["best_bounce_pct"] for r in bounce_results]), 3),
    }


def analyze_volume_liquidity(candles, coin_name):
    """Analyze volume and liquidity characteristics."""
    volumes = [float(c.get("volume", 0)) for c in candles]
    closes = [float(c["close"]) for c in candles]

    if not volumes or not closes:
        return {"coin": coin_name, "data_available": False}

    # Dollar volume
    dollar_volumes = [v * c for v, c in zip(volumes, closes)]

    # Volatility
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, len(closes))]

    return {
        "coin": coin_name,
        "data_available": True,
        "total_bars": len(candles),
        "avg_dollar_volume": round(statistics.mean(dollar_volumes), 2) if dollar_volumes else 0,
        "median_dollar_volume": round(statistics.median(dollar_volumes), 2) if dollar_volumes else 0,
        "max_dollar_volume": round(max(dollar_volumes), 2) if dollar_volumes else 0,
        "avg_return_pct": round(statistics.mean(returns), 4) if returns else 0,
        "volatility_pct": round(statistics.stdev(returns), 4) if len(returns) > 1 else 0,
        "max_up_move_pct": round(max(returns), 3) if returns else 0,
        "max_down_move_pct": round(min(returns), 3) if returns else 0,
    }


def main():
    print("=" * 80)
    print("  LANE 1: RAVE Microstructure Cross-Reference")
    print("=" * 80)

    # Load candles
    candles_data = {}
    for coin in COINS:
        candles = load_candles(coin, "ONE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
        if candles:
            candles_data[coin] = candles

    # Part 1: RSI oversold analysis
    print(f"\n{'='*70}")
    print(f"  RSI(3) Oversold Analysis (< 30 threshold)")
    print(f"{'='*70}")

    rsi_results = []
    for coin in COINS:
        if coin not in candles_data:
            print(f"  {coin}: NO DATA")
            continue
        result = analyze_rsi_oversold(candles_data[coin], coin)
        rsi_results.append(result)
        print(f"\n  {coin}:")
        print(f"    Oversold events: {result.get('oversold_events', 'N/A')}")
        if result.get('oversold_frequency_pct'):
            print(f"    Oversold frequency: {result['oversold_frequency_pct']}% of bars")
            print(f"    Win rate (5-bar hold): {result.get('win_rate_pct', 'N/A')}%")
            print(f"    Avg return: {result.get('avg_hold_return_pct', 'N/A')}%")
            print(f"    Avg best bounce: {result.get('avg_best_bounce_pct', 'N/A')}%")
            print(f"    Avg worst DD: {result.get('avg_worst_dd_pct', 'N/A')}%")

    # Part 2: Volume/Liquidity analysis
    print(f"\n{'='*70}")
    print(f"  Volume/Liquidity Profile")
    print(f"{'='*70}")

    vol_results = []
    for coin in COINS:
        if coin not in candles_data:
            continue
        result = analyze_volume_liquidity(candles_data[coin], coin)
        vol_results.append(result)
        print(f"\n  {coin}:")
        print(f"    Bars: {result['total_bars']}")
        print(f"    Avg $vol: ${result['avg_dollar_volume']:,.2f}")
        print(f"    Median $vol: ${result['median_dollar_volume']:,.2f}")
        print(f"    Volatility: {result['volatility_pct']:.4f}%/bar")
        print(f"    Max up: {result['max_up_move_pct']:+.3f}% | Max down: {result['max_down_move_pct']:+.3f}%")

    # Part 3: What makes RAVE special?
    print(f"\n{'='*70}")
    print(f"  RAVE vs Other Microcaps — What's Different?")
    print(f"{'='*70}")

    rave_rsi = next((r for r in rsi_results if r["coin"] == "RAVE-USD"), None)
    other_rs = [r for r in rsi_results if r["coin"] != "RAVE-USD" and r.get("oversold_events", 0) > 0]

    if rave_rsi and other_rs:
        print(f"\n  RAVE oversold frequency: {rave_rsi.get('oversold_frequency_pct', 'N/A')}%")
        avg_other_freq = statistics.mean([r.get('oversold_frequency_pct', 0) for r in other_rs])
        print(f"  Other alts avg oversold freq: {avg_other_freq:.2f}%")
        print(f"  → RAVE is {'MORE' if rave_rsi.get('oversold_frequency_pct', 0) > avg_other_freq else 'LESS'} prone to oversold")

        print(f"\n  RAVE win rate: {rave_rsi.get('win_rate_pct', 'N/A')}%")
        avg_other_wr = statistics.mean([r.get('win_rate_pct', 0) for r in other_rs if r.get('win_rate_pct')])
        print(f"  Other alts avg win rate: {avg_other_wr:.1f}%")

        print(f"\n  RAVE avg return: {rave_rsi.get('avg_hold_return_pct', 'N/A')}%")
        avg_other_ret = statistics.mean([r.get('avg_hold_return_pct', 0) for r in other_rs if r.get('avg_hold_return_pct')])
        print(f"  Other alts avg return: {avg_other_ret:.3f}%")

        # Fee impact
        rave_ret = rave_rsi.get('avg_hold_return_pct', 0) or 0
        fees_for_round_trip = FEE_RATE * 2 * 100  # 0.8% for 40bps
        print(f"\n  Fee impact at 40bps: {fees_for_round_trip:.2f}% per round trip")
        print(f"  RAVE avg return ({rave_ret:.3f}%) vs fees ({fees_for_round_trip:.2f}%): ", end="")
        if rave_ret > fees_for_round_trip:
            print("✅ RAVE edge exceeds fees")
        else:
            print("❌ RAVE edge does NOT exceed fees at M1 granularity")

    # Save report
    report = {
        "rsi_oversold": rsi_results,
        "volume_liquidity": vol_results,
        "analysis": "RAVE microstructure cross-reference",
    }
    output_path = REPORT_DIR / "rave_microstructure_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
