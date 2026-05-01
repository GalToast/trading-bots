#!/usr/bin/env python3
"""
Coinbase Opportunity Sweep — Find EVERY profitable coin-strategy combo.

Scans ALL tradeable Coinbase coins, tests proven strategies on each,
and reports every combination that makes money. The math is simple:
$10/month × 100 coins = $1,000/month. We don't need home runs.

Strategies swept:
1. Momentum (10-bar, 10% TP, 10% SL)
2. Momentum (25-bar, 12% TP, 7% SL)
3. Momentum (50-bar, 10% TP, 3% SL)
4. RSI MR (3, 30, 25% TP, 48 bars)
5. BB Reversion (30, 20-period, 5% SL, 24 bars)

Output: reports/coinbase_opportunity_sweep.json
"""
import json
import os
import sys
import time
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
import strategy_library as strategy_lib

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "coinbase_opportunity_sweep.json"

BTC = "BTC-USD"
WINDOW_DAYS = 14
STARTING_CASH = 48.0
FEE_RATE = 0.0040
PARTIAL_SAVE_PATH = ROOT / "reports" / "coinbase_opportunity_sweep_partial.json"

# Strategies to sweep
STRATEGIES = [
    {"name": "mom_10", "type": "momentum", "params": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 10.0, "max_hold": 48}},
    {"name": "mom_25", "type": "momentum", "params": {"lookback": 25, "tp_pct": 12.0, "sl_pct": 7.0, "max_hold": 48}},
    {"name": "mom_50", "type": "momentum", "params": {"lookback": 50, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48}},
    {"name": "rsi_mr", "type": "rsi_mr", "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 25.0, "max_hold": 48}},
    {"name": "bb_rev", "type": "bb_reversion", "params": {"rsi_thresh": 30, "bb_period": 20, "proximity_pct": 2.0, "sl_pct": 5.0, "max_hold": 24}},
]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_strategy_backtest(candles, strategy):
    common_kwargs = {
        "fee_rate": FEE_RATE,
        "starting_cash": STARTING_CASH,
        "entry_slip": 0.0,
        "exit_slip": 0.0,
    }
    strategy_type = strategy["type"]
    params = dict(strategy["params"])

    if strategy_type == "momentum":
        result = strategy_lib.momentum(candles, **params, **common_kwargs)
    elif strategy_type == "rsi_mr":
        result = strategy_lib.rsi_mr(candles, **params, **common_kwargs)
    elif strategy_type == "bb_reversion":
        result = strategy_lib.bb_reversion(candles, **params, **common_kwargs)
    else:
        raise ValueError(f"unsupported strategy type: {strategy_type}")

    return {
        "net_pnl": round(result["net_pnl"], 2),
        "return_pct": round(result["return_pct"], 1),
        "win_rate": round(result["win_rate"], 1),
        "closes": int(result["trades"]),
        "wins": int(result["wins"]),
        "losses": int(result["losses"]),
        "signals": int(result["signals"]),
        "max_dd": round(result["max_drawdown"], 1),
        "total_fees": round(result.get("total_fees", 0.0), 2),
        "engine": "strategy_library",
    }


