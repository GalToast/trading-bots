#!/usr/bin/env python3
"""
Creative Frontier Push — Beating the Crown Jewel
=================================================
Testing 7 creative approaches beyond vanilla RSI(4):

1. Stochastic RSI (StochRSI) — RSI of RSI, ultra-sensitive
2. RSI + MFI confluence — Volume-weighted RSI
3. RSI Divergence Detection — Price lower low, RSI higher low
4. RSI + Bollinger %B combo — Double extreme signal
5. RSI Regime Filter — Hurst exponent mean-reversion detection
6. RSI + ATR Expansion — Only catch expanding volatility reversals
7. Multi-Timeframe RSI(4) — M5 + M15 both oversold

Target: Beat RSI(4)+25% TP on RAVE at +$79.45/72h
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_coinbase_spot_rsi import fetch_candles_72h, rsi as compute_rsi
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "creative_frontier_push.json"

# Crown jewel baseline
CROWN_JEWEL_NET = 79.45
CROWN_JEWEL_TRADES = 40
CROWN_JEWEL_WR = 55.0


def stoch_rsi(closes: list[float], rsi_period: int = 4, stoch_period: int = 3) -> list[float]:
    """Stochastic RSI — RSI of RSI. Ultra-sensitive to micro-reversals."""
    rsi_vals = compute_rsi(closes, rsi_period)
    result = [50.0] * len(closes)
    
    for i in range(rsi_period + stoch_period, len(rsi_vals)):
        window = rsi_vals[max(0, i - stoch_period + 1):i + 1]
        low = min(window)
        high = max(window)
        if high > low:
            result[i] = (rsi_vals[i] - low) / (high - low) * 100
        else:
            result[i] = 50.0
    return result


def mfi(candles: list[dict], period: int = 14) -> list[float]:
    """Money Flow Index — volume-weighted RSI."""
    result = [50.0] * len(candles)
    if len(candles) < period + 1:
        return result
    
    typical_prices = [(float(c["high"]) + float(c["low"]) + float(c["close"])) / 3 for c in candles]
    raw_money_flow = [tp * float(c["volume"]) for tp, c in zip(typical_prices, candles)]
    
    for i in range(period, len(candles)):
        positive_flow = 0.0
        negative_flow = 0.0
        for j in range(i - period + 1, i + 1):
            if typical_prices[j] > typical_prices[j - 1]:
                positive_flow += raw_money_flow[j]
            else:
                negative_flow += raw_money_flow[j]
        
        if negative_flow > 0:
            mf_ratio = positive_flow / negative_flow
            result[i] = 100 - 100 / (1 + mf_ratio)
        else:
            result[i] = 100.0
    return result


def bollinger_bands(closes: list[float], period: int = 20, num_std: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    """Returns (middle, upper, lower) bands."""
    middle = []
    upper = []
    lower = []
    
    for i in range(len(closes)):
        if i < period - 1:
            middle.append(closes[i])
            upper.append(closes[i])
            lower.append(closes[i])
        else:
            window = closes[i - period + 1:i + 1]
            sma = sum(window) / period
            variance = sum((x - sma) ** 2 for x in window) / period
            std = math.sqrt(variance)
            middle.append(sma)
            upper.append(sma + num_std * std)
            lower.append(sma - num_std * std)
    return middle, upper, lower


def bollinger_pct_b(close: float, upper: float, lower: float) -> float:
    """%B = (Price - Lower Band) / (Upper Band - Lower Band). %B < 0 means price below lower band."""
    if upper == lower:
        return 0.5
    return (close - lower) / (upper - lower)


def atr(candles: list[dict], period: int = 14) -> list[float]:
    """Average True Range."""
    result = [0.0] * len(candles)
    if len(candles) < 2:
        return result
    
    true_ranges = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    
    if len(true_ranges) >= period:
        result[period] = sum(true_ranges[:period]) / period
        for i in range(period + 1, len(candles)):
            result[i] = (result[i - 1] * (period - 1) + true_ranges[i - 1]) / period
    return result


def detect_rsi_divergence(closes: list[float], rsi_vals: list[float], lookback: int = 10) -> bool:
    """
    Bullish divergence: Price makes lower low, RSI makes higher low.
    Returns True if divergence detected in recent lookback bars.
    """
    if len(closes) < lookback * 2 or len(rsi_vals) < lookback * 2:
        return False
    
    # Find recent lows in price and RSI
    recent_prices = closes[-lookback:]
    recent_rsi = rsi_vals[-lookback:]
    
    price_low_idx = recent_prices.index(min(recent_prices))
    rsi_low_idx = recent_rsi.index(min(recent_rsi))
    
    # Look at earlier window for comparison
    earlier_prices = closes[-lookback * 2:-lookback]
    earlier_rsi = rsi_vals[-lookback * 2:-lookback]
    
    earlier_price_low = min(earlier_prices)
    earlier_rsi_low = min(earlier_rsi)
    
    current_price_low = min(recent_prices)
    current_rsi_low = min(recent_rsi)
    
    # Bullish divergence: price lower low, RSI higher low
    if current_price_low < earlier_price_low and current_rsi_low > earlier_rsi_low:
        return True
    return False


def hurst_exponent(closes: list[float], max_lag: int = 20) -> float:
    """
    Simplified Hurst exponent estimation.
    H < 0.5 = mean-reverting, H > 0.5 = trending, H = 0.5 = random walk.
    """
    if len(closes) < max_lag * 2:
        return 0.5
    
    lags = range(2, min(max_lag, len(closes) // 2))
    tau = []
    
    for lag in lags:
        differences = [abs(closes[i] - closes[i - lag]) for i in range(lag, len(closes))]
        std = (sum(d * d for d in differences) / len(differences)) ** 0.5
        if std > 0:
            tau.append(math.log(std))
        else:
            tau.append(0)
    
    if len(tau) < 3:
        return 0.5
    
    # Linear regression of log(std) vs log(lag)
    log_lags = [math.log(l) for l in lags[:len(tau)]]
    n = len(log_lags)
    sum_x = sum(log_lags)
    sum_y = sum(tau)
    sum_xy = sum(x * y for x, y in zip(log_lags, tau))
    sum_x2 = sum(x * x for x in log_lags)
    
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.5
    
    slope = (n * sum_xy - sum_x * sum_y) / denom
    hurst = slope
    return max(0.0, min(1.0, hurst))


def run_strategy(
    candles: list[dict],
    strategy_name: str,
    *,
    starting_cash: float = 48.0,
    maker_fee_bps: float = 5.0,
    product_id: str = "RAVE-USD",
    **kwargs
) -> dict[str, Any]:
    """Generic strategy runner with shared infrastructure."""
    if len(candles) < 30:
        return {"error": "not enough candles", "strategy": strategy_name}
    
    closes = [float(c["close"]) for c in candles]
    fee_rate = maker_fee_bps / 10000.0
    
    cash = starting_cash
    in_position = False
    position = None
    trades = []
    
    # Pre-compute indicators
    rsi_period = kwargs.get("rsi_period", 4)
    rsi_vals = compute_rsi(closes, rsi_period)
    
    # Strategy-specific precomputations
    stoch_rsi_vals = None
    mfi_vals = None
    bb_middle = bb_upper = bb_lower = None
    atr_vals = None
    
    if "stoch_rsi" in strategy_name.lower():
        stoch_rsi_vals = stoch_rsi(closes, rsi_period, kwargs.get("stoch_period", 3))
    if "mfi" in strategy_name.lower():
        mfi_vals = mfi(candles, kwargs.get("mfi_period", 14))
    if "bb" in strategy_name.lower() or "pct_b" in strategy_name.lower():
        bb_middle, bb_upper, bb_lower = bollinger_bands(closes, kwargs.get("bb_period", 20), kwargs.get("bb_mult", 2.0))
    if "atr" in strategy_name.lower():
        atr_vals = atr(candles, kwargs.get("atr_period", 14))
    
    tp_pct = kwargs.get("tp_pct", 0.25)
    sl_pct = kwargs.get("sl_pct", 0.03)
    max_hold = kwargs.get("max_hold", 24)
    os_thresh = kwargs.get("os_thresh", 30)
    ob_thresh = kwargs.get("ob_thresh", 80)
    deploy_pct = kwargs.get("deploy_pct", 0.95)
    
    for i in range(rsi_period + 5, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        
        # EXIT logic
        if in_position and position:
            tp_price = position["entry"] * (1 + tp_pct)
            sl_price = position["entry"] * (1 - sl_pct)
            
            exit_price = None
            exit_reason = None
            
            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif l <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
            elif current_rsi >= ob_thresh or (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "rsi_or_timeout"
            
            if exit_price:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - position["entry_fee"] - exit_fee
                
                cash += position["entry"] * qty + net
                trades.append({
                    "net": round(net, 4),
                    "exit_reason": exit_reason,
                    "hold_bars": i - position["bar"],
                    "win": net > 0
                })
                in_position = False
                position = None
                continue
        
        # ENTRY logic
        if not in_position and cash >= 10.0:
            entry_signal = False
            
            if strategy_name == "rsi4_baseline":
                # Vanilla RSI(4) baseline
                if current_rsi < os_thresh:
                    entry_signal = True
            
            elif strategy_name == "stoch_rsi":
                # Stochastic RSI — ultra-sensitive
                stoch_val = stoch_rsi_vals[i] if stoch_rsi_vals else 50
                if stoch_val < kwargs.get("stoch_os", 5):  # Extremely oversold
                    entry_signal = True
            
            elif strategy_name == "rsi_mfi_confluence":
                # RSI + MFI both oversold
                mfi_val = mfi_vals[i] if mfi_vals else 50
                if current_rsi < os_thresh and mfi_val < kwargs.get("mfi_os", 20):
                    entry_signal = True
            
            elif strategy_name == "rsi_divergence":
                # RSI divergence detection
                if detect_rsi_divergence(closes[:i+1], rsi_vals[:i+1], lookback=kwargs.get("div_lookback", 8)):
                    entry_signal = True
            
            elif strategy_name == "rsi_bb_pct_b":
                # RSI + Bollinger %B double extreme
                pct_b = bollinger_pct_b(cl, bb_upper[i], bb_lower[i]) if bb_upper else 0.5
                if current_rsi < os_thresh and pct_b < kwargs.get("pct_b_thresh", 0):
                    entry_signal = True
            
            elif strategy_name == "rsi_hurst_filter":
                # RSI with Hurst exponent regime filter
                if len(closes[:i+1]) >= 40:
                    h_exp = hurst_exponent(closes[:i+1], max_lag=15)
                    if current_rsi < os_thresh and h_exp < kwargs.get("hurst_thresh", 0.45):
                        entry_signal = True
            
            elif strategy_name == "rsi_atr_expansion":
                # RSI + ATR expansion
                current_atr = atr_vals[i] if atr_vals else 0
                avg_atr = sum(atr_vals[max(0, i-20):i]) / max(1, min(20, i)) if atr_vals else 0
                if current_rsi < os_thresh and current_atr > avg_atr * kwargs.get("atr_mult", 1.5):
                    entry_signal = True
            
            elif strategy_name == "rsi_multi_tf":
                # Multi-timeframe: needs M15 candles passed separately
                # Simplified: use 3x M5 as proxy for M15
                if i >= 3:
                    m15_closes = closes[max(0, i-3*3):i:3]  # Every 3rd M5 = M15 proxy
                    if len(m15_closes) >= 5:
                        m15_rsi = compute_rsi(m15_closes, rsi_period)[-1]
                        if current_rsi < os_thresh and m15_rsi < kwargs.get("m15_os", 40):
                            entry_signal = True
            
            if entry_signal:
                deploy_usd = cash * deploy_pct
                entry_fee = cl * (deploy_usd / cl) * fee_rate
                qty = (deploy_usd - entry_fee) / cl
                
                if qty > 0:
                    cash -= deploy_usd
                    position = {
                        "entry": cl,
                        "qty": qty,
                        "bar": i,
                        "entry_fee": entry_fee
                    }
                    in_position = True
    
    # Results
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    
    return {
        "strategy": strategy_name,
        "product_id": product_id,
        "realized_net_usd": round(sum(t["net"] for t in trades), 4),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(1, len(trades)), 3),
        "avg_net_per_trade": round(sum(t["net"] for t in trades) / max(1, len(trades)), 4),
        "beats_crown": sum(t["net"] for t in trades) > CROWN_JEWEL_NET,
        "improvement_pct": round((sum(t["net"] for t in trades) - CROWN_JEWEL_NET) / abs(CROWN_JEWEL_NET) * 100, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Creative Frontier Push — Beat the Crown Jewel")
    parser.add_argument("--product", default="RAVE-USD")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    args = parser.parse_args()
    
    client = CoinbaseAdvancedClient()
    print(f"Fetching 72h {args.granularity} candles for {args.product}...")
    candles = fetch_candles_72h(client, args.product, args.granularity)
    print(f"  Got {len(candles)} candles")
    
    strategies = [
        ("rsi4_baseline", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("stoch_rsi", {
            "rsi_period": 4, "stoch_period": 3, "stoch_os": 5,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("rsi_mfi_confluence", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "mfi_period": 14, "mfi_os": 20,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("rsi_divergence", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "div_lookback": 8,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("rsi_bb_pct_b", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "bb_period": 20, "bb_mult": 2.0, "pct_b_thresh": 0,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("rsi_hurst_filter", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "hurst_thresh": 0.45,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("rsi_atr_expansion", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "atr_period": 14, "atr_mult": 1.5,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
        ("rsi_multi_tf", {
            "rsi_period": 4, "os_thresh": 30, "ob_thresh": 80,
            "m15_os": 40,
            "tp_pct": 0.25, "sl_pct": 0.03, "max_hold": 24, "deploy_pct": 0.95
        }),
    ]
    
    results = []
    crown_jewel_baseline = None
    
    for name, params in strategies:
        print(f"\n{'='*70}")
        print(f"  {name.upper().replace('_', ' ')}")
        print(f"{'='*70}")
        
        result = run_strategy(
            candles, name,
            starting_cash=args.starting_cash,
            maker_fee_bps=args.maker_fee_bps,
            product_id=args.product,
            **params
        )
        
        results.append(result)
        
        if name == "rsi4_baseline":
            crown_jewel_baseline = result
        
        print(f"  Net: ${result.get('realized_net_usd', 0):+.2f}")
        print(f"  Trades: {result.get('total_trades', 0)}")
        print(f"  Win Rate: {result.get('win_rate', 0)*100:.1f}%")
        print(f"  Avg/Trade: ${result.get('avg_net_per_trade', 0):+.4f}")
        print(f"  Beats Crown Jewel: {'✅ YES' if result.get('beats_crown') else '❌ NO'}")
        print(f"  Improvement: {result.get('improvement_pct', 0):+.1f}%")
    
    # Sort by net PnL
    results.sort(key=lambda r: r.get("realized_net_usd", 0), reverse=True)
    
    # Summary
    print(f"\n{'='*100}")
    print(f"{'Strategy':<25} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'Avg/Tr':>9} {'Beats Crown':>11} {'Improve%':>9}")
    print(f"{'='*100}")
    for r in results:
        beats = "✅ YES" if r.get("beats_crown") else "❌"
        print(f"{r['strategy']:<25} ${r['realized_net_usd']:>6.2f} {r['total_trades']:>7} {r['win_rate']*100:>5.1f}% ${r['avg_net_per_trade']:>7.4f} {beats:>11} {r['improvement_pct']:>+8.1f}%")
    
    # Write report
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "crown_jewel_baseline": {"net": CROWN_JEWEL_NET, "trades": CROWN_JEWEL_TRADES, "wr": CROWN_JEWEL_WR},
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to: {out}")
    
    # Find the winner
    winner = results[0]
    if winner["beats_crown"]:
        print(f"\n🚨🚨🚨 NEW CROWN JEWEL: {winner['strategy']} at ${winner['realized_net_usd']:.2f} ({winner['improvement_pct']:+.1f}% improvement)!")
    else:
        print(f"\n👑 Crown Jewel still stands: RSI(4)+25% at ${CROWN_JEWEL_NET:.2f}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
