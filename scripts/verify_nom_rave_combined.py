#!/usr/bin/env python3
"""Independent verification of NOM+RAVE combined portfolio claim: $26,369/month."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

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

def momentum_signal(candles, idx, lookback):
    if idx < lookback:
        return False
    current_high = float(candles[idx]["high"])
    highest = max(float(candles[j]["high"]) for j in range(idx - lookback, idx))
    return current_high > highest

def rsi_mr_signal(candles, idx, rsi_period, os_thresh):
    if idx < rsi_period + 2:
        return False
    closes = [float(candles[j]["close"]) for j in range(idx + 1)]
    return compute_rsi(closes[:-1], rsi_period) <= os_thresh

def backtest_combined(candles_a, candles_b, config_a, config_b, starting_cash=48.0, seed=42):
    """Backtest two strategies sharing one bankroll."""
    rng = random.Random(seed)
    cash = starting_cash
    pos_a = None
    pos_b = None
    closes_a = 0
    closes_b = 0
    wins_a = 0
    wins_b = 0
    losses_a = 0
    losses_b = 0
    peak = starting_cash
    max_dd = 0.0
    signals_a = 0
    signals_b = 0
    
    n = min(len(candles_a), len(candles_b))
    
    for i in range(n):
        ca = candles_a[i]
        cb = candles_b[i]
        
        close_a = float(ca["close"])
        high_a = float(ca["high"])
        low_a = float(ca["low"])
        open_a = float(ca["open"])
        
        close_b = float(cb["close"])
        high_b = float(cb["high"])
        low_b = float(cb["low"])
        open_b = float(cb["open"])
        
        fee_rate = 0.004
        
        # EXIT A
        if pos_a:
            pos_a["hold"] += 1
            exit_price = None
            if high_a >= pos_a["tp"]:
                exit_price = pos_a["tp"]
            elif pos_a["sl"] > 0 and low_a <= pos_a["sl"]:
                exit_price = pos_a["sl"]
            elif pos_a["hold"] >= pos_a["max_hold"]:
                exit_price = close_a
            
            if exit_price is not None:
                units = pos_a["units"]
                gross = (exit_price - pos_a["ep"]) * units
                entry_fee = pos_a["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee
                cash += pos_a["q"] + net
                closes_a += 1
                if net > 0:
                    wins_a += 1
                else:
                    losses_a += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos_a = None
        
        # EXIT B
        if pos_b:
            pos_b["hold"] += 1
            exit_price = None
            if high_b >= pos_b["tp"]:
                exit_price = pos_b["tp"]
            elif pos_b["sl"] > 0 and low_b <= pos_b["sl"]:
                exit_price = pos_b["sl"]
            elif pos_b["hold"] >= pos_b["max_hold"]:
                exit_price = close_b
            
            if exit_price is not None:
                units = pos_b["units"]
                gross = (exit_price - pos_b["ep"]) * units
                entry_fee = pos_b["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee
                cash += pos_b["q"] + net
                closes_b += 1
                if net > 0:
                    wins_b += 1
                else:
                    losses_b += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos_b = None
        
        # ENTRY A
        if pos_a is None and cash >= 10.0:
            if config_a["type"] == "momentum":
                sig = momentum_signal(candles_a, i, config_a["lookback"])
            elif config_a["type"] == "rsi_mr":
                sig = rsi_mr_signal(candles_a, i, config_a["rsi_period"], config_a["os_thresh"])
            else:
                sig = False
            
            if sig:
                signals_a += 1
                if rng.random() < 0.95:  # fill probability
                    deploy = cash * 0.95
                    entry = open_a * 1.0008
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / entry
                    tp = entry * (1 + config_a["tp_pct"])
                    sl = entry * (1 - config_a["sl_pct"]) if config_a["sl_pct"] > 0 else 0
                    cash -= deploy
                    pos_a = {"ep": entry, "q": deploy, "units": units, "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee, "max_hold": config_a["max_hold"]}
        
        # ENTRY B
        if pos_b is None and cash >= 10.0:
            if config_b["type"] == "momentum":
                sig = momentum_signal(candles_b, i, config_b["lookback"])
            elif config_b["type"] == "rsi_mr":
                sig = rsi_mr_signal(candles_b, i, config_b["rsi_period"], config_b["os_thresh"])
            else:
                sig = False
            
            if sig:
                signals_b += 1
                if rng.random() < 0.95:
                    deploy = cash * 0.95
                    entry = open_b * 1.0008
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / entry
                    tp = entry * (1 + config_b["tp_pct"])
                    sl = entry * (1 - config_b["sl_pct"]) if config_b["sl_pct"] > 0 else 0
                    cash -= deploy
                    pos_b = {"ep": entry, "q": deploy, "units": units, "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee, "max_hold": config_b["max_hold"]}
    
    # Close remaining positions
    if pos_a:
        cash += pos_a["q"]
    if pos_b:
        cash += pos_b["q"]
    
    net = cash - starting_cash
    total_closes = closes_a + closes_b
    total_wins = wins_a + wins_b
    wr = total_wins / max(total_closes, 1) * 100
    
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": total_closes,
        "wins": total_wins,
        "win_rate": round(wr, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "signals_a": signals_a,
        "signals_b": signals_b,
        "closes_a": closes_a,
        "closes_b": closes_b,
        "wins_a": wins_a,
        "wins_b": wins_b,
    }

print("Fetching NOM and RAVE 30d candles...", flush=True)
rave = normalize_candles(fetch_candles_coinbase('RAVE-USD', 30))
nom = normalize_candles(fetch_candles_coinbase('NOM-USD', 30))
print(f"RAVE: {len(rave)} candles, NOM: {len(nom)} candles", flush=True)

# Test NOM momentum + RAVE momentum combined
rave_cfg = {"type": "momentum", "lookback": 20, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48}
nom_cfg = {"type": "momentum", "lookback": 20, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48}

r = backtest_combined(rave, nom, rave_cfg, nom_cfg, starting_cash=48.0, seed=42)
print(f"\nNOM momentum + RAVE momentum (shared $48):", flush=True)
print(f"  Net: ${r['net_pnl']:+.2f} WR={r['win_rate']}% Trades={r['trades']} DD={r['max_drawdown']}%", flush=True)
print(f"  RAVE: {r['closes_a']} closes, {r['wins_a']} wins | NOM: {r['closes_b']} closes, {r['wins_b']} wins", flush=True)

# Also test individual
rave_only = backtest_combined(rave, nom, rave_cfg, {"type": "none"}, starting_cash=48.0, seed=42)
nom_only = backtest_combined(rave, nom, {"type": "none"}, nom_cfg, starting_cash=48.0, seed=42)

print(f"\nRAVE momentum alone (shared $48): Net=${rave_only['net_pnl']:+.2f} WR={rave_only['win_rate']}% T={rave_only['trades']}", flush=True)
print(f"NOM momentum alone (shared $48): Net=${nom_only['net_pnl']:+.2f} WR={nom_only['win_rate']}% T={nom_only['trades']}", flush=True)

if r['net_pnl'] > rave_only['net_pnl'] and r['net_pnl'] > nom_only['net_pnl']:
    print(f"\n✅ SYNERGY: Combined (${r['net_pnl']:+.2f}) > best individual (${max(rave_only['net_pnl'], nom_only['net_pnl']):+.2f})", flush=True)
elif r['net_pnl'] < 0:
    print(f"\n❌ COMBINED LOSS: Shared bankroll destroyed to ${48 + r['net_pnl']:.2f}", flush=True)
else:
    print(f"\n⚠️ NO SYNERGY: Best individual (${max(rave_only['net_pnl'], nom_only['net_pnl']):+.2f}) >= combined (${r['net_pnl']:+.2f})", flush=True)
