#!/usr/bin/env python3
"""
Super System v2 — Combining Multi-Coin Rotation with best entry signals.

Rotation is the winner (+$10.48/72h, 83.3% WR). Let's improve it:
1. Rotation + Stochastic RSI entry (stoch RSI won on RAVE with 73.4% WR)
2. Rotation + BB confluence entry (BB+RSI was +$1.51 across 4 coins)
3. Rotation with more coins (10-15 instead of 5)
4. Rotation with different exit strategies (trailing vs fixed)
5. Rotation with volume confirmation (filter out weak signals)
"""
import json, time, datetime
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "super_system_v2.json"

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


def stochastic_rsi(closes, rsi_period=14, stoch_period=14):
    rsi_vals = rsi(closes, rsi_period)
    if len(rsi_vals) < stoch_period + 1:
        return [50.0] * len(rsi_vals)
    result = []
    for i in range(len(rsi_vals)):
        if i < stoch_period:
            result.append(50.0)
        else:
            window = rsi_vals[i-stoch_period:i+1]
            lowest = min(window)
            highest = max(window)
            if highest == lowest:
                result.append(50.0)
            else:
                result.append((rsi_vals[i] - lowest) / (highest - lowest) * 100)
    return result


def run_rotation_system(candles_by_pid, params, starting_cash=48.0, fee_rate=0.004,
                        signal_type="rsi", trailing_stop=False, volume_filter=False):
    """
    Multi-coin rotation system with configurable entry signal.
    
    signal_type: "rsi", "stoch_rsi", "bb_rsi", "rsi+volume"
    trailing_stop: use trailing stop instead of fixed TP
    volume_filter: only enter when volume > 1.5x average
    """
    products = list(candles_by_pid.keys())
    if len(products) < 2:
        return {"error": "need at least 2 coins"}

    # Build timeline
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
    trail_high = 0
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

            p = params.get(position_pid, {})
            
            if trailing_stop:
                # Update trail
                if h > trail_high:
                    trail_high = h
                # Trail at 2% below highest
                trail_stop = trail_high * 0.98
                # Hard stop at 3% below entry
                hard_sl = entry_price * 0.97
                
                exit_price = None
                if l <= trail_stop:
                    exit_price = trail_stop
                elif l <= hard_sl:
                    exit_price = hard_sl
                # Timeout after 12 bars
                elif t - entry_bar >= 12 * 300:  # 12 bars * 5 min
                    exit_price = cl
            else:
                tp = entry_price * (1 + p.get("t", 5.0) / 100.0)
                sl = entry_price * (1 - p.get("s", 3.0) / 100.0)
                
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
                })
                in_position = False
                position_pid = None
                trail_high = 0

        # Entry: find best signal
        if not in_position:
            best_pid = None
            best_signal_strength = 999  # Lower = more oversold

            for pid in products:
                if pid not in tick or pid not in params:
                    continue
                p = params[pid]
                ph = price_history[pid]
                if len(ph) < 30:
                    continue

                signal_strength = None

                if signal_type == "rsi":
                    rsi_val = rsi(ph, p.get("p", 7))[-1]
                    if rsi_val <= p.get("os", 30):
                        signal_strength = rsi_val

                elif signal_type == "stoch_rsi":
                    stoch = stochastic_rsi(ph, 14, 14)
                    stoch_val = stoch[-1]
                    if stoch_val <= 20:
                        signal_strength = stoch_val

                elif signal_type == "bb_rsi":
                    # BB + RSI confluence
                    if len(ph) >= 20:
                        closes = ph[-20:]
                        sma = sum(closes) / len(closes)
                        std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
                        lower_bb = sma - 2 * std
                        rsi_val = rsi(ph, 7)[-1]
                        curr_price = ph[-1]
                        if curr_price <= lower_bb * 1.005 and rsi_val < 30:
                            signal_strength = rsi_val  # Use RSI as strength metric

                elif signal_type == "rsi_volume":
                    rsi_val = rsi(ph, p.get("p", 7))[-1]
                    if rsi_val <= p.get("os", 30):
                        # Check volume
                        vh = volume_history[pid]
                        if len(vh) >= 20:
                            avg_vol = sum(vh[-20:]) / 20
                            curr_vol = vh[-1]
                            if avg_vol > 0 and curr_vol > avg_vol * 1.5:
                                signal_strength = rsi_val

                if signal_strength is not None and signal_strength < best_signal_strength:
                    best_signal_strength = signal_strength
                    best_pid = pid

            # Enter on best signal
            if best_pid and cash >= 1.0:
                cl = float(tick[best_pid]["close"])
                entry_price = cl
                deploy = cash  # Full rotation
                entry_fee = entry_price * (deploy / entry_price) * fee_rate
                qty = (deploy - entry_fee) / entry_price
                if qty > 0:
                    cash -= deploy
                    in_position = True
                    position_pid = best_pid
                    entry_bar = t
                    trail_high = entry_price
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

    all_results = {}

    # Test different coin pools
    coin_pools = {
        "top5": ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"],
        "top10": ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
                  "TROLL-USD", "NOM-USD", "CFG-USD", "DASH-USD", "IRYS-USD"],
        "top15": ALL_PRODUCTS,
        "volatile_only": ["RAVE-USD", "TROLL-USD", "NOM-USD", "FARTCOIN-USD", "MON-USD"],
    }

    # Test different signal types
    signal_types = ["rsi", "stoch_rsi", "bb_rsi", "rsi_volume"]
    
    # Test trailing stops
    trailing_options = [False, True]

    print(f"\n{'='*140}")
    print(f"{'Config':60s} {'Net $':>8} {'Ret%':>7} {'Trades':>6} {'WR':>6} {'Avg/Tr':>8} {'Fees':>8}")
    print(f"{'='*140}")

    config_count = 0
    for pool_name, pool_coins in coin_pools.items():
        pool_params = {pid: all_params[pid] for pid in pool_coins if pid in all_params}
        pool_candles = {pid: candles_cache[pid] for pid in pool_coins if pid in candles_cache}
        
        if len(pool_candles) < 2:
            continue

        for sig_type in signal_types:
            for trailing in trailing_options:
                config_count += 1
                config_name = f"{pool_name}_{sig_type}{'_trail' if trailing else ''}"
                
                r = run_rotation_system(pool_candles, pool_params, signal_type=sig_type, trailing_stop=trailing)
                
                if "error" not in r:
                    all_results[config_name] = r
                    label = f"{config_name}"
                    print(f"{label:60s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f} ${r['total_fees']:>6.2f}")

    # Top 20 configs
    print(f"\n{'='*140}")
    print(f"TOP 20 CONFIGURATIONS:")
    print(f"{'='*140}")
    sorted_configs = sorted(all_results.items(), key=lambda x: x[1].get("realized_net", -999), reverse=True)
    for i, (name, r) in enumerate(sorted_configs[:20]):
        print(f"{i+1:>2}. {name:58s} ${r['realized_net']:>6.2f} ({r['return_pct']:>5.1f}%) {r['trades']:3d}c {r['win_rate']:.1%} WR")

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
