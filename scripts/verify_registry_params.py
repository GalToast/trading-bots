#!/usr/bin/env python3
"""Verify GHST/TRU/RED/NOM at their CLAIMED 7d registry params on 30d data."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

# Claimed 7d registry params from @main's sweep
claimed = {
    'GHST-USD': {'lookback': 5, 'tp_pct': 15, 'sl_pct': 3, 'max_hold': 36},
    'TRU-USD': {'lookback': 10, 'tp_pct': 10, 'sl_pct': 2, 'max_hold': 24},
    'RED-USD': {'lookback': 8, 'tp_pct': 10, 'sl_pct': 8, 'max_hold': 48},
    'NOM-USD': {'lookback': 30, 'tp_pct': 8, 'sl_pct': 8, 'max_hold': 12},
}

lines = []
lines.append("Registry-Param Verification (7d claimed params → 30d validation)")
lines.append("=" * 70)

for coin, params in claimed.items():
    print(f"Fetching {coin}...", flush=True)
    try:
        candles = normalize_candles(fetch_candles_coinbase(coin, 30))
        r = momentum(candles, lookback=params['lookback'], tp_pct=params['tp_pct'],
                     sl_pct=params['sl_pct'], max_hold=params['max_hold'],
                     fee_rate=0.004, starting_cash=100.0, seed=42)
        
        lines.append(f"\n{coin} (30d, {len(candles)} candles):")
        lines.append(f"  Claimed params: lb={params['lookback']}, TP={params['tp_pct']}%, SL={params['sl_pct']}%, MH={params['max_hold']}")
        lines.append(f"  7d claim: See @main sweep for 7d numbers")
        lines.append(f"  30d result: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
        
        if r['net_pnl'] > 0 and r['win_rate'] >= 40:
            lines.append(f"  [CLAIMED_PARAM_CONFIRMED ✅]")
        elif r['net_pnl'] > 0:
            lines.append(f"  [CLAIMED_PARAM_WEAK - net positive but WR<40%]")
        else:
            lines.append(f"  [CLAIMED_PARAM_FAILED ❌ — only works after optimization]")
    except Exception as e:
        lines.append(f"\n{coin}: ERROR - {e}")

result = "\n".join(lines)
with open("reports/registry_param_verification.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
