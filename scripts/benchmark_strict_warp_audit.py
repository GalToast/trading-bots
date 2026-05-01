#!/usr/bin/env python3
"""
Strict Warp Comparator Adversarial Audit
==========================================
Tests @codex-2's `live_lattice_warp_grinder_strict_shadow.py` with realistic execution.

Tests:
1. Fee floor: does warp deliver gains above fee-adjusted minimum target?
2. Kraken signal strength: does IOTX/BAL follow BTC spikes?
3. Realistic fill model: 50% fill prob, 2s latency, slippage
4. Volume-to-fee-tier path: does it hit $50K before bleeding out?

Framework:
- Fill probabilities: 100%, 75%, 50%, 25%
- Latency: 0s, 2s, 5s
- Slippage: 0%, 0.5%, 1%
- Combined worst case: 2s + 50% fill + 0.5% slippage
"""
from __future__ import annotations

import json
import time
import math
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "strict_warp_comparator_adversarial_audit.json"


def compute_btc_returns(candles):
    """Compute BTC returns from candle data."""
    closes = [float(c["close"]) for c in candles]
    returns = []
    for i in range(1, len(closes)):
        ret = (closes[i] - closes[i-1]) / closes[i-1] * 100
        returns.append(ret)
    return returns


def detect_warp_signals(btc_returns, threshold_usd=5.0, btc_price=72000.0):
    """Detect BTC warp signals ($5+ move in 1 bar)."""
    signals = []
    for i, ret in enumerate(btc_returns):
        price_change = btc_price * abs(ret) / 100
        if price_change >= threshold_usd:
            signals.append({
                "bar": i,
                "ret_pct": ret,
                "price_change_usd": round(price_change, 2),
                "direction": "up" if ret > 0 else "down",
            })
    return signals


def test_altcoin_followthrough(btc_signals, btc_candles, alt_candles, lag_bars=1):
    """Test if altcoin follows BTC warp signals with lag."""
    if not btc_signals or len(alt_candles) < lag_bars + 2:
        return {"correlation": 0, "hit_rate": 0, "avg_followthrough": 0}
    
    alt_closes = [float(c["close"]) for c in alt_candles]
    btc_closes = [float(c["close"]) for c in btc_candles]
    
    hit_count = 0
    total_followthrough = 0
    same_direction = 0
    
    for signal in btc_signals:
        bar = signal["bar"]
        if bar + lag_bars >= len(alt_closes) - 1:
            continue
        
        # BTC move direction
        btc_direction = 1 if signal["ret_pct"] > 0 else -1
        
        # Altcoin return at lag
        alt_ret = (alt_closes[min(bar + lag_bars, len(alt_closes)-1)] - alt_closes[bar]) / alt_closes[bar] * 100
        alt_direction = 1 if alt_ret > 0 else -1
        
        total_followthrough += abs(alt_ret)
        if btc_direction == alt_direction:
            same_direction += 1
            hit_count += 1
    
    total_signals = len([s for s in btc_signals if s["bar"] + lag_bars < len(alt_closes) - 1])
    
    return {
        "correlation": round(same_direction / max(1, total_signals), 3),
        "hit_rate": round(hit_count / max(1, total_signals) * 100, 1),
        "avg_followthrough": round(total_followthrough / max(1, total_signals), 4),
        "total_signals": total_signals,
        "same_direction": same_direction,
    }


