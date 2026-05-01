#!/usr/bin/env python3
"""
Super System v4 — Pushing past $40/72h.

Champion: rotation + BB+RSI + 10% TP = +$32.70/72h

Remaining optimization vectors:
1. More coins in rotation (10-15 instead of 5)
2. Regime filter (only trade when 3+ coins oversold)
3. Wider entry criteria (BB 1.5x std, RSI < 35)
4. Per-coin TP/SL (customize exits)
5. Dynamic coin pool (rotate based on recent vol)
6. Volume confirmation on entry
"""
import json, time, datetime
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "super_system_v4.json"

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


def run_rotation_v4(candles_by_pid, params, starting_cash=48.0, fee_rate=0.004,
                    tp_pct=0.10, sl_pct=0.025, bb_mult=2.0, rsi_threshold=30,
                    regime_filter=0, volume_confirmation=False, vol_mult=1.5):
    """
    Rotation system with all remaining optimization vectors.
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

            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
            
            ph = price_history[position_pid]
            rsi_val = rsi(ph, 7)[-1] if len(ph) > 7 else 50
            
            exit_price = None
            if h >= tp:
                exit_price = tp
            elif l <= sl:
                exit_price = sl
            elif rsi_val >= 75:
                exit_price = cl
            elif t - entry_bar >= 12 * 300:
                exit_price = cl

            if exit_price is not None:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({"pid": position_pid, "net_pnl": round(net, 4),
                               "fee": round(entry_fee + exit_fee, 4), "hold_bars": (t - entry_bar) // 300})
                in_position = False
                position_pid = None

        # Entry
        if not in_position:
            # Regime filter: count oversold coins
            if regime_filter > 0:
                oversold_count = 0
                for pid in products:
                    ph = price_history[pid]
                    if len(ph) < 20:
                        continue
                    closes = ph[-20:]
                    sma = sum(closes) / len(closes)
                    std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
                    lower_bb = sma - bb_mult * std
                    rsi_val = rsi(ph, 7)[-1]
                    if ph[-1] <= lower_bb * 1.005 and rsi_val < rsi_threshold:
                        oversold_count += 1
                if oversold_count < regime_filter:
                    continue

            # Find best signal
            best_pid = None
            best_rsi = 999

            for pid in products:
                if pid not in tick or pid not in params:
                    continue
                ph = price_history[pid]
                if len(ph) < 20:
                    continue

                closes = ph[-20:]
                sma = sum(closes) / len(closes)
                std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
                lower_bb = sma - bb_mult * std
                rsi_val = rsi(ph, 7)[-1]
                curr_price = ph[-1]

                if curr_price <= lower_bb * 1.005 and rsi_val < rsi_threshold:
                    # Volume confirmation
                    if volume_confirmation:
                        vh = volume_history[pid]
                        if len(vh) >= 20:
                            avg_vol = sum(vh[-20:]) / 20
                            curr_vol = vh[-1]
                            if avg_vol <= 0 or curr_vol < avg_vol * vol_mult:
                                continue

                    if rsi_val < best_rsi:
                        best_rsi = rsi_val
                        best_pid = pid

            if best_pid and cash >= 1.0:
                cl = float(tick[best_pid]["close"])
                entry_price = cl
                deploy = cash
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
            trades.append({"pid": position_pid, "net_pnl": round(net, 4),
                           "fee": round(entry_fee + exit_fee, 4), "hold_bars": (t - entry_bar) // 300})
            cash += exit_price * qty - exit_fee

    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    return {
        "starting_cash": starting_cash, "ending_cash": round(cash, 2),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades) / starting_cash * 100, 2),
        "trades": len(trades), "wins": wins, "losses": len(trades) - wins,
        "win_rate": round(wins / max(1, len(trades)), 3),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades) / max(1, len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
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

    all_results = {}

    # Define coin pools
    pools = {
        "top5": ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"],
        "top10": ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
                  "TROLL-USD", "NOM-USD", "CFG-USD", "DASH-USD", "IRYS-USD"],
        "top15": ALL_PRODUCTS,
    }

    print(f"\n{'='*150}")
    print(f"{'Config':70s} {'Net $':>8} {'Ret%':>7} {'Trades':>6} {'WR':>6} {'Avg/Tr':>8} {'Fees':>8}")
    print(f"{'='*150}")

    config_count = 0

    # 1. More coins
    for pool_name, pool_coins in pools.items():
        pool_params = {pid: all_params[pid] for pid in pool_coins if pid in all_params}
        pool_candles = {pid: candles_cache[pid] for pid in pool_coins if pid in candles_cache}
        
        config_count += 1
        name = f"pool_{pool_name}"
        r = run_rotation_v4(pool_candles, pool_params)
        if "error" not in r:
            all_results[name] = r
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 2. Regime filter
    for pool_name, pool_coins in pools.items():
        pool_params = {pid: all_params[pid] for pid in pool_coins if pid in all_params}
        pool_candles = {pid: candles_cache[pid] for pid in pool_coins if pid in candles_cache}
        
        for reg in [2, 3, 4]:
            config_count += 1
            name = f"pool_{pool_name}_regime{reg}"
            r = run_rotation_v4(pool_candles, pool_params, regime_filter=reg)
            if "error" not in r and r["realized_net"] > 20:
                all_results[name] = r
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 3. Wider entry criteria
    for pool_name, pool_coins in pools.items():
        pool_params = {pid: all_params[pid] for pid in pool_coins if pid in all_params}
        pool_candles = {pid: candles_cache[pid] for pid in pool_coins if pid in candles_cache}
        
        for bb_mult in [1.5, 1.8]:
            for rsi_thresh in [35, 40]:
                config_count += 1
                name = f"pool_{pool_name}_bb{bb_mult}_rsi{rsi_thresh}"
                r = run_rotation_v4(pool_candles, pool_params, bb_mult=bb_mult, rsi_threshold=rsi_thresh)
                if "error" not in r and r["realized_net"] > 20:
                    all_results[name] = r
                    print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 4. Volume confirmation
    for pool_name, pool_coins in pools.items():
        pool_params = {pid: all_params[pid] for pid in pool_coins if pid in all_params}
        pool_candles = {pid: candles_cache[pid] for pid in pool_coins if pid in candles_cache}
        
        for vol_mult in [1.5, 2.0, 2.5]:
            config_count += 1
            name = f"pool_{pool_name}_vol{vol_mult}"
            r = run_rotation_v4(pool_candles, pool_params, volume_confirmation=True, vol_mult=vol_mult)
            if "error" not in r and r["realized_net"] > 20:
                all_results[name] = r
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # 5. Wider TP (12-15%)
    for tp in [0.12, 0.15, 0.20]:
        for sl in [0.02, 0.025, 0.03]:
            pool_coins = pools["top5"]
            pool_params = {pid: all_params[pid] for pid in pool_coins if pid in all_params}
            pool_candles = {pid: candles_cache[pid] for pid in pool_coins if pid in candles_cache}
            
            config_count += 1
            name = f"tp{tp*100:.0f}_sl{sl*100:.1f}"
            r = run_rotation_v4(pool_candles, pool_params, tp_pct=tp, sl_pct=sl)
            if "error" not in r:
                all_results[name] = r
                if r["realized_net"] > 25:
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
