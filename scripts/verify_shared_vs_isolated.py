#!/usr/bin/env python3
"""Verify shared vs isolated bankroll at different capital levels."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

COINS = ['RAVE-USD', 'NOM-USD', 'GHST-USD', 'TRU-USD', 'SUP-USD', 'A8-USD', 'BAL-USD', 'CFG-USD', 'IOTX-USD']

# Optimal params from sweep
CONFIGS = {
    'RAVE-USD': {'lookback': 15, 'tp_pct': 0.10, 'sl_pct': 0.00, 'max_hold': 36},
    'NOM-USD': {'lookback': 20, 'tp_pct': 0.10, 'sl_pct': 0.03, 'max_hold': 48},
    'GHST-USD': {'lookback': 50, 'tp_pct': 0.10, 'sl_pct': 0.03, 'max_hold': 96},
    'TRU-USD': {'lookback': 10, 'tp_pct': 0.10, 'sl_pct': 0.03, 'max_hold': 48},
    'SUP-USD': {'lookback': 10, 'tp_pct': 0.10, 'sl_pct': 0.03, 'max_hold': 48},
    'A8-USD': {'lookback': 10, 'tp_pct': 0.15, 'sl_pct': 0.00, 'max_hold': 48},
    'BAL-USD': {'lookback': 50, 'tp_pct': 0.10, 'sl_pct': 0.03, 'max_hold': 96},
    'CFG-USD': {'lookback': 50, 'tp_pct': 0.15, 'sl_pct': 0.00, 'max_hold': 48},
    'IOTX-USD': {'lookback': 10, 'tp_pct': 0.10, 'sl_pct': 0.03, 'max_hold': 48},
}

def backtest_momentum(candles, lookback, tp_pct, sl_pct, max_hold, fee_rate, starting_cash, seed):
    """Simple momentum backtest."""
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    history = []
    candle_hist = []

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        open_price = float(c["open"])

        history.append(close)
        candle_hist.append(c)
        if len(history) > 500:
            history = history[-500:]
            candle_hist = candle_hist[-500:]

        ts = int(c.get("start", 0))
        hour = __import__('datetime', fromlist=['datetime']).datetime.fromtimestamp(ts, tz=__import__('datetime', fromlist=['timezone']).timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}

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
        if pos is None and session_open and cash >= 2.0:
            if len(candle_hist) > lookback + 1:
                recent_high = max(float(c["high"]) for c in candle_hist[-(lookback+1):-1])
                if high > recent_high:
                    if rng.random() < 0.95:  # 95% fill rate
                        deploy = cash * 0.90
                        entry = open_price * 1.0008
                        entry_fee = deploy * fee_rate
                        units = (deploy - entry_fee) / entry
                        tp = entry * (1 + tp_pct)
                        sl = entry * (1 - sl_pct) if sl_pct > 0 else 0
                        cash -= deploy
                        pos = {"ep": entry, "q": deploy, "units": units, "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee, "max_hold": max_hold}

    if pos:
        cash += pos["q"]
    return cash - starting_cash, closes_count, wins, losses, max_dd

print("=" * 70)
print("SHARED vs ISOLATED BANKROLL VERIFICATION")
print("=" * 70)

# Fetch all candles
print("\nFetching 30d candles for 9 coins...", flush=True)
all_candles = {}
for coin in COINS:
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    all_candles[coin] = candles
    print(f"  {coin}: {len(candles)} candles", flush=True)

# Test at $48 bankroll
print(f"\n{'='*70}")
print(f"TEST 1: $48 total bankroll")
print(f"{'='*70}")

# Isolated: $48/9 = $5.33 per coin
total_isolated_48 = 0
for coin in COINS:
    cfg = CONFIGS[coin]
    pnl, trades, w, l, dd = backtest_momentum(
        all_candles[coin], cfg['lookback'], cfg['tp_pct'], cfg['sl_pct'], cfg['max_hold'],
        0.004, 48.0/9, 42
    )
    total_isolated_48 += pnl
    if trades > 0:
        print(f"  {coin}: ${pnl:+.2f} ({trades} trades, {w}W/{l}L, {w/max(trades,1)*100:.1f}% WR)")

print(f"  ISOLATED TOTAL (48): ${total_isolated_48:+.2f}")

# Shared: $48 shared, first-come-first-serve
# Simple approximation: sum of individual results scaled by capital ratio
# In shared mode, all coins compete for the same $48
total_shared_48 = 0
for coin in COINS:
    cfg = CONFIGS[coin]
    pnl, trades, w, l, dd = backtest_momentum(
        all_candles[coin], cfg['lookback'], cfg['tp_pct'], cfg['sl_pct'], cfg['max_hold'],
        0.004, 48.0, 42
    )
    total_shared_48 += pnl
print(f"  SHARED TOTAL (48, naive sum): ${total_shared_48:+.2f}")

# Test at $900 bankroll
print(f"\n{'='*70}")
print(f"TEST 2: $900 total bankroll")
print(f"{'='*70}")

# Isolated: $900/9 = $100 per coin
total_isolated_900 = 0
for coin in COINS:
    cfg = CONFIGS[coin]
    pnl, trades, w, l, dd = backtest_momentum(
        all_candles[coin], cfg['lookback'], cfg['tp_pct'], cfg['sl_pct'], cfg['max_hold'],
        0.004, 100.0, 42
    )
    total_isolated_900 += pnl
    if trades > 0:
        print(f"  {coin}: ${pnl:+.2f} ({trades} trades, {w}W/{l}L)")

print(f"  ISOLATED TOTAL (900): ${total_isolated_900:+.2f}")

# Shared: $900 shared
total_shared_900 = 0
for coin in COINS:
    cfg = CONFIGS[coin]
    pnl, trades, w, l, dd = backtest_momentum(
        all_candles[coin], cfg['lookback'], cfg['tp_pct'], cfg['sl_pct'], cfg['max_hold'],
        0.004, 900.0, 42
    )
    total_shared_900 += pnl

print(f"  SHARED TOTAL (900, naive sum): ${total_shared_900:+.2f}")

print(f"\n{'='*70}")
print(f"VERDICT:")
print(f"  At $48: Isolated=${total_isolated_48:+.2f}, Shared(naive)=${total_shared_48:+.2f}")
print(f"  At $900: Isolated=${total_isolated_900:+.2f}, Shared(naive)=${total_shared_900:+.2f}")
print(f"\nNote: Shared 'naive sum' is an UPPER BOUND. Real shared pool performs WORSE")
print(f"because coins compete for the same capital. The actual shared pool retention")
print(f"at $48 was 0.38% ($15.71 of $4,088 = 0.38%).")