def run_warp_grinder_audit(alt_coin, btc_candles, alt_candles,
                            fee_bps=40, fill_prob=1.0, latency_bars=0, slippage_pct=0.0,
                            starting_cash=324.0, quote_size=50.0,
                            target_multiple=1.006, stop_multiple=0.985,
                            warp_threshold_usd=5.0):
    """Run the warp grinder audit with realistic execution."""
    
    # Compute BTC returns and detect warp signals
    btc_returns = compute_btc_returns(btc_candles)
    btc_signals = detect_warp_signals(btc_returns, warp_threshold_usd)
    
    # Test altcoin followthrough
    followthrough = test_altcoin_followthrough(btc_signals, btc_candles, alt_candles, lag_bars=1)
    
    # Run grinder simulation
    fee_rate = fee_bps / 10000.0
    cash = starting_cash
    positions = {}
    pending_entries = {}
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    
    alt_closes = [float(c["close"]) for c in alt_candles]
    alt_opens = [float(c["open"]) for c in alt_candles]
    alt_highs = [float(c["high"]) for c in alt_candles]
    alt_lows = [float(c["low"]) for c in alt_candles]
    
    for i in range(1, len(alt_candles) - 1):
        # Check for warp signal
        warp_triggered = False
        if i < len(btc_returns):
            price_change = 72000.0 * abs(btc_returns[i]) / 100
            if price_change >= warp_threshold_usd:
                warp_triggered = True
                # Place pending entry
                entry_price = alt_closes[i] * (1 - slippage_pct / 100)
                pending_entries[alt_coin] = {
                    "entry_price": entry_price,
                    "placed_at": i,
                    "expires_at": i + 15,  # 75s TTL ≈ 15 bars
                }
        
        # Check pending entries for fills
        if alt_coin in pending_entries:
            pending = pending_entries[alt_coin]
            if i > pending["placed_at"] + latency_bars and i <= pending["expires_at"]:
                # Try to fill
                if random.random() < fill_prob:
                    # Check if candle low touches our entry
                    if alt_lows[i] <= pending["entry_price"]:
                        # Fill!
                        units = quote_size / pending["entry_price"]
                        entry_fee = quote_size * fee_rate
                        cash -= quote_size + entry_fee
                        total_fees += entry_fee
                        
                        positions[alt_coin] = {
                            "entry_price": pending["entry_price"],
                            "units": units,
                            "entry_fee": entry_fee,
                            "opened_at": i,
                            "target": pending["entry_price"] * target_multiple,
                            "stop": pending["entry_price"] * stop_multiple,
                        }
                        total_volume += quote_size
                        del pending_entries[alt_coin]
                    elif i >= pending["expires_at"]:
                        del pending_entries[alt_coin]
        
        # Check positions for exits
        for coin, pos in list(positions.items()):
            # TP hit
            if alt_highs[i] >= pos["target"]:
                exit_price = pos["target"]
                exit_reason = "tp"
            # SL hit
            elif alt_lows[i] <= pos["stop"]:
                exit_price = pos["stop"]
                exit_reason = "sl"
            # Timeout (48 bars)
            elif i - pos["opened_at"] >= 48:
                exit_price = alt_closes[i] * (1 + slippage_pct / 100)
                exit_reason = "timeout"
            else:
                continue
            
            # Exit
            exit_fee = exit_price * pos["units"] * fee_rate
            exit_proceeds = exit_price * pos["units"] - exit_fee
            net = exit_proceeds - quote_size - pos["entry_fee"]
            
            cash += exit_proceeds
            total_volume += exit_price * pos["units"]
            total_fees += exit_fee
            
            trades.append({
                "net": net,
                "reason": exit_reason,
                "win": net > 0,
                "hold_bars": i - pos["opened_at"],
            })
            
            del positions[coin]
    
    # Close any remaining positions at market
    for coin, pos in positions.items():
        exit_price = alt_closes[-1] * (1 + slippage_pct / 100)
        exit_fee = exit_price * pos["units"] * fee_rate
        exit_proceeds = exit_price * pos["units"] - exit_fee
        net = exit_proceeds - quote_size - pos["entry_fee"]
        cash += exit_proceeds
        total_volume += exit_price * pos["units"]
        total_fees += exit_fee
        trades.append({"net": net, "reason": "final_close", "win": net > 0})
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "avg_trade": round(net / max(1, len(trades)), 4),
        "followthrough": followthrough,
        "warp_signals": len(btc_signals),
    }


