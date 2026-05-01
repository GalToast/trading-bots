#!/usr/bin/env python3
"""
Independent RSI validation — replicates @gemini's findings from scratch.

This is a completely separate codebase from gemini's optimizer.
Uses the same optimal params from rsi_optimal_params.json but runs
through our own backtest engine to independently confirm or refute.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "rsi_independent_validation.json"


def fetch_candles_72h(client, product_id, granularity="FIVE_MINUTE"):
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append({
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.06)
    return sorted(all_candles, key=lambda x: x["time"])


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


def validate_rsi_config(candles, product_id, params, starting_cash=24.0, maker_fee_bps=40.0):
    """
    Long-only RSI mean reversion:
    - Enter when RSI crosses below oversold threshold
    - Exit when: TP hit, SL hit, RSI crosses above OB, or max_hold_bars reached
    """
    if len(candles) < params["p"] + 10:
        return {"error": "not enough candles"}

    closes_list = [c["close"] for c in candles]
    rsi_values = rsi(closes_list, params["p"])
    fee_rate = maker_fee_bps / 10000.0

    cash = starting_cash
    in_position = False
    entry_bar = 0
    entry_price = 0.0
    entry_fee = 0.0
    qty = 0.0
    trades = []

    tp_pct = params["t"] / 100.0
    sl_pct = params["s"] / 100.0
    max_hold = params["h"]
    os_level = params["os"]
    ob_level = params["ob"]

    for i in range(params["p"] + 1, len(candles)):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_values[i]

        if in_position:
            # Check exits
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
            bars_held = i - entry_bar

            exit_price = None
            exit_reason = None

            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif l <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
            elif current_rsi >= ob_level:
                exit_price = cl
                exit_reason = "rsi_ob"
            elif bars_held >= max_hold:
                exit_price = cl
                exit_reason = "timeout"

            if exit_reason:
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee

                trades.append({
                    "entry_bar": entry_bar,
                    "exit_bar": i,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "entry_rsi": rsi_values[entry_bar],
                    "exit_rsi": round(current_rsi, 1),
                    "exit_reason": exit_reason,
                    "gross_pnl": round(gross, 4),
                    "fee": round(entry_fee + exit_fee, 4),
                    "net_pnl": round(net, 4),
                    "hold_bars": bars_held,
                })

                in_position = False

        elif not in_position:
            # Check entry: RSI crosses below oversold
            if current_rsi <= os_level:
                deploy = cash
                if deploy >= 1.0:
                    entry_price = cl
                    entry_fee = entry_price * (deploy / entry_price) * fee_rate
                    qty = (deploy - entry_fee) / entry_price
                    if qty > 0:
                        cash -= deploy
                        in_position = True
                        entry_bar = i
                        entry_fee = entry_fee

    # Close any open position at end
    if in_position and len(candles) > 0:
        exit_price = float(candles[-1]["close"])
        gross = (exit_price - entry_price) * qty
        exit_fee = exit_price * qty * fee_rate
        net = gross - entry_fee - exit_fee
        trades.append({
            "entry_bar": entry_bar,
            "exit_bar": len(candles) - 1,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_rsi": rsi_values[entry_bar],
            "exit_rsi": rsi_values[-1],
            "exit_reason": "end_of_data",
            "gross_pnl": round(gross, 4),
            "fee": round(entry_fee + exit_fee, 4),
            "net_pnl": round(net, 4),
            "hold_bars": len(candles) - 1 - entry_bar,
        })

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    tp_exits = [t for t in trades if t["exit_reason"] == "tp"]
    sl_exits = [t for t in trades if t["exit_reason"] == "sl"]
    rsi_exits = [t for t in trades if t["exit_reason"] == "rsi_ob"]
    to_exits = [t for t in trades if t["exit_reason"] == "timeout"]

    return {
        "product_id": product_id,
        "params": params,
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades) / starting_cash * 100, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades) / len(trades), 4) if trades else 0,
        "avg_win": round(sum(t["net_pnl"] for t in wins) / len(wins), 4) if wins else 0,
        "avg_loss": round(sum(t["net_pnl"] for t in losses) / len(losses), 4) if losses else 0,
        "tp_exits": len(tp_exits),
        "sl_exits": len(sl_exits),
        "rsi_ob_exits": len(rsi_exits),
        "timeout_exits": len(to_exits),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
        "median_hold_bars": sorted(t["hold_bars"] for t in trades)[len(trades)//2] if trades else 0,
    }


def main():
    client = CoinbaseAdvancedClient()

    # Load @gemini's optimal params
    params_path = ROOT / "reports" / "rsi_optimal_params.json"
    if not params_path.exists():
        print("ERROR: rsi_optimal_params.json not found")
        return

    optimal_params = json.loads(params_path.read_text(encoding="utf-8"))
    products = list(optimal_params.keys())
    print(f"Validating {len(products)} products with @gemini's optimal params...")

    # Fetch candles
    candles_cache = {}
    for pid in products:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    # Run independent validation
    results = {}
    for pid in products:
        if pid not in candles_cache:
            continue
        params = optimal_params[pid]
        print(f"\n  === {pid} ===")
        print(f"  RSI({params['p']}) OS={params['os']} OB={params['ob']} TP={params['t']}% SL={params['s']}% Hold={params['h']}")

        result = validate_rsi_config(candles_cache[pid], pid, params)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue

        results[pid] = result
        print(f"  ${result['realized_net']:+.2f} ({result['return_pct']:+.1f}%), {result['trades']} trades, {result['win_rate']:.1%} WR")
        print(f"  TP/SL/RSI/TO: {result['tp_exits']}/{result['sl_exits']}/{result['rsi_ob_exits']}/{result['timeout_exits']}")
        print(f"  Avg win ${result['avg_win']:+.4f}, avg loss ${result['avg_loss']:+.4f}")

    # Summary
    profitable = {pid: r for pid, r in results.items() if r.get("realized_net", 0) > 0}
    unprofitable = {pid: r for pid, r in results.items() if r.get("realized_net", 0) <= 0}

    print(f"\n{'='*100}")
    print(f"INDEPENDENT VALIDATION RESULTS")
    print(f"{'='*100}")
    print(f"Profitable: {len(profitable)}/{len(results)}")
    print(f"Unprofitable: {len(unprofitable)}/{len(results)}")

    if profitable:
        print(f"\nPROFITABLE COINS (confirmed edge):")
        for pid, r in sorted(profitable.items(), key=lambda x: x[1]["realized_net"], reverse=True):
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']} trades, {r['win_rate']:.1%} WR")

    if unprofitable:
        print(f"\nUNPROFITABLE COINS (edge not confirmed):")
        for pid, r in sorted(unprofitable.items(), key=lambda x: x[1]["realized_net"]):
            print(f"  {pid:15s}: ${r['realized_net']:+.2f} ({r['return_pct']:+.1f}%), {r['trades']} trades, {r['win_rate']:.1%} WR")

    # Compare with @gemini's results
    gemini_profitable = set(optimal_params.keys())  # All 15 were profitable in gemini's test
    our_profitable = set(profitable.keys())
    match = gemini_profitable & our_profitable
    disagree = gemini_profitable - our_profitable

    print(f"\n{'='*100}")
    print(f"COMPARISON WITH @GEMINI'S FINDINGS:")
    print(f"  @gemini found {len(gemini_profitable)} profitable coins")
    print(f"  We confirmed {len(our_profitable)} profitable coins")
    print(f"  Agreement: {len(match)} coins")
    if disagree:
        print(f"  DISAGREEMENT (gemini profitable, we're not): {disagree}")

    total_net = sum(r["realized_net"] for r in results.values())
    print(f"\n  Total net across all {len(results)} coins: ${total_net:+.2f}")
    print(f"  Total net across profitable {len(profitable)} coins: ${sum(r['realized_net'] for r in profitable.values()):+.2f}")

    # Write report
    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "validator": "qwen-main (independent codebase)",
        "params_source": "gemini-cli-20260411 (rsi_optimal_params.json)",
        "total_products": len(results),
        "profitable_count": len(profitable),
        "unprofitable_count": len(unprofitable),
        "total_net": round(total_net, 4),
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
