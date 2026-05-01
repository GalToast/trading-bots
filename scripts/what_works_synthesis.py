#!/usr/bin/env python3
"""
What Works — Definitive Synthesis Report
==========================================
Aggregates ALL verified 30d backtest results into one ranked table.
Only includes edges verified through strategy_library.py on 30d data.
"""
import sys; sys.path.insert(0, 'scripts')
import json
from strategy_library import backtest, _momentum_entry, _range_breakout_entry, _rsi_mr_entry

# ---------------------------------------------------------------------------
# Verified 30d results from individual backtests
# ---------------------------------------------------------------------------

VERIFIED_RESULTS = [
    # Momentum
    ("RAVE-USD", "momentum", {"lookback": 15, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 36}, "RAVE_USD_FIVE_MINUTE_30d.json"),
    ("NOM-USD", "momentum", {"lookback": 30, "tp_pct": 8.0, "sl_pct": 8.0, "max_hold": 12}, "NOM_USD_FIVE_MINUTE_30d.json"),
    ("GHST-USD", "momentum", {"lookback": 20, "tp_pct": 15.0, "sl_pct": 3.0, "max_hold": 24}, "GHST_USD_FIVE_MINUTE_30d.json"),
    ("TRU-USD", "momentum", {"lookback": 10, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 24}, "TRU_USD_FIVE_MINUTE_30d.json"),
    ("SUP-USD", "momentum", {"lookback": 10, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 24}, "SUP_USD_FIVE_MINUTE_30d.json"),
    ("A8-USD", "momentum", {"lookback": 10, "tp_pct": 15.0, "sl_pct": 0.0, "max_hold": 48}, "A8_USD_FIVE_MINUTE_30d.json"),
    ("BAL-USD", "momentum", {"lookback": 50, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 36}, "BAL_USD_FIVE_MINUTE_30d.json"),
    ("IOTX-USD", "momentum", {"lookback": 20, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 24}, "IOTX_USD_FIVE_MINUTE_30d.json"),
    ("CFG-USD", "momentum", {"lookback": 50, "tp_pct": 15.0, "sl_pct": 0.0, "max_hold": 48}, "CFG_USD_FIVE_MINUTE_30d.json"),
    
    # Range Breakout
    ("NOM-USD", "range_breakout", {"range_lookback": 10, "tp_pct": 10.0, "sl_pct": 1.0, "max_hold": 24}, "NOM_USD_FIVE_MINUTE_30d.json"),
    ("SUP-USD", "range_breakout", {"range_lookback": 8, "tp_pct": 8.0, "sl_pct": 1.0, "max_hold": 24}, "SUP_USD_FIVE_MINUTE_30d.json"),
    ("PRL-USD", "range_breakout", {"range_lookback": 25, "tp_pct": 10.0, "sl_pct": 1.0, "max_hold": 36}, "PRL_USD_FIVE_MINUTE_30d.json"),
    ("BAL-USD", "range_breakout", {"range_lookback": 50, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 36}, "BAL_USD_FIVE_MINUTE_30d.json"),
    
    # RSI MR
    ("MOG-USD", "rsi_mr", {"rsi_period": 4, "os_thresh": 45, "tp_pct": 7.5, "sl_pct": 0.5, "max_hold": 48}, "MOG_USD_FIVE_MINUTE_30d.json"),
    
    # Volume-weighted (approximate via strategy_library)
    # Robust Regression (approximate via strategy_library - not directly available, skip for now)
]

STRATEGY_MAP = {
    "momentum": _momentum_entry,
    "range_breakout": _range_breakout_entry,
    "rsi_mr": _rsi_mr_entry,
}

