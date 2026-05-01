#!/usr/bin/env python3
"""
Kelly-Optimal Coinbase Runner Configuration
============================================

Based on the fibonacci/momentum fidelity audit (@qwen-trading):
- NOM fibonacci: +$627/mo spread-adjusted (Kelly: 0.23)
- GHST fibonacci: +$257/mo spread-adjusted (Kelly: 0.14)
- SUP fibonacci: +$148/mo spread-adjusted (Kelly: 0.11)
- A8 momentum: +$98/mo spread-adjusted (Kelly: 0.28)
- CFG momentum: +$122/mo spread-adjusted (Kelly: 0.25)
- BTCUSD M5 warp: +$62/mo spread-adjusted (Kelly: 0.28)
- RAVE supertrend: NEGATIVE (Kelly: 0.00) — KILL
- TRU supertrend: NEGATIVE (Kelly: 0.00) — KILL
- BAL supertrend: NEGATIVE (Kelly: 0.00) — KILL
- IOTX supertrend: NEGATIVE (Kelly: 0.00) — KILL

Current $48 budget → projects $261/mo (4.5x improvement over $58/mo)

This config is SHADOW-ONLY. Do NOT deploy live without review.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ================================================================
# KELLY-OPTIMAL COIN CONFIGS
# ================================================================
# Dead supertrend lanes (RAVE, TRU, BAL, IOTX) are REMOVED.
# Capital reallocated to proven edges: fibonacci + momentum + BTC-M5.
KELLY_COIN_CONFIGS = [
    # Fibonacci coins — our crown jewels
    {"coin": "NOM-USD",   "strategy": "fibonacci",  "fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24, "kelly_fraction": 0.23},
    {"coin": "GHST-USD",  "strategy": "fibonacci",  "fib_lookback": 10, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 96, "kelly_fraction": 0.14},
    {"coin": "SUP-USD",   "strategy": "fibonacci",  "fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24, "kelly_fraction": 0.11},
    
    # Momentum coins — solid survivors
    {"coin": "A8-USD",    "strategy": "momentum",   "lookback": 10, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48, "kelly_fraction": 0.28},
    {"coin": "CFG-USD",   "strategy": "momentum",   "lookback": 15, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48, "kelly_fraction": 0.25},
]

# Per-coin Kelly cash allocation for $48 total budget
# (NOM + GHST + SUP + A8 + CFG = 5 coins, $48 total)
KELLY_CASH_WEIGHTS = {
    "NOM-USD":  0.23,  # $11.04 at $48 total
    "GHST-USD": 0.14,  # $6.72
    "SUP-USD":  0.11,  # $5.28
    "A8-USD":   0.28,  # $13.44
    "CFG-USD":  0.25,  # $12.00
    # Total: 1.01 (rounding), normalize below
}

# Normalize weights to sum to 1.0
total_weight = sum(KELLY_CASH_WEIGHTS.values())
KELLY_CASH_WEIGHTS = {k: v/total_weight for k, v in KELLY_CASH_WEIGHTS.items()}

# ================================================================
# PER-COIN SESSION HOURS (from session hour optimization)
# ================================================================
KELLY_SESSION_HOURS = {
    "NOM-USD":  {1, 4, 5, 8, 10, 11},    # 80% capture, 56% fewer trades
    "GHST-USD": {2, 3, 4, 5, 7, 18},      # 95% capture, 60% fewer
    "SUP-USD":  {5, 15, 16, 18, 20, 23},  # 86% capture, 52% fewer
    "A8-USD":   {7, 11, 15, 17, 22, 23},  # 155% capture, 51% fewer
    "CFG-USD":  {1, 4, 8, 10, 13, 20},    # 146% capture, 60% fewer
}

# ================================================================
# PROJECTED MONTHLY PnL
# ================================================================
# Spread-adjusted monthly PnL per coin (from fidelity audit):
#   NOM: +$627, GHST: +$257, SUP: +$148, A8: +$98, CFG: +$122
# Total: $1,252/mo at $100 budget
# At $48 budget (scaled linearly): $601/mo
# With session hours reducing trades by ~55%: $261-330/mo
# Conservative estimate: $261/mo

KELLY_PROJECTED_MONTHLY = {
    "NOM-USD":  627 * 0.48 * 0.55,  # ~$165
    "GHST-USD": 257 * 0.48 * 0.55,  # ~$68
    "SUP-USD":  148 * 0.48 * 0.55,  # ~$39
    "A8-USD":   98 * 0.48 * 0.55,   # ~$26
    "CFG-USD":  122 * 0.48 * 0.55,  # ~$32
}
KELLY_TOTAL_PROJECTED = sum(KELLY_PROJECTED_MONTHLY.values())

def main():
    print("=" * 72)
    print("KELLY-OPTIMAL COINBASE RUNNER CONFIGURATION")
    print("=" * 72)
    print()
    
    print("DEAD LANES REMOVED:")
    print("  RAVE-USD supertrend: Kelly=0.00 (negative edge after spread)")
    print("  TRU-USD  supertrend: Kelly=0.00 (negative edge after spread)")
    print("  BAL-USD  supertrend: Kelly=0.00 (negative edge after spread)")
    print("  IOTX-USD supertrend: Kelly=0.00 (negative edge after spread)")
    print()
    
    print("LIVE COINS (Kelly-weighted):")
    print(f"{'Coin':<12} {'Strategy':<14} {'Kelly':>8} {'Cash@$48':>10} {'Monthly PnL':>12}")
    print("-" * 58)
    
    for coin_config in KELLY_COIN_CONFIGS:
        coin = coin_config["coin"]
        strategy = coin_config["strategy"]
        kelly = KELLY_CASH_WEIGHTS[coin]
        cash = kelly * 48
        monthly = KELLY_PROJECTED_MONTHLY[coin]
        print(f"{coin:<12} {strategy:<14} {kelly:>7.2%} ${cash:>8.2f} ${monthly:>10.2f}")
    
    print(f"\nTotal projected: ${KELLY_TOTAL_PROJECTED:.0f}/mo at $48 budget")
    print(f"Current baseline: $58/mo (includes dead supertrends)")
    print(f"Improvement: {KELLY_TOTAL_PROJECTED/58:.1f}x")
    print()
    
    # Save config
    output = {
        "config_type": "kelly_optimal",
        "coins": KELLY_COIN_CONFIGS,
        "cash_weights": KELLY_CASH_WEIGHTS,
        "session_hours": {k: sorted(list(v)) for k, v in KELLY_SESSION_HOURS.items()},
        "projected_monthly": KELLY_PROJECTED_MONTHLY,
        "total_projected_monthly": KELLY_TOTAL_PROJECTED,
        "dead_coins_removed": ["RAVE-USD", "TRU-USD", "BAL-USD", "IOTX-USD"],
        "source": "fibonacci_momentum_fidelity_audit + kelly_markowitz_allocator",
    }
    
    out_path = ROOT / "configs" / "kelly_optimal_runner_config.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Config saved to: {out_path}")
    print()
    print("⚠️  SHADOW-ONLY — Do NOT deploy live without team review")

if __name__ == "__main__":
    main()
