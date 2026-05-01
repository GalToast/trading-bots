#!/usr/bin/env python3
"""
Super System v3 — Pushing the rotation + BB+RSI champion to its limits.

Current champion: top5_bb_rsi = +$21.30/72h (44.4%), 65.6% WR

Optimization vectors:
1. TP/SL ratio optimization (wider TP, tighter SL)
2. Partial deployment (70-90% instead of 100%)
3. Cooldown after loss (skip N bars after a losing trade)
4. Regime filter (only trade when 3+ coins oversold = market-wide dip)
5. Time-of-day filter on rotation (skip low-vol hours)
6. More granular coin pools (test every 3-coin combo)
7. Dynamic coin selection (rotate pool based on recent volatility)
8. Asymmetric exits (different TP for different coins)
"""
import json, time, datetime
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "super_system_v3.json"

ALL_PRODUCTS = [
    "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "TROLL-USD", "NOM-USD", "CFG-USD", "DASH-USD", "IRYS-USD",
    "FARTCOIN-USD", "BOBBOB-USD", "MON-USD", "ZEC-USD", "VVV-USD",
]


def fetch_candles_72h(client, pid, granularity="FIVE_MINUTE"):
    gsec = 300
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_c = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(pid, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles", [])
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_c.append({"time": t, "open": float(c["open"]), "high": float(c["high"]),
                              "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0))})
        chunk_end = chunk_start - 1
        time.sleep(0.05)
    return sorted(all_c, key=lambda x: x["time"])


def rsi(closes, period):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result


def run_optimized_rotation(candles_by_pid, params, starting_cash=48.0, fee_rate=0.004,
                           tp_pct=0.05, sl_pct=0.03, deploy_pct=1.0,
                           cooldown_bars=0, regime_filter_count=0,
                           time_filter_start=None, time_filter_end=None):
    """
    Optimized rotation system with advanced features.
    """
    products = list(candles_by_pid.keys())
    if len(products) < 2:
        return {"error": "need at least 2 coins"}

    all_times = set()
    time_lookup = {}
    for pid, candles in candles_by_pid.items():
        for c in candles:
            t = int(c["time"])
            all_times.add(t)
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c
    all_times = sorted(all_times)

    cash = starting_cash
    in_position = False
    position_pid = None
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    price_history = {pid: [] for pid in products}
    volume_history = {pid: [] for pid in products}
    trades = []
    last_loss_bar = -999  # For cooldown

    for t in all_times:
        tick = time_lookup.get(t, {})

        # Update histories
        for pid in products:
            if pid in tick:
                c = tick[pid]
                cl = float(c["close"])
                vol = float(c["volume"])
                price_history[pid].append(cl)
                volume_history[pid].append(vol)
                if len(price_history[pid]) > 100:
                    price_history[pid] = price_history[pid][-100:]
                    volume_history[pid] = volume_history[pid][-100:]

        # Exit
        if in_position and position_pid in tick:
            c = tick[position_pid]
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])

            p = params.get(position_pid, {})
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
            
            ph = price_history[position_pid]
            rsi_val = rsi(ph, p.get("p", 7))[-1] if len(ph) > p.get("p", 7) else 50
            
            exit_price = None
            if h >= tp:
                exit_price = tp
            elif l <= sl:
                exit_price = sl
            elif rsi_val >= p.get("ob", 75):
                exit_price = cl
            elif t - entry_bar >= 12 * 300:
                exit_price = cl

            if exit_price is not None:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({
                    "pid": position_pid, "net_pnl": round(net, 4),
                    "fee": round(entry_fee + exit_fee, 4),
                    "hold_bars": (t - entry_bar) // 300,
                    "exit_reason": "tp" if exit_price == tp else ("sl" if exit_price == sl else "other"),
                })
                if net <= 0:
                    last_loss_bar = t
                in_position = False
                position_pid = None

        # Entry: find best signal with BB+RSI confluence
        if not in_position:
            # Cooldown check
            if cooldown_bars > 0 and t - last_loss_bar < cooldown_bars * 300:
                continue

            # Time filter
            if time_filter_start is not None or time_filter_end is not None:
                dt = datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
                hour = dt.hour
                if time_filter_start is not None and hour < time_filter_start:
                    continue
                if time_filter_end is not None and hour > time_filter_end:
                    continue

            # Regime filter: count oversold coins
            if regime_filter_count > 0:
                oversold_count = 0
                for pid in products:
                    if pid in params:
                        ph = price_history[pid]
                        if len(ph) > 20:
                            closes = ph[-20:]
                            sma = sum(closes) / len(closes)
                            std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
                            lower_bb = sma - 2 * std
                            rsi_val = rsi(ph, 7)[-1]
                            curr_price = ph[-1]
                            if curr_price <= lower_bb * 1.005 and rsi_val < 30:
                                oversold_count += 1
                if oversold_count < regime_filter_count:
                    continue

            best_pid = None
            best_signal_strength = 999

            for pid in products:
                if pid not in tick or pid not in params:
                    continue
                p = params[pid]
                ph = price_history[pid]
                if len(ph) < 20:
                    continue

                # BB + RSI confluence
                closes = ph[-20:]
                sma = sum(closes) / len(closes)
                std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
                lower_bb = sma - 2 * std
                rsi_val = rsi(ph, 7)[-1]
                curr_price = ph[-1]

                if curr_price <= lower_bb * 1.005 and rsi_val < 30:
                    signal_strength = rsi_val  # Lower RSI = stronger signal
                    if signal_strength < best_signal_strength:
                        best_signal_strength = signal_strength
                        best_pid = pid

            # Enter on best signal
            if best_pid and cash >= 1.0:
                cl = float(tick[best_pid]["close"])
                entry_price = cl
                deploy = cash * deploy_pct
                entry_fee = entry_price * (deploy / entry_price) * fee_rate
                qty = (deploy - entry_fee) / entry_price
                if qty > 0:
                    cash -= deploy
                    in_position = True
                    position_pid = best_pid
                    entry_bar = t
                    entry_fee = entry_fee

    # Close any open position
    if in_position and position_pid in candles_by_pid:
        candles = candles_by_pid[position_pid]
        if candles:
            exit_price = float(candles[-1]["close"])
            gross = (exit_price - entry_price) * qty
            exit_fee = exit_price * qty * fee_rate
            net = gross - entry_fee - exit_fee
            trades.append({
                "pid": position_pid, "net_pnl": round(net, 4),
                "fee": round(entry_fee + exit_fee, 4),
                "hold_bars": (t - entry_bar) // 300,
                "exit_reason": "end",
            })
            cash += exit_price * qty - exit_fee

    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades) / starting_cash * 100, 2),
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / max(1, len(trades)), 3),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades) / max(1, len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
        "median_hold_bars": sorted(t["hold_bars"] for t in trades)[len(trades)//2] if trades else 0,
    }


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles...")
    candles_cache = {}
    for pid in ALL_PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    params_path = ROOT / "reports" / "rsi_optimal_params.json"
    all_params = json.loads(params_path.read_text(encoding="utf-8"))

    # Top 5 coin pool
    top5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    top5_params = {pid: all_params[pid] for pid in top5 if pid in all_params}
    top5_candles = {pid: candles_cache[pid] for pid in top5 if pid in candles_cache}

    all_results = {}
    config_count = 0

    print(f"\n{'='*150}")
    print(f"{'Config':70s} {'Net $':>8} {'Ret%':>7} {'Trades':>6} {'WR':>6} {'Avg/Tr':>8} {'Fees':>8}")
    print(f"{'='*150}")

    # 1. TP/SL optimization
    for tp in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]:
        for sl in [0.01, 0.015, 0.02, 0.025, 0.03]:
            config_count += 1
            name = f"tp{tp*100:.0f}_sl{sl*100:.1f}"
            r = run_optimized_rotation(top5_candles, top5_params, tp_pct=tp, sl_pct=sl)
            if "error" not in r:
                all_results[name] = r
                if r["realized_net"] > 15:  # Only show good configs
                    print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 2. Partial deployment
    for dep in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]:
        config_count += 1
        name = f"deploy{dep*100:.0f}pct"
        r = run_optimized_rotation(top5_candles, top5_params, deploy_pct=dep)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 15:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 3. Cooldown after loss
    for cooldown in [1, 2, 3, 5, 8, 12]:
        config_count += 1
        name = f"cooldown{cooldown}"
        r = run_optimized_rotation(top5_candles, top5_params, cooldown_bars=cooldown)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 15:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 4. Regime filter
    for reg_count in [2, 3, 4, 5]:
        config_count += 1
        name = f"regime{reg_count}"
        r = run_optimized_rotation(top5_candles, top5_params, regime_filter_count=reg_count)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 15:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 5. Time filter
    for start_h, end_h in [(8, 20), (12, 22), (14, 22), (16, 24), (0, 12), (0, 8), (20, 24)]:
        config_count += 1
        name = f"time{start_h:02d}-{end_h:02d}"
        r = run_optimized_rotation(top5_candles, top5_params, time_filter_start=start_h if start_h > 0 else None,
                                      time_filter_end=end_h if end_h < 24 else None)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 15:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 6. Combined optimizations (best from each category)
    combos = [
        {"name": "tp6_sl2", "tp_pct": 0.06, "sl_pct": 0.02},
        {"name": "tp6_sl2_dep90", "tp_pct": 0.06, "sl_pct": 0.02, "deploy_pct": 0.9},
        {"name": "tp6_sl2_cooldown2", "tp_pct": 0.06, "sl_pct": 0.02, "cooldown_bars": 2},
        {"name": "tp6_sl2_dep90_cooldown2", "tp_pct": 0.06, "sl_pct": 0.02, "deploy_pct": 0.9, "cooldown_bars": 2},
        {"name": "tp8_sl2", "tp_pct": 0.08, "sl_pct": 0.02},
        {"name": "tp8_sl2_dep90", "tp_pct": 0.08, "sl_pct": 0.02, "deploy_pct": 0.9},
        {"name": "tp10_sl2", "tp_pct": 0.10, "sl_pct": 0.02},
        {"name": "tp10_sl2_dep90", "tp_pct": 0.10, "sl_pct": 0.02, "deploy_pct": 0.9},
        {"name": "tp6_sl15", "tp_pct": 0.06, "sl_pct": 0.015},
        {"name": "tp6_sl15_dep90", "tp_pct": 0.06, "sl_pct": 0.015, "deploy_pct": 0.9},
    ]

    for combo in combos:
        config_count += 1
        name = combo.pop("name")
        r = run_optimized_rotation(top5_candles, top5_params, **combo)
        if "error" not in r:
            all_results[name] = r
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # Top 20
    print(f"\n{'='*150}")
    print(f"TOP 20 CONFIGURATIONS:")
    print(f"{'='*150}")
    sorted_configs = sorted(all_results.items(), key=lambda x: x[1].get("realized_net", -999), reverse=True)
    for i, (name, r) in enumerate(sorted_configs[:20]):
        print(f"{i+1:>2}. {name:68s} ${r['realized_net']:>6.2f} ({r['return_pct']:>5.1f}%) {r['trades']:3d}c {r['win_rate']:.1%} WR ${r['avg_net_per_trade']:+.4f}/tr")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_configs": config_count,
        "top_20": sorted_configs[:20],
        "all_results": all_results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