def main():
    print("=" * 80)
    print("  WHAT WORKS — DEFINITIVE 30D SYNTHESIS REPORT")
    print("=" * 80)
    print()
    
    all_results = []
    
    for coin, strat_name, params, cache_file in VERIFIED_RESULTS:
        try:
            entry_fn = STRATEGY_MAP[strat_name]
            data = json.loads(open(f'reports/candle_cache/{cache_file}').read())
            candles = data['candles']
            result = backtest(candles, entry_fn, params, 0.004, 100.0)
            
            all_results.append({
                'coin': coin,
                'strategy': strat_name,
                'net': result['net_pnl'],
                'wr': result['win_rate'],
                'trades': result['trades'],
                'dd': result['max_drawdown'],
                'signals': result['signals'],
                'params': params,
            })
        except Exception as e:
            print(f"  ERROR: {coin} {strat_name}: {e}")
    
    # Sort by net PnL
    all_results.sort(key=lambda x: x['net'], reverse=True)
    
    # Print all results
    print(f"  {'#':>3} {'Coin':<12} {'Strategy':<18} {'Net/mo':>9} {'WR%':>5} "
          f"{'Trades':>7} {'DD%':>5} {'Signals':>8}")
    print(f"  {'-'*3} {'-'*12} {'-'*18} {'-'*9} {'-'*5} {'-'*7} {'-'*5} {'-'*8}")
    
    for rank, r in enumerate(all_results, 1):
        print(f"  {rank:>3} {r['coin']:<12} {r['strategy']:<18} "
              f"${r['net']:>8.2f} {r['wr']:>5.1f}% {r['trades']:>7} "
              f"{r['dd']:>5.1f}% {r['signals']:>8}")
    
    # Filter: WR >= 45%, DD <= 40%
    qualified = [r for r in all_results if r['wr'] >= 45.0 and r['dd'] <= 40.0]
    print(f"\n{'='*80}")
    print(f"  QUALIFIED EDGES (WR>=45%, DD<=40%): {len(qualified)}")
    print(f"{'='*80}")
    
    if qualified:
        print(f"\n  {'#':>3} {'Coin':<12} {'Strategy':<18} {'Net/mo':>9} {'WR%':>5} "
              f"{'DD%':>5}")
        print(f"  {'-'*3} {'-'*12} {'-'*18} {'-'*9} {'-'*5} {'-'*5}")
        for rank, r in enumerate(qualified, 1):
            print(f"  {rank:>3} {r['coin']:<12} {r['strategy']:<18} "
                  f"${r['net']:>8.2f} {r['wr']:>5.1f}% {r['dd']:>5.1f}%")
    else:
        print("  No edges pass the WR>=45%, DD<=40% filter.")
    
    # Group by strategy
    print(f"\n{'='*80}")
    print(f"  BY STRATEGY FAMILY")
    print(f"{'='*80}")
    
    families = {}
    for r in all_results:
        fam = r['strategy']
        if fam not in families:
            families[fam] = []
        families[fam].append(r)
    
    for fam, results in sorted(families.items(), key=lambda x: -sum(r['net'] for r in x[1])):
        total_net = sum(r['net'] for r in results)
        total_trades = sum(r['trades'] for r in results)
        avg_wr = sum(r['wr'] for r in results) / len(results) if results else 0
        print(f"\n  {fam} ({len(results)} coins, total ${total_net:.0f}/mo, avg WR {avg_wr:.1f}%):")
        for r in sorted(results, key=lambda x: -x['net']):
            check = "✅" if r['wr'] >= 45.0 and r['dd'] <= 40.0 else "⚠️" if r['net'] > 0 else "❌"
            print(f"    {check} {r['coin']}: ${r['net']:+.2f}, WR={r['wr']:.1f}%, DD={r['dd']:.1f}%")
    
    # Summary statistics
    profitable = [r for r in all_results if r['net'] > 0]
    print(f"\n{'='*80}")
    print(f"  SUMMARY STATISTICS")
    print(f"{'='*80}")
    print(f"  Total combos tested: {len(all_results)}")
    print(f"  Profitable: {len(profitable)} ({len(profitable)/len(all_results)*100:.0f}%)")
    print(f"  Unprofitable: {len(all_results) - len(profitable)}")
    print(f"  Qualified (WR>=45%, DD<=40%): {len(qualified)}")
    print(f"  Total net (all combos): ${sum(r['net'] for r in all_results):.0f}/mo")
    print(f"  Total net (qualified only): ${sum(r['net'] for r in qualified):.0f}/mo")
    
    # Save report
    report = {
        'all_results': all_results,
        'qualified': qualified,
        'summary': {
            'total_combos': len(all_results),
            'profitable': len(profitable),
            'qualified': len(qualified),
            'total_net': round(sum(r['net'] for r in all_results), 2),
            'qualified_net': round(sum(r['net'] for r in qualified), 2),
        },
    }
    
    output_path = "reports/what_works_synthesis.json"
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Also save markdown
    md_path = "reports/what_works_synthesis.md"
    with open(md_path, 'w') as f:
        f.write("# What Works — Definitive 30D Synthesis Report\n\n")
        f.write(f"Total combos tested: {len(all_results)}\n")
        f.write(f"Profitable: {len(profitable)} ({len(profitable)/len(all_results)*100:.0f}%)\n")
        f.write(f"Qualified (WR>=45%, DD<=40%): {len(qualified)}\n\n")
        
        f.write("## Ranked Results\n\n")
        f.write("| # | Coin | Strategy | Net/mo | WR% | Trades | DD% | Signals |\n")
        f.write("|---|------|----------|--------|-----|--------|-----|--------|\n")
        for rank, r in enumerate(all_results, 1):
            f.write(f"| {rank} | {r['coin']} | {r['strategy']} | ${r['net']:.2f} | {r['wr']:.1f}% | {r['trades']} | {r['dd']:.1f}% | {r['signals']} |\n")
        
        f.write(f"\n## Qualified Edges (WR>=45%, DD<=40%)\n\n")
        for rank, r in enumerate(qualified, 1):
            f.write(f"{rank}. **{r['coin']}** + **{r['strategy']}**: ${r['net']:.2f}/mo, WR={r['wr']:.1f}%, DD={r['dd']:.1f}%\n")
    
    print(f"\n  Report saved: {output_path}")
    print(f"  Markdown saved: {md_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
