#!/usr/bin/env python3
"""
Independent verification of NOM+RAVE synergy claim ($26,369/mo, 16.4% DD).

@qwen-trading claims this from scripts/nom_combined_test.py.
Testing through ground-truth engine with shared bankroll simulation.
"""
import json, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import backtest, momentum

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days="30d"):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}.json")
    if not os.path.exists(path): return []
    with open(path) as f: data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

# Load candles
rave = load("RAVE-USD")
nom = load("NOM-USD")
print(f"RAVE: {len(rave)} candles")
print(f"NOM: {len(nom)} candles")

# 1. Individual backtests first
print("\n" + "="*70)
print("INDIVIDUAL BACKTESTS (shared bankroll baseline)")
print("="*70)

rave_r = momentum(rave, lookback=15, tp_pct=10, sl_pct=0, max_hold=48,
                  starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
print(f"RAVE momentum (lb=15, tp=10%, sl=0%): PnL=${rave_r['net_pnl']:.2f} WR={rave_r['win_rate']}% Trades={rave_r['trades']}")

# NOM needs range_breakout — let's check if we have it
from strategy_library import range_breakout

nom_rb = range_breakout(nom, range_lookback=10, tp_pct=10, sl_pct=1, max_hold=24,
                        starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
print(f"NOM range_breakout (lb=10, tp=10%, sl=1%): PnL=${nom_rb['net_pnl']:.2f} WR={nom_rb['win_rate']}% Trades={nom_rb['trades']}")

# 2. Shared bankroll simulation
# We need to interleave signals from both strategies on a shared pool
# This requires custom simulation since the backtest engine is per-coin only

print("\n" + "="*70)
print("SHARED BANKROLL SIMULATION — RAVE + NOM")
print("="*70)

# For a proper shared bankroll test, we need to:
# 1. Get signal times from both strategies
# 2. On each signal, deploy if cash available
# 3. Track exits and bankroll compounding
# 4. This is fundamentally different from individual backtests

# Simplified approach: alternate between strategies on each cycle
# (This approximates shared bankroll without full signal interleaving)

# Build a unified simulation
def shared_bankroll_sim(rave_candles, nom_candles, starting_cash=48.0):
    """Simulate shared bankroll with RAVE momentum + NOM range_breakout."""
    from strategy_library import compute_rsi, compute_bb
    from datetime import datetime, timezone
    
    cash = starting_cash
    rave_pos = None
    nom_pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    
    # Use the shorter candle set
    min_len = min(len(rave_candles), len(nom_candles))
    rave_c = rave_candles[:min_len]
    nom_c = nom_candles[:min_len]
    
    for i in range(min_len):
        rc = rave_c[i]
        nc = nom_c[i]
        
        r_close = float(rc["close"])
        r_high = float(rc["high"])
        r_low = float(rc["low"])
        r_open = float(rc["open"])
        
        n_close = float(nc["close"])
        n_high = float(nc["high"])
        n_low = float(nc["low"])
        n_open = float(nc["open"])
        
        ts = int(rc.get("start", rc.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}
        
        # --- RAVE position management ---
        if rave_pos:
            rave_pos["hold"] += 1
            exit_price = None
            if r_high >= rave_pos["tp"]:
                exit_price = rave_pos["tp"]
            elif rave_pos["hold"] >= rave_pos["max_hold"]:
                exit_price = r_close
            if exit_price:
                net = (exit_price - rave_pos["ep"]) * rave_pos["units"] - rave_pos["entry_fee"] - exit_price * rave_pos["units"] * 0.004
                cash += rave_pos["q"] + net
                closes_count += 1
                if net > 0: wins += 1
                else: losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                rave_pos = None
        
        # --- NOM position management ---
        if nom_pos:
            nom_pos["hold"] += 1
            exit_price = None
            if n_high >= nom_pos["tp"]:
                exit_price = nom_pos["tp"]
            elif n_low <= nom_pos["sl"] and nom_pos["sl"] > 0:
                exit_price = nom_pos["sl"]
            elif nom_pos["hold"] >= nom_pos["max_hold"]:
                exit_price = n_close
            if exit_price:
                net = (exit_price - nom_pos["ep"]) * nom_pos["units"] - nom_pos["entry_fee"] - exit_price * nom_pos["units"] * 0.004
                cash += nom_pos["q"] + net
                closes_count += 1
                if net > 0: wins += 1
                else: losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                nom_pos = None
        
        # --- RAVE entry (momentum lb=15) ---
        if rave_pos is None and session_open and cash >= 10.0 and i >= 15:
            recent_high = max(float(rave_c[j]["high"]) for j in range(i-15, i))
            if r_high > recent_high:
                deploy = cash * 0.95
                entry_fee = deploy * 0.004
                ep = r_open * 1.0008
                units = (deploy - entry_fee) / ep
                tp = ep * 1.10
                cash -= deploy
                rave_pos = {"ep": ep, "q": deploy, "units": units, "tp": tp, "max_hold": 48, "hold": 0, "entry_fee": entry_fee}
        
        # --- NOM entry (range_breakout lb=10) ---
        if nom_pos is None and session_open and cash >= 10.0 and i >= 10:
            range_high = max(float(nom_c[j]["high"]) for j in range(i-10, i))
            if n_high > range_high:
                deploy = cash * 0.95
                entry_fee = deploy * 0.004
                ep = n_open * 1.0008
                units = (deploy - entry_fee) / ep
                tp = ep * 1.10
                sl = ep * 0.99
                cash -= deploy
                nom_pos = {"ep": ep, "q": deploy, "units": units, "tp": tp, "sl": sl, "max_hold": 24, "hold": 0, "entry_fee": entry_fee}
    
    # Close any remaining positions
    for pos, close in [(rave_pos, r_close), (nom_pos, n_close)]:
        if pos:
            net = (close - pos["ep"]) * pos["units"] - pos["entry_fee"] - close * pos["units"] * 0.004
            cash += pos["q"] + net
            closes_count += 1
            if net > 0: wins += 1
            else: losses += 1
    
    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100
    return {"net_pnl": round(net, 2), "win_rate": round(wr, 1), "trades": closes_count,
            "wins": wins, "losses": losses, "max_drawdown": round(max_dd * 100, 1),
            "final_cash": round(cash, 2)}

result = shared_bankroll_sim(rave, nom)
print(f"\nShared Bankroll Result:")
print(f"  Net PnL: ${result['net_pnl']:.2f}")
print(f"  Win Rate: {result['win_rate']}%")
print(f"  Trades: {result['trades']}")
print(f"  Max DD: {result['max_drawdown']}%")
print(f"  Final Cash: ${result['final_cash']:.2f}")

# Compare with individual
indiv_total = rave_r['net_pnl'] + nom_rb['net_pnl']
print(f"\nComparison:")
print(f"  Individual total: ${indiv_total:.2f}")
print(f"  Shared bankroll:  ${result['net_pnl']:.2f}")
print(f"  Synergy:          ${result['net_pnl'] - indiv_total:.2f}")

if result['net_pnl'] > 20000:
    print(f"\n  🚨 SYNERGY CONFIRMED — $26K+ is real")
elif result['net_pnl'] > 5000:
    print(f"\n  ⚠️ POSITIVE but not $26K — synergy exists but was overstated")
elif result['net_pnl'] > indiv_total:
    print(f"\n  ✅ Small synergy — shared > individual but not explosive")
else:
    print(f"\n  ❌ NO synergy — shared bankroll underperforms individual")

print("\n" + "="*70)
print("VERIFICATION COMPLETE")
