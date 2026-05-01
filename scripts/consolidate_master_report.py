#!/usr/bin/env python3
"""Consolid all parallel scan chunks into master report."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"

chunk_files = [
    "parallel_scan_chunk_0_49.json",
    "parallel_scan_chunk_50_99.json",
    "parallel_scan_chunk_100_149.json",
    "parallel_scan_chunk_150_199.json",
    "parallel_scan_chunk_200_end.json",
]

all_passing = []
total_coins = 0
total_combos = 0

for fname in chunk_files:
    fpath = REPORT_DIR / fname
    if not fpath.exists():
        print(f"  SKIP: {fname}")
        continue
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        # Direct list of results
        all_passing.extend(data)
    elif isinstance(data, dict):
        total_coins += data.get("coins_scanned", 0)
        total_combos += data.get("total_combos_tested", 0)
        # Results are in top_results (chunk 1) or as a list (chunks 3-5)
        results = data.get("top_results", [])
        if not results:
            # Try other list keys
            for k, v in data.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    results = v
                    break
        all_passing.extend(results)

# Normalize key names across different agent outputs
for r in all_passing:
    # Normalize net_pnl -> net
    if "net_pnl" in r and "net" not in r:
        r["net"] = r["net_pnl"]
    # Normalize wr_pct -> win_rate
    if "wr_pct" in r and "win_rate" not in r:
        r["win_rate"] = r["wr_pct"]
    # Normalize max_dd_pct -> max_drawdown
    if "max_dd_pct" in r and "max_drawdown" not in r:
        r["max_drawdown"] = r["max_dd_pct"]
    # Normalize total_pnl -> net (if still missing)
    if "total_pnl" in r and "net" not in r:
        r["net"] = r["total_pnl"]

# Filter: only positive net, >= 5 trades
all_passing = [r for r in all_passing if r.get("net", 0) > 0 and r.get("trades", 0) >= 5]

# Sort by net
all_passing.sort(key=lambda x: x.get("net", 0), reverse=True)

top_50 = all_passing[:50]

# Best per coin
coin_best = {}
for r in all_passing:
    coin = r.get("coin", "?")
    if coin not in coin_best or r.get("net", 0) > coin_best[coin].get("net", 0):
        coin_best[coin] = r

coins_sorted = sorted(coin_best.values(), key=lambda x: x.get("net", 0), reverse=True)

# Stats per coin
coin_stats = {}
for r in all_passing:
    coin = r.get("coin", "?")
    if coin not in coin_stats:
        coin_stats[coin] = {"count": 0, "net_max": 0, "trades_total": 0, "wr_sum": 0}
    coin_stats[coin]["count"] += 1
    coin_stats[coin]["trades_total"] += r.get("trades", 0)
    coin_stats[coin]["wr_sum"] += r.get("win_rate", 0)
    if r.get("net", 0) > coin_stats[coin]["net_max"]:
        coin_stats[coin]["net_max"] = r["net"]

for coin in coin_stats:
    coin_stats[coin]["avg_wr"] = coin_stats[coin]["wr_sum"] / max(1, coin_stats[coin]["count"])

print(f"\n{'=' * 80}")
print(f"  MASTER REPORT — {total_coins} coins x 500 combos = {total_combos:,} backtests")
print(f"  {len(all_passing):,} passing combos (net > 0, trades >= 5)")
print(f"  {len(coins_sorted)} coins with edge")
print(f"{'=' * 80}")

print(f"\n  TOP 50 MOST PROFITABLE:")
print(f"  {'Rank':>4} {'Coin':<18} {'RSI':>3} {'TP':>5} {'Hold':>5} {'Net':>9} {'Trades':>7} {'WR%':>5} {'DD%':>5}")
print(f"  {'----'} {'------------------'} {'---'} {'-----'} {'-----'} {'---------'} {'-------'} {'-----'} {'-----'}")
for i, r in enumerate(top_50):
    net = r.get("net", 0)
    trades = r.get("trades", 0)
    wr = r.get("win_rate", 0)
    dd = r.get("max_drawdown", 0)
    print(f"  {i+1:>4} {r['coin']:<18} {r.get('rsi_period','?'):>3} {r.get('tp',0)*100:>4.0f}% {r.get('max_hold','?'):>5} "
          f"${net:+8.2f}  {trades:>6}  {wr:>4.1f}% {dd:>4.1f}%")

print(f"\n  TOP 20 COINS (best single combo):")
print(f"  {'Rank':>4} {'Coin':<18} {'Best Net':>9} {'Combos':>7} {'Avg WR%':>7}")
print(f"  {'----'} {'------------------'} {'---------'} {'-------'} {'-------'}")
for i, r in enumerate(coins_sorted[:20]):
    coin = r["coin"]
    stats = coin_stats.get(coin, {})
    print(f"  {i+1:>4} {coin:<18} ${r['net']:+8.2f}  {stats.get('count',0):>6}  {stats.get('avg_wr',0):>6.1f}%")

# Save
master = {
    "total_coins_scanned": total_coins,
    "total_combos_tested": total_combos,
    "passing_combos": len(all_passing),
    "coins_with_edge": len(coins_sorted),
    "top_50": top_50,
    "coin_best": {r["coin"]: r for r in coins_sorted[:50]},
    "coin_stats": coin_stats,
}
output_path = REPORT_DIR / "master_edge_scan_all_coins.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(master, f, indent=2, default=str)
print(f"\n  Saved: {output_path}")
