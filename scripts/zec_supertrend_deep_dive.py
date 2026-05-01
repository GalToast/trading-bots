#!/usr/bin/env python3
"""
ZEC-USD Supertrend Deep Dive — 30d Backtest + Param Sweep

Base params:
- atr_period=10, atr_mult=3.0
- tp=10%, sl=3%, max_hold=48
- Fee=0.4%, deploy=90%, session gate {0,6,12,19}, $2 min position
- Starting cash: $5.33 and $100

Param sweep:
- atr_mult: 2.0, 2.5, 3.0, 3.5, 4.0
- tp: 5%, 8%, 10%, 15%
- sl: 0%, 2%, 3%, 5%

Total combos: 5 * 4 * 4 = 80 per cash level
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

SESSION_DEAD_HOURS = {0, 6, 12, 19}
BASE_PATH = Path(__file__).parent.parent
CANDLE_CACHE = BASE_PATH / "reports" / "candle_cache"


def load_cached_candles(symbol, timeframe, days):
    """Try to load candles from cache."""
    variants = [
        f"{symbol}_{timeframe}_{days}d.json",
        f"{symbol.replace('-', '_')}_{timeframe}_{days}d.json",
        f"{symbol.replace('-', '_').replace('_USD', '')}_{timeframe}_{days}d.json",
    ]
    for name in variants:
        p = CANDLE_CACHE / name
        if p.exists():
            with open(p) as f:
                raw = json.load(f)
            # The cache wraps candles in a key sometimes
            if isinstance(raw, dict):
                for k in ("candles", "data", "result"):
                    if k in raw:
                        return raw[k]
                # Try first list value
                for v in raw.values():
                    if isinstance(v, list):
                        return v
            if isinstance(raw, list):
                return raw
    return None


def fetch_candles_api(client, product_id, start_ts, end_ts, granularity="FIVE_MINUTE"):
    """Fetch candles from Coinbase API with chunking."""
    chunk_sec = 300 * 5 * 60  # 25 hours per chunk
    all_c = []
    cs = start_ts
    while cs < end_ts:
        ce = min(cs + chunk_sec, end_ts)
        try:
            resp = client.market_candles(product_id, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.15)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_c


def load_zec_candles():
    """Load ZEC-USD candles: try cache 30d, then 7d, then API."""
    # Try 30d cache
    candles = load_cached_candles("ZEC-USD", "FIVE_MINUTE", 30)
    if candles:
        print(f"  Loaded 30d from cache: {len(candles)} candles")
        return candles, "cache_30d"

    # Try 7d cache
    candles = load_cached_candles("ZEC-USD", "FIVE_MINUTE", 7)
    if candles:
        print(f"  Loaded 7d from cache: {len(candles)} candles (limited)")
        return candles, "cache_7d"

    # Fetch 30d from API
    print("  Fetching 30d from API...")
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_ts = now - 30 * 86400
    candles = fetch_candles_api(client, "ZEC-USD", start_ts, now)
    if candles:
        # Save to cache
        CANDLE_CACHE.mkdir(parents=True, exist_ok=True)
        cache_file = CANDLE_CACHE / "ZEC_USD_FIVE_MINUTE_30d.json"
        with open(cache_file, "w") as f:
            json.dump(candles, f)
        print(f"  Fetched {len(candles)} candles, saved to cache")
        return candles, "api_30d"

    return [], "none"


def compute_supertrend_all(candles, period=10, multiplier=3.0):
    """
    Compute Supertrend for all candles.
    Returns list of (trend_line, trend_direction) aligned with candle indices.
    Uses proper Supertrend logic with band persistence.
    """
    n = len(candles)
    if n < period + 2:
        return [(None, None)] * n

    # Calculate True Ranges
    trs = []
    for i in range(1, n):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c_prev = float(candles[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    # ATR with rolling window
    atrs = []
    for i in range(len(trs)):
        if i < period - 1:
            atrs.append(None)
        else:
            atrs.append(sum(trs[i - period + 1:i + 1]) / period)

    # Supertrend bands
    results = [(None, None)]  # First candle has no signal

    # Initialize with first valid ATR
    trend = "bullish"
    final_upper = 0.0
    final_lower = 0.0

    for i in range(len(trs)):
        if atrs[i] is None:
            results.append((None, None))
            continue

        atr = atrs[i]
        mid = (float(candles[i + 1]["high"]) + float(candles[i + 1]["low"])) / 2
        basic_upper = mid + multiplier * atr
        basic_lower = mid - multiplier * atr

        # Persistent bands
        if i > 0 and results[i][0] is not None:
            prev_upper = final_upper
            prev_lower = final_lower
        else:
            prev_upper = basic_upper
            prev_lower = basic_lower

        final_upper = min(basic_upper, prev_upper) if basic_upper < prev_upper else basic_upper
        final_lower = max(basic_lower, prev_lower) if basic_lower > prev_lower else basic_lower

        # Trend flip logic
        close = float(candles[i + 1]["close"])
        prev_close = float(candles[i]["close"]) if i > 0 else close

        if trend == "bearish" and close > final_upper:
            trend = "bullish"
            final_lower = basic_lower
        elif trend == "bullish" and close < final_lower:
            trend = "bearish"
            final_upper = basic_upper
        elif trend == "bullish" and close < final_lower:
            trend = "bearish"
            final_upper = basic_upper
        elif trend == "bearish" and close > final_upper:
            trend = "bullish"
            final_lower = basic_lower

        trend_line = final_lower if trend == "bullish" else final_upper
        results.append((trend_line, trend))

    return results


def supertrend_entry(candles_hist, closes, candle, params):
    """
    Supertrend entry: buy on bearish->bullish flip.
    Also allows continuation entries if trend just flipped within last 3 bars.
    """
    period = params.get("st_period", 10)
    multiplier = params.get("st_multiplier", 3.0)

    if len(candles_hist) < period + 5:
        return False

    # Compute supertrend for all history
    st = compute_supertrend_all(candles_hist, period, multiplier)

    if len(st) < 2:
        return False

    current_trend = st[-1][1]
    if current_trend != "bullish":
        return False

    # Check for recent flip from bearish (within last 5 bars)
    for j in range(max(0, len(st) - 5), len(st)):
        if st[j][1] == "bearish":
            return True

    return False


def backtest_supertrend(candles, atr_period, atr_mult, tp_pct, sl_pct, max_hold,
                        fee_rate=0.004, deploy_pct=0.9, min_position=2.0,
                        starting_cash=100.0, seed=42):
    """
    Dedicated Supertrend backtester.
    - Entry: bearish->bullish flip (checked via supertrend_entry)
    - Exit: TP / SL / max_hold bars
    - Fee on both entry and exit
    - Session gate: skip entries during dead hours {0,6,12,19}
    - Min position size check
    """
    import random
    rng = random.Random(seed)

    cash = starting_cash
    pos = None
    trades = []  # List of dicts with trade details
    peak = starting_cash
    max_dd = 0.0
    signals_count = 0
    signals_filtered = 0
    signal_reasons = {"session": 0, "min_position": 0}
    equity_curve = [cash]
    hold_bars = []

    # Pre-compute supertrend for all candles
    st_all = compute_supertrend_all(candles, atr_period, atr_mult)

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        # Session gate
        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        # EXIT position
        if pos:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = pos["units"]
                gross = (exit_price - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                hold_bars.append(pos["hold"])
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

                trades.append({
                    "entry_idx": pos["entry_idx"],
                    "exit_idx": i,
                    "entry_price": pos["ep"],
                    "exit_price": exit_price,
                    "units": units,
                    "net_pnl": net,
                    "hold_bars": pos["hold"],
                    "exit_reason": exit_reason,
                })
                equity_curve.append(cash)
                pos = None

        # ENTRY
        if pos is None:
            # Build candles_hist up to current point
            candles_hist = candles[:i + 1]
            closes_hist = [float(cc["close"]) for cc in candles_hist]

            params = {
                "st_period": atr_period,
                "st_multiplier": atr_mult,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "max_hold": max_hold,
            }
            signal = supertrend_entry(candles_hist, closes_hist, c, params)

            if signal:
                signals_count += 1

                if not session_open:
                    signals_filtered += 1
                    signal_reasons["session"] += 1
                    continue

                deploy = cash * deploy_pct
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / candle_open
                position_value = units * candle_open

                if position_value < min_position:
                    signals_filtered += 1
                    signal_reasons["min_position"] += 1
                    continue

                tp = candle_open * (1 + tp_pct / 100.0)
                sl = candle_open * (1 - sl_pct / 100.0) if sl_pct > 0 else 0

                cash -= deploy
                pos = {
                    "ep": candle_open,
                    "q": deploy,
                    "hold": 0,
                    "tp": tp,
                    "sl": sl,
                    "units": units,
                    "entry_fee": entry_fee,
                    "max_hold": max_hold,
                    "entry_idx": i,
                }

    # Close remaining position at last candle
    if pos and candles:
        last_close = float(candles[-1]["close"])
        units = pos["units"]
        gross = (last_close - pos["ep"]) * units
        entry_fee = pos["entry_fee"]
        exit_fee = last_close * units * fee_rate
        net = gross - entry_fee - exit_fee
        cash += pos["q"] + net
        hold_bars.append(pos["hold"])
        peak = max(peak, cash)
        dd = (peak - cash) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

        trades.append({
            "entry_idx": pos["entry_idx"],
            "exit_idx": len(candles) - 1,
            "entry_price": pos["ep"],
            "exit_price": last_close,
            "units": units,
            "net_pnl": net,
            "hold_bars": pos["hold"],
            "exit_reason": "end_of_data",
        })
        equity_curve.append(cash)

    # Compute metrics
    total_trades = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / max(total_trades, 1) * 100

    total_pnl = sum(t["net_pnl"] for t in trades)
    net_pnl = cash - starting_cash
    return_pct = net_pnl / starting_cash * 100 if starting_cash > 0 else 0

    avg_hold = sum(hold_bars) / len(hold_bars) if hold_bars else 0

    # PnL per trade
    pnl_per_trade = total_pnl / max(total_trades, 1)

    # Gross profit/loss
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)

    # Sharpe ratio
    if total_trades > 1:
        pnl_values = [t["net_pnl"] for t in trades]
        mean_pnl = sum(pnl_values) / len(pnl_values)
        std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnl_values) / len(pnl_values))
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
    else:
        sharpe = 0

    # Calmar ratio
    calmar = (net_pnl / starting_cash) / max_dd if max_dd > 0 else 0

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        r = t["exit_reason"]
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Session signals breakdown
    fill_rate = total_trades / max(signals_count, 1) * 100

    return {
        "net_pnl": round(net_pnl, 4),
        "return_pct": round(return_pct, 2),
        "total_trades": total_trades,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate, 1),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "total_pnl_from_trades": round(total_pnl, 4),
        "avg_hold_bars": round(avg_hold, 1),
        "pnl_per_trade": round(pnl_per_trade, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999,
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "signals": signals_count,
        "signals_filtered": signals_filtered,
        "fill_rate": round(fill_rate, 1),
        "signal_reasons": signal_reasons,
        "exit_reasons": exit_reasons,
        "final_equity": round(cash, 4),
        "starting_cash": starting_cash,
    }


def run_sweep(candles, atr_period=10, cash_levels=None):
    """
    Full parameter sweep.
    atr_mult: 2.0, 2.5, 3.0, 3.5, 4.0
    tp: 5%, 8%, 10%, 15%
    sl: 0%, 2%, 3%, 5%
    """
    if cash_levels is None:
        cash_levels = [5.33, 100.0]

    atr_mults = [2.0, 2.5, 3.0, 3.5, 4.0]
    tps = [5, 8, 10, 15]
    sls = [0, 2, 3, 5]
    max_hold = 48
    fee_rate = 0.004
    deploy_pct = 0.9
    min_position = 2.0

    combos = list(product(atr_mults, tps, sls))
    total_combos = len(combos) * len(cash_levels)

    print(f"\n  Sweeping {len(combos)} param combos x {len(cash_levels)} cash levels = {total_combos} runs")
    print(f"  atr_mult: {atr_mults}")
    print(f"  tp: {tps}%")
    print(f"  sl: {sls}%")
    print()

    all_results = []
    start = time.time()

    for idx, (am, tp, sl) in enumerate(combos):
        for cash in cash_levels:
            result = backtest_supertrend(
                candles=candles,
                atr_period=atr_period,
                atr_mult=am,
                tp_pct=tp,
                sl_pct=sl,
                max_hold=max_hold,
                fee_rate=fee_rate,
                deploy_pct=deploy_pct,
                min_position=min_position,
                starting_cash=cash,
            )
            result["params"] = {
                "atr_period": atr_period,
                "atr_mult": am,
                "tp_pct": tp,
                "sl_pct": sl,
                "max_hold": max_hold,
            }
            result["cash_level"] = cash
            all_results.append(result)

        if (idx + 1) % 20 == 0:
            elapsed = time.time() - start
            print(f"    Progress: {idx + 1}/{len(combos)} ({elapsed:.1f}s)")

    return all_results


def print_report(label, results, cash_level):
    """Print a formatted report for a set of results."""
    filtered = [r for r in results if r["cash_level"] == cash_level]
    filtered.sort(key=lambda x: x["net_pnl"], reverse=True)

    print(f"\n  {'='*80}")
    print(f"  {label} — Starting Cash ${cash_level}")
    print(f"  {'='*80}")

    if not filtered:
        print("    No results.")
        return

    # Top 10 by PnL
    print(f"\n  TOP 10 BY NET PnL:")
    print(f"  {'Mult':<6} {'TP%':<5} {'SL%':<5} {'PnL$':<10} {'Ret%':<8} {'WR%':<6} {'Trades':<7} {'MaxDD%':<8} {'Sharpe':<8} {'Calmar':<8}")
    print(f"  {'-'*80}")
    for r in filtered[:10]:
        p = r["params"]
        print(f"  {p['atr_mult']:<6} {p['tp_pct']:<5} {p['sl_pct']:<5} ${r['net_pnl']:>8.4f}  {r['return_pct']:>6.2f}%  {r['win_rate']:>5.1f}%  {r['total_trades']:>5}  {r['max_drawdown_pct']:>6.2f}%  {r['sharpe']:>6.4f}  {r['calmar']:>6.4f}")

    # Best config
    best = filtered[0]
    bp = best["params"]
    print(f"\n  BEST CONFIG:")
    print(f"    atr_period={bp['atr_period']}, atr_mult={bp['atr_mult']}, tp={bp['tp_pct']}%, sl={bp['sl_pct']}%, max_hold={bp['max_hold']}")
    print(f"    Net PnL: ${best['net_pnl']:.4f} ({best['return_pct']:.2f}%)")
    print(f"    Win Rate: {best['win_rate']}% ({best['wins']}W / {best['losses']}L)")
    print(f"    Total Trades: {best['total_trades']}")
    print(f"    Signals: {best['signals']} (filtered: {best['signals_filtered']})")
    print(f"    Max Drawdown: {best['max_drawdown_pct']}%")
    print(f"    Avg Hold Bars: {best['avg_hold_bars']}")
    print(f"    PnL/Trade: ${best['pnl_per_trade']:.4f}")
    print(f"    Profit Factor: {best['profit_factor']}")
    print(f"    Sharpe: {best['sharpe']:.4f}")
    print(f"    Calmar: {best['calmar']:.4f}")
    print(f"    Exit Reasons: {best['exit_reasons']}")
    print(f"    Signal Reasons: {best['signal_reasons']}")

    # Bottom 5 (worst)
    print(f"\n  BOTTOM 5 (WORST):")
    for r in filtered[-5:]:
        p = r["params"]
        print(f"    Mult={p['atr_mult']}, TP={p['tp_pct']}%, SL={p['sl_pct']}% -> PnL=${r['net_pnl']:.4f}, WR={r['win_rate']}%, Trades={r['total_trades']}")

    # Stats summary
    profitable = [r for r in filtered if r["net_pnl"] > 0]
    print(f"\n  SUMMARY: {len(profitable)}/{len(filtered)} configs profitable ({len(profitable)/len(filtered)*100:.1f}%)")
    if profitable:
        avg_pnl = sum(r["net_pnl"] for r in profitable) / len(profitable)
        print(f"    Avg profitable PnL: ${avg_pnl:.4f}")


def main():
    start_time = time.time()
    print(f"\n{'='*80}")
    print(f"ZEC-USD SUPERTREND DEEP DIVE — 30D BACKTEST + PARAM SWEEP")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*80}\n")

    # Load candles
    print("Loading ZEC-USD candle data...")
    candles, source = load_zec_candles()
    if not candles:
        print("ERROR: Could not load ZEC-USD candle data.")
        print("  Check cache at: reports/candle_cache/")
        print("  Or ensure Coinbase API credentials are configured.")
        return

    # Data stats
    first_ts = int(candles[0].get("start", candles[0].get("time", 0)))
    last_ts = int(candles[-1].get("start", candles[-1].get("time", 0)))
    duration_days = (last_ts - first_ts) / 86400
    print(f"  Candles: {len(candles)}")
    print(f"  Period: {duration_days:.1f} days")
    print(f"  First: {datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    print(f"  Last:  {datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    print(f"  Price range: ${float(candles[0]['close']):.2f} -> ${float(candles[-1]['close']):.2f}")
    print(f"  Source: {source}")

    # ---- BASE CONFIG ----
    print(f"\n{'='*80}")
    print(f"BASE CONFIG: atr_period=10, atr_mult=3.0, tp=10%, sl=3%, max_hold=48")
    print(f"{'='*80}")

    for cash in [5.33, 100.0]:
        result = backtest_supertrend(
            candles=candles,
            atr_period=10,
            atr_mult=3.0,
            tp_pct=10,
            sl_pct=3,
            max_hold=48,
            fee_rate=0.004,
            deploy_pct=0.9,
            min_position=2.0,
            starting_cash=cash,
        )
        print(f"\n  Starting Cash: ${cash}")
        print(f"    Net PnL: ${result['net_pnl']:.4f} ({result['return_pct']:.2f}%)")
        print(f"    Win Rate: {result['win_rate']}% ({result['wins']}W / {result['losses']}L)")
        print(f"    Total Trades: {result['total_trades']}")
        print(f"    Signals: {result['signals']} (filtered: {result['signals_filtered']})")
        print(f"    Max Drawdown: {result['max_drawdown_pct']}%")
        print(f"    Avg Hold Bars: {result['avg_hold_bars']}")
        print(f"    PnL/Trade: ${result['pnl_per_trade']:.4f}")
        print(f"    Profit Factor: {result['profit_factor']}")
        print(f"    Sharpe: {result['sharpe']:.4f}")
        print(f"    Exit Reasons: {result['exit_reasons']}")

    # ---- PARAM SWEEP ----
    all_results = run_sweep(candles, atr_period=10, cash_levels=[5.33, 100.0])

    # Reports
    print_report("PARAM SWEEP RESULTS", all_results, 5.33)
    print_report("PARAM SWEEP RESULTS", all_results, 100.0)

    # ---- BEST OVERALL (balanced score) ----
    for r in all_results:
        if r["max_drawdown_pct"] > 0 and r["total_trades"] > 0:
            r["score"] = r["net_pnl"] * r["sharpe"] / (r["max_drawdown_pct"] / 100)
        else:
            r["score"] = 0

    for cash in [5.33, 100.0]:
        cash_results = [r for r in all_results if r["cash_level"] == cash and r["total_trades"] > 0]
        cash_results.sort(key=lambda x: x["score"], reverse=True)
        if cash_results:
            best = cash_results[0]
            bp = best["params"]
            print(f"\n  {'='*80}")
            print(f"  BEST BALANCED (PnL * Sharpe / DD) — Cash ${cash}")
            print(f"  {'='*80}")
            print(f"    atr_period={bp['atr_period']}, atr_mult={bp['atr_mult']}, tp={bp['tp_pct']}%, sl={bp['sl_pct']}%, max_hold={bp['max_hold']}")
            print(f"    Score: {best['score']:.4f}")
            print(f"    Net PnL: ${best['net_pnl']:.4f} ({best['return_pct']:.2f}%)")
            print(f"    Win Rate: {best['win_rate']}% ({best['wins']}W / {best['losses']}L)")
            print(f"    Total Trades: {best['total_trades']}")
            print(f"    Max Drawdown: {best['max_drawdown_pct']}%")
            print(f"    Avg Hold Bars: {best['avg_hold_bars']}")
            print(f"    PnL/Trade: ${best['pnl_per_trade']:.4f}")
            print(f"    Sharpe: {best['sharpe']:.4f}")
            print(f"    Calmar: {best['calmar']:.4f}")

    # ---- SAVE RESULTS ----
    elapsed = time.time() - start_time

    # Find best configs per cash level
    best_configs = {}
    for cash in [5.33, 100.0]:
        cash_results = [r for r in all_results if r["cash_level"] == cash]
        cash_results.sort(key=lambda x: x["net_pnl"], reverse=True)
        if cash_results:
            best_configs[str(cash)] = {
                "by_pnl": cash_results[0]["params"],
                "by_pnl_metrics": {k: v for k, v in cash_results[0].items() if k != "params"},
            }
        cash_results.sort(key=lambda x: x["score"], reverse=True)
        if cash_results:
            best_configs[str(cash)]["balanced"] = cash_results[0]["params"]
            best_configs[str(cash)]["balanced_metrics"] = {k: v for k, v in cash_results[0].items() if k != "params"}

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "symbol": "ZEC-USD",
        "data_source": source,
        "candles_count": len(candles),
        "duration_days": round(duration_days, 1),
        "price_range": {
            "first": float(candles[0]["close"]),
            "last": float(candles[-1]["close"]),
            "high": max(float(c["high"]) for c in candles),
            "low": min(float(c["low"]) for c in candles),
        },
        "base_config": {
            "atr_period": 10,
            "atr_mult": 3.0,
            "tp_pct": 10,
            "sl_pct": 3,
            "max_hold": 48,
            "fee_rate": 0.004,
            "deploy_pct": 0.9,
            "min_position": 2.0,
            "session_gate": [0, 6, 12, 19],
        },
        "sweep_ranges": {
            "atr_mult": [2.0, 2.5, 3.0, 3.5, 4.0],
            "tp_pct": [5, 8, 10, 15],
            "sl_pct": [0, 2, 3, 5],
        },
        "total_combos_tested": len(all_results),
        "best_configs": best_configs,
        "all_results": all_results,
    }

    out_path = BASE_PATH / "reports" / "zec_supertrend_deep_dive.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*80}")
    print(f"COMPLETE in {elapsed:.0f}s")
    print(f"Results saved: {out_path}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