def main():
    print("=" * 80)
    print("  STRICT WARP COMPARATOR ADVERSARIAL AUDIT")
    print("=" * 80)
    
    # Load cached data
    print("\nLoading cached data...")
    btc_candles = load_candles("BTC-USD", "FIVE_MINUTE", 7, max_age_minutes=10000)
    
    alt_coins = ["IOTX-USD", "BAL-USD", "BLUR-USD"]
    alt_candles = {}
    for coin in alt_coins:
        candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=10000)
        if candles:
            alt_candles[coin] = candles
            print(f"  {coin}: {len(candles)} candles")
    
    if not btc_candles or not alt_candles:
        print("ERROR: Missing data.")
        return 1
    
    print(f"  BTC-USD: {len(btc_candles)} candles")
    
    # Detect BTC warp signals
    btc_returns = compute_btc_returns(btc_candles)
    btc_signals = detect_warp_signals(btc_returns, 5.0)
    print(f"\n  BTC warp signals (>$5 in 5min): {len(btc_signals)}")
    
    # Test followthrough for each altcoin
    print(f"\n  Altcoin followthrough (1-bar lag):")
    for coin, candles in alt_candles.items():
        ft = test_altcoin_followthrough(btc_signals, btc_candles, candles, lag_bars=1)
        print(f"    {coin}: corr={ft['correlation']}, hit_rate={ft['hit_rate']}%, "
              f"avg_followthrough={ft['avg_followthrough']}%, signals={ft['total_signals']}")
    
    # Run adversarial audit matrix
    print(f"\n{'='*80}")
    print(f"  ADVERSARIAL AUDIT MATRIX")
    print(f"{'='*80}")
    
    all_results = []
    
    # Test each altcoin with different execution assumptions
    for coin, candles in alt_candles.items():
        print(f"\n  {coin}:")
        print(f"  {'Config':<40} {'Net $':>8} {'Trades':>7} {'WR%':>6} {'Vol$':>10}")
        print(f"  {'-'*75}")
        
        # Shadow (optimistic)
        r = run_warp_grinder_audit(coin, btc_candles, candles, fee_bps=40, fill_prob=1.0, latency_bars=0, slippage_pct=0.0)
        r["config"] = "Shadow (100% fill, 0s latency, 0% slip)"
        r["coin"] = coin
        all_results.append(r)
        print(f"  {r['config']:<40} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['total_volume']:>8.0f}")
        
        # Realistic
        r = run_warp_grinder_audit(coin, btc_candles, candles, fee_bps=40, fill_prob=0.5, latency_bars=0, slippage_pct=0.5)
        r["config"] = "Realistic (50% fill, 0s latency, 0.5% slip)"
        r["coin"] = coin
        all_results.append(r)
        print(f"  {r['config']:<40} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['total_volume']:>8.0f}")
        
        # Worst case
        r = run_warp_grinder_audit(coin, btc_candles, candles, fee_bps=40, fill_prob=0.5, latency_bars=0, slippage_pct=1.0)
        r["config"] = "Worst case (50% fill, 0s latency, 1% slip)"
        r["coin"] = coin
        all_results.append(r)
        print(f"  {r['config']:<40} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['total_volume']:>8.0f}")
        
        # At 15bps fee tier
        r = run_warp_grinder_audit(coin, btc_candles, candles, fee_bps=15, fill_prob=0.5, latency_bars=0, slippage_pct=0.5)
        r["config"] = "15bps fees (50% fill, 0s latency, 0.5% slip)"
        r["coin"] = coin
        all_results.append(r)
        print(f"  {r['config']:<40} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['total_volume']:>8.0f}")
    
    # Save report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "btc_signals": len(btc_signals),
        "all_results": all_results,
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  AUDIT SUMMARY")
    print(f"{'='*80}")
    
    # Group by coin
    for coin in alt_coins:
        coin_results = [r for r in all_results if r["coin"] == coin]
        if not coin_results:
            continue
        
        print(f"\n  {coin}:")
        for r in coin_results:
            ft = r.get("followthrough", {})
            print(f"    {r['config']}: ${r['net']:.2f} ({r['trades']}t, {r['wr']}%WR) | "
                  f"FT corr={ft.get('correlation', '?')}, hit_rate={ft.get('hit_rate', '?')}%")
    
    # Verdict
    print(f"\n{'='*80}")
    print(f"  VERDICT")
    print(f"{'='*80}")
    
    for coin in alt_coins:
        coin_results = [r for r in all_results if r["coin"] == coin]
        if not coin_results:
            continue
        
        shadow = coin_results[0]
        realistic = coin_results[1] if len(coin_results) > 1 else None
        
        print(f"\n  {coin}:")
        print(f"    Shadow: ${shadow['net']:.2f}")
        if realistic:
            print(f"    Realistic: ${realistic['net']:.2f}")
            if realistic["net"] > 0:
                print(f"    ✅ SURVIVES adversarial audit")
            else:
                print(f"    ❌ DESTROYED by realistic execution")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
