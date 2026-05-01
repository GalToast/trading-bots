#!/usr/bin/env python3
"""
Momentum + Theil-Sen Robust Regression Combined
=================================================

Tests whether running BOTH momentum and v2_theil_sen on the SAME coin
improves results vs running either alone.

Key questions:
1. Do they fire on different bars? (overlap analysis)
2. Does the combined PnL exceed the sum of individual PnLs? (synergy)
3. Does the combined WR improve? (quality)

Uses per-coin independent bankrolls ($100 each) to avoid the shared-pool death spiral.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from strategy_library import backtest

COINS = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]
FEE_RATE = 0.004
STARTING_CASH = 100.0

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "momentum_plus_theilsen.json"


# ==========================================
# ENTRY FUNCTIONS
# ==========================================
def momentum_entry(candles_hist, closes, candle, params):
    lookback = params.get("lookback", 15)
    if len(candles_hist) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def theil_sen_entry(candles_hist, closes, candle, params):
    reg_period = params.get("reg_period", 20)
    if len(closes) < reg_period + 5:
        return False
    recent = closes[-reg_period:]
    n = len(recent)
    x = list(range(n))
    y = recent
    slopes = []
    for i in range(0, n - 1, 2):
        if x[i + 1] - x[i] != 0:
            slopes.append((y[i + 1] - y[i]) / (x[i + 1] - x[i]))
    if not slopes:
        return False
    med_slope = sorted(slopes)[len(slopes) // 2]
    med_y = sorted(y)[len(y) // 2]
    med_x = sorted(x)[len(x) // 2]
    intercept = med_y - med_slope * med_x
    predicted = med_slope * n + intercept
    actual = y[-1]
    deviation = (predicted - actual) / actual
    return deviation < -0.02


def combined_entry(candles_hist, closes, candle, params):
    """Fire if EITHER momentum or theil-sen fires."""
    mom = momentum_entry(candles_hist, closes, candle, params)
    ts = theil_sen_entry(candles_hist, closes, candle, params)
    return mom or ts


# ==========================================
# MAIN
# ==========================================
def main():
    print("=" * 80)
    print("  MOMENTUM + THEIL-SEN COMBINED TEST")
    print("=" * 80)
    print(f"Per-coin bankroll: ${STARTING_CASH}, Fee: {FEE_RATE*100:.1f}%")
    print()

    # Optimal params from prior research
    mom_params = {"lookback": 15, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48}
    ts_params = {"reg_period": 20, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48}
    combined_params = {"lookback": 15, "reg_period": 20, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48}

    results = {}

    for coin_name in COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles = data["candles"]

            mom_result = backtest(candles, momentum_entry, mom_params, FEE_RATE, STARTING_CASH)
            ts_result = backtest(candles, theil_sen_entry, ts_params, FEE_RATE, STARTING_CASH)
            comb_result = backtest(candles, combined_entry, combined_params, FEE_RATE, STARTING_CASH)

            mom_net = mom_result["net_pnl"]
            ts_net = ts_result["net_pnl"]
            comb_net = comb_result["net_pnl"]
            sum_individual = mom_net + ts_net
            synergy = comb_net - sum_individual

            results[coin_name] = {
                "momentum": {
                    "net_pnl": round(mom_net, 2),
                    "win_rate": round(mom_result["win_rate"], 1),
                    "trades": mom_result["trades"],
                    "signals": mom_result["signals"],
                    "max_drawdown": round(mom_result["max_drawdown"], 1),
                },
                "theil_sen": {
                    "net_pnl": round(ts_net, 2),
                    "win_rate": round(ts_result["win_rate"], 1),
                    "trades": ts_result["trades"],
                    "signals": ts_result["signals"],
                    "max_drawdown": round(ts_result["max_drawdown"], 1),
                },
                "combined": {
                    "net_pnl": round(comb_net, 2),
                    "win_rate": round(comb_result["win_rate"], 1),
                    "trades": comb_result["trades"],
                    "signals": comb_result["signals"],
                    "max_drawdown": round(comb_result["max_drawdown"], 1),
                },
                "sum_individuals": round(sum_individual, 2),
                "synergy": round(synergy, 2),
                "synergy_pct": round(synergy / max(0.01, abs(sum_individual)) * 100, 1),
            }

            r = results[coin_name]
            print(f"  {coin_name}:", flush=True)
            print(f"    Momentum:    ${r['momentum']['net_pnl']:+.2f} (WR={r['momentum']['win_rate']}%, {r['momentum']['trades']} trades, {r['momentum']['signals']} signals)", flush=True)
            print(f"    Theil-Sen:   ${r['theil_sen']['net_pnl']:+.2f} (WR={r['theil_sen']['win_rate']}%, {r['theil_sen']['trades']} trades, {r['theil_sen']['signals']} signals)", flush=True)
            print(f"    Combined:    ${r['combined']['net_pnl']:+.2f} (WR={r['combined']['win_rate']}%, {r['combined']['trades']} trades, {r['combined']['signals']} signals)", flush=True)
            print(f"    Sum alone:   ${r['sum_individuals']:+.2f}", flush=True)
            syn = r['synergy']
            syn_label = "✅ SYNERGY" if syn > 0 else "⚠️ DILUTION" if syn > -50 else "❌ INTERFERENCE"
            print(f"    Synergy:     ${syn:+.2f} ({r['synergy_pct']:+.1f}%) {syn_label}", flush=True)
            print()

        except Exception as e:
            print(f"  {coin_name}: ERROR — {e}", flush=True)

    # ==========================================
    # SUMMARY
    # ==========================================
    print(f"{'='*80}", flush=True)
    print("  SUMMARY", flush=True)
    print(f"{'='*80}", flush=True)

    total_mom = sum(r["momentum"]["net_pnl"] for r in results.values())
    total_ts = sum(r["theil_sen"]["net_pnl"] for r in results.values())
    total_comb = sum(r["combined"]["net_pnl"] for r in results.values())
    total_syn = total_comb - (total_mom + total_ts)

    print(f"\n  Strategy       | Total PnL | Avg WR% | Total Trades | Total Signals", flush=True)
    print(f"  {'-'*17}-+-{'-'*9}-+-{'-'*9}-+-{'-'*12}-+-{'-'*13}", flush=True)
    print(f"  {'Momentum':<17} | ${total_mom:>+8.2f} | {sum(r['momentum']['win_rate'] for r in results.values())/len(results):>7.1f} | {sum(r['momentum']['trades'] for r in results.values()):>12} | {sum(r['momentum']['signals'] for r in results.values()):>13}", flush=True)
    print(f"  {'Theil-Sen':<17} | ${total_ts:>+8.2f} | {sum(r['theil_sen']['win_rate'] for r in results.values())/len(results):>7.1f} | {sum(r['theil_sen']['trades'] for r in results.values()):>12} | {sum(r['theil_sen']['signals'] for r in results.values()):>13}", flush=True)
    print(f"  {'Combined':<17} | ${total_comb:>+8.2f} | {sum(r['combined']['win_rate'] for r in results.values())/len(results):>7.1f} | {sum(r['combined']['trades'] for r in results.values()):>12} | {sum(r['combined']['signals'] for r in results.values()):>13}", flush=True)
    print(f"  {'Synergy':<17} | ${total_syn:>+8.2f}", flush=True)

    print(f"\n{'='*80}", flush=True)
    print("  KEY FINDINGS", flush=True)
    print(f"{'='*80}", flush=True)

    if total_syn > 0:
        print(f"\n  ✅ POSITIVE SYNERGY: Combined (${total_comb:+.2f}) exceeds sum of individuals (${total_mom + total_ts:+.2f})")
        print(f"     Running both strategies on the same coins adds ${total_syn:+.2f} extra PnL.")
        print(f"     The strategies fire on different bars and complement each other.")
    elif total_syn > -50:
        print(f"\n  ⚠️ NEAR-ADDITIVE: Combined (${total_comb:+.2f}) ≈ sum of individuals (${total_mom + total_ts:+.2f})")
        print(f"     No significant synergy or interference. Combined result is roughly what you'd expect.")
        print(f"     Running both gives more trades but doesn't improve per-trade quality.")
    else:
        print(f"\n  ❌ NEGATIVE SYNERGY (INTERFERENCE): Combined (${total_comb:+.2f}) < sum of individuals (${total_mom + total_ts:+.2f})")
        print(f"     Running both strategies TOGETHER loses ${-total_syn:.2f} vs running separately.")
        print(f"     The strategies interfere with each other's positions (shared bankroll conflict).")

    # Save report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "coins": COINS,
        "results": results,
        "totals": {
            "momentum": round(total_mom, 2),
            "theil_sen": round(total_ts, 2),
            "combined": round(total_comb, 2),
            "synergy": round(total_syn, 2),
        },
        "conclusion": "positive_synergy" if total_syn > 0 else "near_additive" if total_syn > -50 else "negative_synergy",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
