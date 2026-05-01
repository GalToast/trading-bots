#!/usr/bin/env python3
"""Test statistical strategies (robust_regression, spectral_analysis) on runner coins, 30d."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import backtest, momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

COINS = ['RAVE-USD', 'NOM-USD', 'GHST-USD', 'TRU-USD', 'SUP-USD']

def _robust_regression_entry(candles_hist, closes, candle, params):
    """Entry: buy when price is below robust regression line (mean reversion to trend)."""
    lookback = params.get("lookback", 20)
    dev_thresh = params.get("dev_thresh", 1.0)
    if len(closes) < lookback + 1:
        return False
    window = closes[-(lookback+1):-1]
    n = len(window)
    # Simple linear regression
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(window) / n
    num = sum((x[i] - x_mean) * (window[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return False
    slope = num / den
    intercept = y_mean - slope * x_mean
    # Predicted value for current bar
    predicted = intercept + slope * n
    current = closes[-1]
    # Buy if price is BELOW the regression line by dev_thresh%
    deviation = (current - predicted) / predicted * 100
    return deviation < -dev_thresh

def _spectral_entry(candles_hist, closes, candle, params):
    """Entry: buy at detected cycle low using simple spectral analysis."""
    lookback = params.get("lookback", 40)
    if len(closes) < lookback + 1:
        return False
    window = closes[-(lookback+1):-1]
    n = len(window)
    # Remove trend
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(window) / n
    num = sum((x[i] - x_mean) * (window[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0
    detrended = [window[i] - (y_mean + slope * x[i]) for i in range(n)]
    # Simple cycle detection: find if we're at a local minimum
    if n < 6:
        return False
    recent = detrended[-3:]
    if recent[1] < recent[0] and recent[1] < recent[2] and recent[1] < 0:
        return True
    return False

def run_strategy(candles, entry_fn, params, name):
    """Run a strategy and return results."""
    r = backtest(candles, entry_fn, params, fee_rate=0.004, starting_cash=100.0, seed=42)
    return {**r, "name": name, "params": params}

lines = []
lines.append("STATISTICAL STRATEGY 30D VALIDATION")
lines.append("=" * 70)

for coin in COINS:
    print(f"Fetching {coin}...", flush=True)
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    lines.append(f"\n{coin} ({len(candles)} candles):")

    # Robust Regression variants
    for lb in [20, 40, 60]:
        for dev in [0.5, 1.0, 2.0]:
            params = {"lookback": lb, "dev_thresh": dev, "tp_pct": 5.0, "sl_pct": 2.0, "max_hold": 24}
            r = run_strategy(candles, _robust_regression_entry, params, f"robust_reg_lb{lb}_dev{dev}")
            if r["net_pnl"] > 10 and r["win_rate"] >= 35:
                lines.append(f"  robust_reg lb={lb} dev={dev}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")

    # Spectral variants
    for lb in [20, 40, 60]:
        for tp in [5, 10, 15]:
            params = {"lookback": lb, "tp_pct": tp, "sl_pct": 3.0, "max_hold": 24}
            r = run_strategy(candles, _spectral_entry, params, f"spectral_lb{lb}_tp{tp}")
            if r["net_pnl"] > 10 and r["win_rate"] >= 35:
                lines.append(f"  spectral lb={lb} TP={tp}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")

    # Also run momentum as baseline
    for lb in [10, 20, 50]:
        r = momentum(candles, lookback=lb, tp_pct=10, sl_pct=3, max_hold=48,
                     fee_rate=0.004, starting_cash=100.0, seed=42)
        if r["net_pnl"] > 10:
            lines.append(f"  MOMENTUM lb={lb}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")

result = "\n".join(lines)
with open("reports/statistical_strategy_30d.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
