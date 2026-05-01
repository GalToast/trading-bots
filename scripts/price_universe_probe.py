import argparse
import json
import os
import sys
import time

import MetaTrader5 as mt5

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import mt5_bot_v10 as bot


def build_symbol_row(name, active_symbols, now):
    info = mt5.symbol_info(name)
    tick = mt5.symbol_info_tick(name)
    diag = {}
    signal, confidence, atr, thesis, signal_type = bot.get_price_edge_signal(name, diagnostics=diag)

    row = {
        "symbol": name,
        "active": name in active_symbols,
        "visible": bool(info.visible) if info else None,
        "exotic": bot.is_exotic(name),
        "price_signal": signal,
        "price_conf": round(float(confidence or 0.0), 4),
        "price_best_conf": round(float(diag.get("price_best_confidence", 0.0) or 0.0), 4),
        "price_best_score": round(float(diag.get("price_best_score", 0.0) or 0.0), 2),
        "price_best_type": (
            diag.get("price_best_signal_type")
            or diag.get("price_best_score_signal_type")
            or "-"
        ),
        "price_fail_exotic": int(diag.get("price_fail_exotic", 0) or 0),
        "price_fail_m1_bars": int(diag.get("price_fail_m1_bars", 0) or 0),
        "price_fail_bars": int(diag.get("price_fail_bars", 0) or 0),
        "price_fail_htf_bars": int(diag.get("price_fail_htf_bars", 0) or 0),
        "price_fail_atr": int(diag.get("price_fail_atr", 0) or 0),
        "spread_pct": None,
        "spread_ok": False,
        "stale": None,
        "tick_age_s": None,
    }

    if tick and tick.ask and tick.ask > 0:
        spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100
        stale, age = bot.is_tick_stale(tick, now=now)
        if bot.is_crypto(name):
            max_spread = bot.MAX_SPREAD_PCT_CRYPTO
        elif bot.is_exotic(name):
            max_spread = bot.MAX_SPREAD_PCT_EXOTIC
        else:
            max_spread = bot.MAX_SPREAD_PCT_FOREX
        row["spread_pct"] = round(spread_pct, 5)
        row["spread_ok"] = spread_pct <= max_spread
        row["stale"] = bool(stale)
        row["tick_age_s"] = round(float(age or 0.0), 1)

    return row


def main():
    parser = argparse.ArgumentParser(description="Probe current PRICE universe and offboard symbols.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=["AUDNZD", "GBPNZD", "GBPCAD", "GBPCHF", "GER30", "EURDKK", "USDCHF"],
        help="Extra symbols to force into the report.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="How many symbols to keep in the global/offboard rankings.",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Also scan the full MT5 symbol list for offboard PRICE scores.",
    )
    args = parser.parse_args()

    if not bot.connect_mt5():
        print(json.dumps({"error": "connect_mt5_failed", "last_error": mt5.last_error()}, indent=2))
        raise SystemExit(1)

    active_symbols = bot.get_active_symbols()
    active_set = set(active_symbols)
    now = time.time()
    rows = []

    for sym in active_symbols:
        rows.append(build_symbol_row(sym, active_set, now))

    seen = set(active_symbols)
    for sym in args.symbols:
        if sym in seen:
            continue
        rows.append(build_symbol_row(sym, active_set, now))
        seen.add(sym)

    offboard_rows = []
    if args.scan_all:
        all_symbols = mt5.symbols_get() or []
        for info in all_symbols:
            name = info.name
            if name in seen or info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                continue
            row = build_symbol_row(name, active_set, now)
            if not row["active"] and row["price_best_score"] > 0 and not row["exotic"]:
                offboard_rows.append(row)

    rows.sort(key=lambda row: (row["price_best_score"], row["price_best_conf"]), reverse=True)
    offboard_rows.sort(key=lambda row: (row["price_best_score"], row["price_best_conf"]), reverse=True)

    result = {
        "active_count": len(active_symbols),
        "active_symbols": active_symbols,
        "tracked_rows": rows,
        "top_price_offboard": offboard_rows[: args.top],
    }
    print(json.dumps(result, indent=2))
    mt5.shutdown()


if __name__ == "__main__":
    main()