def filter_usd_coins(products, min_volume_usd=100000):
    """Filter for USD pairs with sufficient volume."""
    usd_coins = []
    for p in products:
        pid = p.get("product_id", "")
        if not pid.endswith("-USD"):
            continue
        if p.get("status") != "online":
            continue
        # Check volume
        vol_24h = p.get("volume_24_h", "0")
        try:
            vol = float(vol_24h)
        except (ValueError, TypeError):
            vol = 0
        if vol < min_volume_usd:
            continue
        usd_coins.append(pid)
    return usd_coins


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def _save_partial(coin_count, total_backtests, profitable_combos, by_strategy):
    """Save incremental results."""
    report = {
        "run_at": utc_now_iso(),
        "partial": True,
        "window_days": WINDOW_DAYS,
        "coins_scanned": coin_count,
        "total_backtests": total_backtests,
        "profitable_combos_count": len(profitable_combos),
        "total_monthly_pnl": round(sum(r["net_pnl"] for r in profitable_combos), 2),
        "profitable_combos": [
            {"coin": r["coin"], "strategy": r["strategy"], "net_pnl": r["net_pnl"],
             "win_rate": r["win_rate"], "closes": r["closes"], "max_dd": r["max_dd"]}
            for r in sorted(profitable_combos, key=lambda x: x["net_pnl"], reverse=True)
        ],
        "by_strategy": {
            s: {"count": len(c), "total_pnl": round(sum(r["net_pnl"] for r in c), 2)}
            for s, c in by_strategy.items()
        },
    }
    PARTIAL_SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PARTIAL_SAVE_PATH, "w") as f:
        json.dump(report, f, indent=2)


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"COINBASE OPPORTUNITY SWEEP — ALL coins, ALL strategies", flush=True)
    print(f"{WINDOW_DAYS}d window, {len(STRATEGIES)} strategies, 40bps flat", flush=True)
    print(f"=" * 70, flush=True)

    # Step 1: Fetch all products
    print(f"\n[1/4] Fetching all Coinbase products...", flush=True)
    try:
        resp = client.list_products(get_all_products=True, limit=1000)
        products = resp.get("products", [])
        print(f"  Total products: {len(products)}", flush=True)
    except Exception as e:
        print(f"  API fetch failed: {e}. Using expanded fallback.", flush=True)
        products = []

    # Step 2: Filter USD coins
    print(f"\n[2/4] Filtering USD pairs with volume > $100K...", flush=True)
    usd_coins = filter_usd_coins(products, min_volume_usd=100000)
    print(f"  Tradeable USD coins: {len(usd_coins)}", flush=True)

    if not usd_coins:
        # Fallback: expanded known coin list
        print("  Using expanded fallback coin list...", flush=True)
        usd_coins = [
            # Microcaps
            "RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD", "MOG-USD",
            "FARTCOIN-USD", "A8-USD", "VVV-USD", "PRL-USD", "COMP-USD",
            "ALEPH-USD", "CFG-USD", "DASH-USD", "CHECK-USD",
            # Mid-caps
            "SOL-USD", "DOGE-USD", "XRP-USD", "PEPE-USD", "WIF-USD",
            "AAVE-USD", "LINK-USD", "UNI-USD", "AVAX-USD", "NEAR-USD",
            "FET-USD", "RENDER-USD", "TIA-USD", "SEI-USD", "SUI-USD",
            "ONDO-USD", "ARB-USD", "OP-USD", "MATIC-USD", "ADA-USD",
            "DOT-USD", "ATOM-USD", "FIL-USD", "APT-USD", "ALGO-USD",
            # More mid/small caps
            "LDO-USD", "INJ-USD", "TIA-USD", "RUNE-USD", "STX-USD",
            "IMX-USD", "GRT-USD", "MKR-USD", "SNX-USD", "AAVE-USD",
            "CRV-USD", "SAND-USD", "MANA-USD", "AXS-USD", "ENS-USD",
            "APE-USD", "SHIB-USD", "BONK-USD", "WLD-USD", "PENDLE-USD",
            "TRX-USD", "FET-USD", "RNDR-USD", "JUP-USD", "PYTH-USD",
            "TIA-USD", "SEI-USD", "SUI-USD", "ORDI-USD", "BOME-USD",
            "WIF-USD", "PEPE-USD", "FLOKI-USD", "MEME-USD", "GALA-USD",
            # Majors
            "BTC-USD", "ETH-USD", "LTC-USD", "BCH-USD", "ETC-USD",
        ]
        # Deduplicate while preserving order
        seen = set()
        unique_coins = []
        for c in usd_coins:
            if c not in seen:
                seen.add(c)
                unique_coins.append(c)
        usd_coins = unique_coins
        print(f"  Fallback coins: {len(usd_coins)}", flush=True)

    # Step 3: Scan each coin
    print(f"\n[3/4] Scanning {len(usd_coins)} coins × {len(STRATEGIES)} strategies = {len(usd_coins)*len(STRATEGIES)} backtests...", flush=True)

    all_results = []
    profitable_combos = []
    coin_count = 0
    total_backtests = 0
    scan_start = time.time()
    by_strategy = {}

    for coin_idx, coin in enumerate(usd_coins, 1):
        coin_count += 1

        # Fetch candles
        try:
            candles = fetch_candles(client, coin, start, now)
        except Exception as e:
            print(f"  [{coin_idx}/{len(usd_coins)}] {coin}: ERROR ({e})", flush=True)
            continue

        if len(candles) < 100:
            continue

        coin_best = None
        coin_results = []

        for strat in STRATEGIES:
            total_backtests += 1
            result = run_strategy_backtest(candles, strat)
            result["strategy"] = strat["name"]
            result["coin"] = coin
            coin_results.append(result)

            if result["net_pnl"] > 0:
                profitable_combos.append(result)
                s = result["strategy"]
                if s not in by_strategy:
                    by_strategy[s] = []
                by_strategy[s].append(result)

                if coin_best is None or result["net_pnl"] > coin_best["net_pnl"]:
                    coin_best = result

        # Progress
        if coin_count % 5 == 0 or coin_best:
            best_strat = coin_best["strategy"] if coin_best else "none"
            best_pnl = coin_best["net_pnl"] if coin_best else 0
            print(f"  [{coin_idx}/{len(usd_coins)}] {coin:<16} | {len(candles):>4} candles | Best: {best_strat:<8} | ${best_pnl:>8.2f}", flush=True)

        # Save incremental results every 10 coins or if running > 8 min
        if coin_count % 10 == 0 or (time.time() - scan_start) > 480:
            _save_partial(coin_count, total_backtests, profitable_combos, by_strategy)

    # Step 4: Report
    print(f"\n{'='*70}", flush=True)
    print(f"RESULTS — {total_backtests} backtests on {coin_count} coins", flush=True)
    print(f"{'='*70}", flush=True)

    profitable_combos.sort(key=lambda r: r["net_pnl"], reverse=True)

    print(f"\n  Total profitable combos: {len(profitable_combos)} / {total_backtests} ({len(profitable_combos)/max(1,total_backtests)*100:.1f}%)", flush=True)

    if profitable_combos:
        total_monthly = sum(r["net_pnl"] for r in profitable_combos)
        print(f"  Total monthly PnL (if all deployed): ${total_monthly:.2f}", flush=True)
        print(f"  Average per profitable combo: ${total_monthly/len(profitable_combos):.2f}", flush=True)

        # By strategy
        print(f"\n  Profitable combos by strategy:", flush=True)
        by_strategy = {}
        for r in profitable_combos:
            s = r["strategy"]
            if s not in by_strategy:
                by_strategy[s] = []
            by_strategy[s].append(r)

        for s, combos in sorted(by_strategy.items(), key=lambda x: sum(r["net_pnl"] for r in x[1]), reverse=True):
            total = sum(r["net_pnl"] for r in combos)
            print(f"    {s:<12}: {len(combos):>3} combos, total ${total:>8.2f}, avg ${total/len(combos):>6.2f}", flush=True)

        # Top 50 profitable combos
        print(f"\n{'='*70}", flush=True)
        print(f"TOP 50 PROFITABLE COMBOS", flush=True)
        print(f"{'='*70}", flush=True)
        print(f"{'Rank':>4} | {'Coin':<16} | {'Strategy':<10} | {'PnL':>8} | {'WR':>5} | {'Trades':>6} | {'DD':>5} | {'Signals':>7}", flush=True)
        print(f"{'-'*4}-+-{'-'*16}-+-{'-'*10}-+-{'-'*8}-+-{'-'*5}-+-{'-'*6}-+-{'-'*5}-+-{'-'*7}", flush=True)

        for i, r in enumerate(profitable_combos[:50], 1):
            print(f"{i:>4} | {r['coin']:<16} | {r['strategy']:<10} | ${r['net_pnl']:>7.2f} | "
                  f"{r['win_rate']:>4.1f}% | {r['closes']:>6} | {r['max_dd']:>4.1f}% | {r['signals']:>7}", flush=True)

        # Coins with multiple profitable strategies
        multi_coins = {}
        for r in profitable_combos:
            c = r["coin"]
            if c not in multi_coins:
                multi_coins[c] = []
            multi_coins[c].append(r)

        multi_profitable = {c: combos for c, combos in multi_coins.items() if len(combos) >= 2}
        if multi_profitable:
            print(f"\n  Coins with MULTIPLE profitable strategies ({len(multi_profitable)} coins):", flush=True)
            for c, combos in sorted(multi_profitable.items(), key=lambda x: sum(r["net_pnl"] for r in x[1]), reverse=True):
                total = sum(r["net_pnl"] for r in combos)
                strat_names = ", ".join(r["strategy"] for r in combos)
                print(f"    {c:<16}: ${total:>8.2f} total — {strat_names}", flush=True)

    else:
        print(f"\n  NO profitable combos found. The strategies don't work on the current coin universe.", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "fee_rate": FEE_RATE,
        "engine": "strategy_library",
        "total_coins_scanned": coin_count,
        "total_backtests": total_backtests,
        "profitable_combos_count": len(profitable_combos),
        "total_monthly_pnl": round(sum(r["net_pnl"] for r in profitable_combos), 2),
        "profitable_combos": [
            {
                "coin": r["coin"],
                "strategy": r["strategy"],
                "net_pnl": r["net_pnl"],
                "return_pct": r["return_pct"],
                "win_rate": r["win_rate"],
                "closes": r["closes"],
                "signals": r["signals"],
                "max_dd": r["max_dd"],
                "total_fees": r["total_fees"],
                "engine": r["engine"],
            }
            for r in profitable_combos
        ],
        "by_strategy": {
            s: {
                "count": len(combos),
                "total_pnl": round(sum(r["net_pnl"] for r in combos), 2),
                "avg_pnl": round(sum(r["net_pnl"] for r in combos) / len(combos), 2),
            }
            for s, combos in by_strategy.items()
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
