#!/usr/bin/env python3
"""Test robust_regression + momentum overlap on runner coins."""
import sys, os, random, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

COINS = ['RAVE-USD', 'NOM-USD', 'GHST-USD', 'TRU-USD', 'SUP-USD']

def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0

def momentum_signal(closes, candle_history, idx, lookback):
    """Buy when price breaks above N-bar high."""
    if idx < lookback:
        return False
    high = float(candle_history[idx]["high"])
    highest = max(float(candle_history[j]["high"]) for j in range(idx - lookback, idx))
    return high > highest

def robust_regression_signal(closes, candle_history, idx, lookback=20, dev_thresh=1.0):
    """Buy when price is below robust regression line by dev_thresh%."""
    if len(closes) < lookback + 1:
        return False
    window = closes[-(lookback+1):-1]
    n = len(window)
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(window) / n
    num = sum((x[i] - x_mean) * (window[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return False
    slope = num / den
    intercept = y_mean - slope * x_mean
    predicted = intercept + slope * n
    current = closes[-1]
    deviation = (current - predicted) / predicted * 100
    return deviation < -dev_thresh

def backtest_with_signals(candles, signal_fn, params, fee_rate=0.004, starting_cash=100.0, seed=42):
    """Generic backtest that accepts any signal function."""
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    closes_history = []
    candle_hist = []
    signals = 0
    signal_bars = []  # Track which bars fired

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        closes_history.append(close)
        candle_hist.append(c)

        ts = int(c.get("start", 0))
        hour = __import__('datetime', fromlist=['datetime']).datetime.fromtimestamp(ts, tz=__import__('datetime', fromlist=['timezone']).timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}

        # Use local history for signals (don't trim until after signal check)

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            if high >= pos["tp"]:
                exit_price = pos["tp"]
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close

            if exit_price is not None:
                units = pos["units"]
                gross = (exit_price - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee
                cash += pos["q"] + net
                closes_count += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY
        if pos is None and session_open and cash >= 10.0:
            current_idx = len(candle_hist) - 1  # Use relative index since history may be trimmed
            sig = signal_fn(closes_history, candle_hist, current_idx, **params.get("signal_params", {}))
            if sig:
                signals += 1
                signal_bars.append(i)
                if rng.random() > 0.95:  # 5% miss rate
                    continue
                deploy = cash * 0.95
                entry = candle_open * 1.0008
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / entry
                tp = entry * (1 + params.get("tp_pct", 0.10))
                sl = entry * (1 - params.get("sl_pct", 0.03)) if params.get("sl_pct", 0.03) > 0 else 0
                cash -= deploy
                pos = {"ep": entry, "q": deploy, "units": units, "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee, "max_hold": params.get("max_hold", 48)}

        # Trim history to prevent unbounded growth
        if len(closes_history) > 500:
            closes_history = closes_history[-500:]
            candle_hist = candle_hist[-500:]

    if pos:
        cash += pos["q"]
    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100
    return {"net_pnl": round(net, 2), "win_rate": round(wr, 1), "trades": closes_count, "max_drawdown": round(max_dd * 100, 1), "signals": signals, "signal_bars": set(signal_bars)}

print("=" * 70)
print("ROBUST REGRESSION vs MOMENTUM OVERLAP ANALYSIS")
print("=" * 70)

for coin in COINS:
    print(f"\nFetching {coin}...", flush=True)
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    print(f"  {len(candles)} candles", flush=True)

    # Momentum
    mom = backtest_with_signals(candles, momentum_signal, {"signal_params": {"lookback": 20}, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48})
    
    # Robust Regression
    rr = backtest_with_signals(candles, robust_regression_signal, {"signal_params": {"lookback": 20, "dev_thresh": 1.0}, "tp_pct": 0.05, "sl_pct": 0.02, "max_hold": 24})

    # Overlap
    mom_bars = mom["signal_bars"]
    rr_bars = rr["signal_bars"]
    overlap = mom_bars & rr_bars
    unique_rr = rr_bars - mom_bars
    unique_mom = mom_bars - rr_bars

    print(f"  Momentum:     {mom['signals']} signals, {mom['trades']} trades, ${mom['net_pnl']:+.2f}, WR={mom['win_rate']}%")
    print(f"  Robust Reg:   {rr['signals']} signals, {rr['trades']} trades, ${rr['net_pnl']:+.2f}, WR={rr['win_rate']}%")
    print(f"  Overlap:      {len(overlap)}/{len(rr_bars)} ({len(overlap)/max(len(rr_bars),1)*100:.1f}% of RR signals also fire on momentum)")
    print(f"  Unique RR:    {len(unique_rr)} signals ({len(unique_rr)/max(len(rr_bars),1)*100:.1f}% of RR is unique)")
    print(f"  Unique Mom:   {len(unique_mom)} signals ({len(unique_mom)/max(len(mom_bars),1)*100:.1f}% of Mom is unique)")
