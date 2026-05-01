#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WINDOWS = (10, 20, 30)
VOLUME = 0.01
FX_MAJORS = ("AUDUSD", "EURUSD", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")
KNOWN_CRYPTO_MARKERS = ("BTC", "ETH", "XRP", "LTC", "DASH", "XMR", "ZEC")
KNOWN_CURRENCIES = {
    "AUD",
    "CAD",
    "CHF",
    "CNH",
    "CZK",
    "DKK",
    "EUR",
    "GBP",
    "HKD",
    "JPY",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "SEK",
    "SGD",
    "USD",
    "ZAR",
}
SESSION_BUCKETS = (
    ("asia", 0, 7),
    ("london", 7, 13),
    ("new_york", 13, 21),
    ("late", 21, 24),
)


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    lookback: int
    min_range_expansion: float
    confirm_pips: float
    confirm_window_bars: int
    max_hold_bars: int
    min_mfe_for_trail_pips: float
    retain_ratio: float


@dataclass
class Trade:
    symbol: str
    recipe_id: str
    direction: str
    pnl_usd: float
    hold_bars: int
    entry_idx: int
    exit_idx: int
    entry_time_utc: datetime
    mfe_pips: float
    mae_pips: float
    entry_atr_pips: float
    session_bucket: str = "unknown"
    vol_bucket: str = "unknown"


CORE_RECIPES = (
    Recipe("confirm_disp_1p5_rx2p5_ret60", 20, 2.5, 1.5, 1, 30, 3.0, 0.60),
    Recipe("confirm_disp_2p0_rx2p5_ret60", 20, 2.5, 2.0, 1, 30, 3.0, 0.60),
    Recipe("confirm_disp_3p0_rx2p5_ret60", 20, 2.5, 3.0, 1, 30, 3.0, 0.60),
    Recipe("confirm_disp_1p5_rx2p0_ret60", 20, 2.0, 1.5, 1, 30, 3.0, 0.60),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Vivisect symbols under the same confirmed-displacement rigor: "
            "window stability, walk-forward folds, session/volatility pockets, and stress."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument(
        "--asset-groups",
        nargs="*",
        default=["fx_majors", "crypto"],
        help="Groups: fx_majors, fx_all, crypto, metals, indices",
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--windows", nargs="*", type=int, default=list(DEFAULT_WINDOWS))
    parser.add_argument(
        "--recipe-set",
        choices=("current", "core"),
        default="current",
        help="current = live recipe only, core = small recipe family for cross-symbol ranking",
    )
    parser.add_argument(
        "--fold-days",
        type=int,
        default=10,
        help="Length of each walk-forward fold in days",
    )
    parser.add_argument(
        "--min-pocket-trades",
        type=int,
        default=8,
        help="Minimum trade count before reporting a direction/session or direction/vol pocket",
    )
    parser.add_argument(
        "--top-pockets",
        type=int,
        default=3,
        help="Number of positive pockets to print per symbol+recipe",
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "symbol_vivisection.csv"),
        help="Path for symbol-level CSV output",
    )
    parser.add_argument(
        "--pockets-csv",
        default=str(ROOT / "reports" / "symbol_vivisection_pockets.csv"),
        help="Path for pocket-level CSV output",
    )
    return parser.parse_args()


def selected_recipes(recipe_set: str) -> tuple[Recipe, ...]:
    if recipe_set == "core":
        return CORE_RECIPES
    return (CORE_RECIPES[0],)


def visible_symbols() -> list[str]:
    return sorted(
        {
            str(symbol.name)
            for symbol in (mt5.symbols_get() or [])
            if getattr(symbol, "name", None)
        }
    )


def is_fx_symbol(name: str) -> bool:
    if len(name) != 6:
        return False
    base = name[:3]
    quote = name[3:]
    return base in KNOWN_CURRENCIES and quote in KNOWN_CURRENCIES


def discover_symbols(explicit: list[str], asset_groups: list[str]) -> list[str]:
    available = visible_symbols()
    available_set = set(available)
    picked: set[str] = {symbol.upper() for symbol in explicit if symbol.upper() in available_set}

    for group in asset_groups:
        normalized = str(group).lower()
        if normalized == "fx_majors":
            picked.update(symbol for symbol in FX_MAJORS if symbol in available_set)
        elif normalized == "fx_all":
            picked.update(symbol for symbol in available if is_fx_symbol(symbol))
        elif normalized == "crypto":
            picked.update(
                symbol
                for symbol in available
                if any(marker in symbol.upper() for marker in KNOWN_CRYPTO_MARKERS)
            )
        elif normalized == "metals":
            picked.update(symbol for symbol in available if symbol.upper().startswith(("XAU", "XAG")))
        elif normalized == "indices":
            picked.update(
                symbol
                for symbol in available
                if any(char.isdigit() for char in symbol) and not is_fx_symbol(symbol)
            )

    return sorted(picked)


def pip_size_for(symbol_info) -> float:
    point = float(symbol_info.point or 0.0)
    digits = int(symbol_info.digits or 0)
    return point * 10.0 if digits in (3, 5) else point


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(rate[0]),
            "open": float(rate[1]),
            "high": float(rate[2]),
            "low": float(rate[3]),
            "close": float(rate[4]),
            "tick_volume": int(rate[5]),
        }
        for rate in rates
    ]


def body_pips(bar: dict, pip_size: float) -> float:
    return abs(bar["close"] - bar["open"]) / pip_size


def compute_atr_pips(bars: list[dict], idx: int, pip_size: float, period: int = 14) -> float:
    if idx < period:
        return 0.0
    true_ranges: list[float] = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        true_ranges.append(tr / pip_size)
    return mean(true_ranges) if true_ranges else 0.0


def spread_price(symbol_info) -> float:
    return float(symbol_info.spread or 0.0) * float(symbol_info.point or 0.0)


def spread_cost_usd(symbol: str, direction: str, entry_price: float, spread_px: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    if direction == "BUY":
        cost = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price + spread_px, entry_price)
    else:
        cost = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price, entry_price + spread_px)
    return abs(float(cost or 0.0))


def trade_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, spread_px: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price, exit_price)
    if gross is None:
        return 0.0
    return float(gross) - spread_cost_usd(symbol, direction, entry_price, spread_px)


def signed_pips(direction: str, start: float, end: float, pip_size: float) -> float:
    move = (end - start) / pip_size
    return move if direction == "BUY" else -move


def detect_confirmed_displacement(
    bars: list[dict],
    idx: int,
    recipe: Recipe,
    pip_size: float,
) -> tuple[str, float, float] | None:
    if idx < recipe.lookback + 1:
        return None
    current = bars[idx]
    prior = bars[idx - recipe.lookback : idx]
    prior_high = max(bar["high"] for bar in prior)
    prior_low = min(bar["low"] for bar in prior)
    atr_pips = compute_atr_pips(bars, idx, pip_size)
    if atr_pips <= 0.0:
        return None
    if body_pips(current, pip_size) < recipe.min_range_expansion * atr_pips:
        return None
    if current["close"] > prior_high + recipe.confirm_pips * pip_size:
        return "BUY", prior_high, atr_pips
    if current["close"] < prior_low - recipe.confirm_pips * pip_size:
        return "SELL", prior_low, atr_pips
    return None


def find_entry_plan(
    bars: list[dict],
    idx: int,
    recipe: Recipe,
    pip_size: float,
) -> tuple[str, int, float, float] | None:
    detected = detect_confirmed_displacement(bars, idx, recipe, pip_size)
    if detected is None:
        return None
    direction, structure_level, atr_pips = detected
    end_idx = min(len(bars), idx + 1 + recipe.confirm_window_bars)
    for entry_idx in range(idx + 1, end_idx):
        bar = bars[entry_idx]
        if direction == "BUY" and bar["close"] >= structure_level:
            return direction, entry_idx, bar["open"], atr_pips
        if direction == "SELL" and bar["close"] <= structure_level:
            return direction, entry_idx, bar["open"], atr_pips
    return None


def should_exit(
    direction: str,
    entry_price: float,
    bar: dict,
    mfe_pips: float,
    recipe: Recipe,
    pip_size: float,
) -> bool:
    if mfe_pips < recipe.min_mfe_for_trail_pips:
        return False
    close_pips = signed_pips(direction, entry_price, bar["close"], pip_size)
    floor = mfe_pips * recipe.retain_ratio
    return close_pips <= floor


def utc_dt(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc)


def session_bucket(entry_time_utc: datetime) -> str:
    hour = int(entry_time_utc.hour)
    for name, start_hour, end_hour in SESSION_BUCKETS:
        if start_hour <= hour < end_hour:
            return name
    return "late"


def assign_vol_buckets(trades: list[Trade]) -> None:
    if not trades:
        return
    atrs = sorted(trade.entry_atr_pips for trade in trades)
    if not atrs:
        return
    low_cut = atrs[max(0, int((len(atrs) - 1) * (1 / 3)))]
    high_cut = atrs[max(0, int((len(atrs) - 1) * (2 / 3)))]
    for trade in trades:
        if trade.entry_atr_pips <= low_cut:
            trade.vol_bucket = "low"
        elif trade.entry_atr_pips >= high_cut:
            trade.vol_bucket = "high"
        else:
            trade.vol_bucket = "mid"


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, recipe: Recipe) -> list[Trade]:
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    trades: list[Trade] = []
    idx = recipe.lookback + 14
    while idx < len(bars) - 2:
        entry_plan = find_entry_plan(bars, idx, recipe, pip_size)
        if entry_plan is None:
            idx += 1
            continue

        direction, entry_idx, entry_price, atr_pips = entry_plan
        exit_idx = None
        exit_price = None
        mfe_pips = 0.0
        mae_pips = 0.0

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + recipe.max_hold_bars + 1)):
            bar = bars[j]
            favorable_price = bar["high"] if direction == "BUY" else bar["low"]
            adverse_price = bar["low"] if direction == "BUY" else bar["high"]
            favorable = signed_pips(direction, entry_price, favorable_price, pip_size)
            adverse = max(0.0, -signed_pips(direction, entry_price, adverse_price, pip_size))
            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)

            if should_exit(direction, entry_price, bar, mfe_pips, recipe, pip_size):
                exit_idx = j
                exit_price = bar["close"]
                break
            if (j - entry_idx + 1) >= recipe.max_hold_bars:
                exit_idx = j
                exit_price = bar["close"]
                break

        if exit_idx is None or exit_price is None:
            exit_idx = min(len(bars) - 1, entry_idx + recipe.max_hold_bars)
            exit_price = bars[exit_idx]["close"]

        trades.append(
            Trade(
                symbol=symbol,
                recipe_id=recipe.recipe_id,
                direction=direction,
                pnl_usd=trade_pnl_usd(symbol, direction, entry_price, exit_price, spread_px),
                hold_bars=exit_idx - entry_idx + 1,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                entry_time_utc=utc_dt(bars[entry_idx]["time"]),
                mfe_pips=max(0.0, mfe_pips),
                mae_pips=max(0.0, mae_pips),
                entry_atr_pips=max(0.0, atr_pips),
                session_bucket=session_bucket(utc_dt(bars[entry_idx]["time"])),
            )
        )
        idx = exit_idx + 1

    assign_vol_buckets(trades)
    return trades


def summarize_trades(trades: list[Trade], days: int) -> dict[str, float]:
    wins = [trade for trade in trades if trade.pnl_usd > 0.0]
    pnls = [trade.pnl_usd for trade in trades]
    streak = 0
    max_loss_streak = 0
    worst_cluster_5 = 0.0
    for start in range(len(pnls)):
        cluster = sum(pnls[start : start + 5])
        worst_cluster_5 = min(worst_cluster_5, cluster)
    for pnl in pnls:
        if pnl <= 0.0:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0
    return {
        "trades": len(trades),
        "trades_per_day": len(trades) / max(days, 1),
        "trades_per_hour": len(trades) / max(days * 24, 1),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "net_usd": sum(pnls),
        "exp_usd": (sum(pnls) / len(trades)) if trades else 0.0,
        "avg_hold_bars": mean(trade.hold_bars for trade in trades) if trades else 0.0,
        "avg_mfe_pips": mean(trade.mfe_pips for trade in trades) if trades else 0.0,
        "avg_mae_pips": mean(trade.mae_pips for trade in trades) if trades else 0.0,
        "max_loss_streak": float(max_loss_streak),
        "worst_cluster_5": float(worst_cluster_5),
    }


def window_summary(symbol: str, bars: list[dict], info, recipe: Recipe, window_days: int) -> dict[str, float]:
    subset = bars[-1440 * window_days :]
    trades = simulate_symbol(symbol, subset, info, recipe)
    summary = summarize_trades(trades, window_days)
    summary["days"] = float(window_days)
    return summary


def walk_forward_summaries(
    symbol: str,
    bars: list[dict],
    info,
    recipe: Recipe,
    fold_days: int,
) -> list[dict[str, float]]:
    if fold_days <= 0:
        return []
    fold_len = 1440 * fold_days
    total_folds = len(bars) // fold_len
    if total_folds <= 1:
        return []
    summaries: list[dict[str, float]] = []
    for fold_idx in range(total_folds):
        start = fold_idx * fold_len
        end = start + fold_len
        fold_bars = bars[start:end]
        if len(fold_bars) < fold_len:
            continue
        summary = summarize_trades(simulate_symbol(symbol, fold_bars, info, recipe), fold_days)
        summary["fold_index"] = float(fold_idx)
        summaries.append(summary)
    return summaries


def pocket_rows(
    trades: list[Trade],
    symbol: str,
    recipe_id: str,
    min_trades: int,
) -> list[dict[str, float | str]]:
    buckets: dict[tuple[str, str], list[Trade]] = {}
    for trade in trades:
        keys = (
            ("direction_session", f"{trade.direction}|{trade.session_bucket}"),
            ("direction_vol", f"{trade.direction}|{trade.vol_bucket}"),
        )
        for bucket_type, bucket_key in keys:
            buckets.setdefault((bucket_type, bucket_key), []).append(trade)

    rows: list[dict[str, float | str]] = []
    for (bucket_type, bucket_key), bucket_trades in buckets.items():
        if len(bucket_trades) < min_trades:
            continue
        summary = summarize_trades(bucket_trades, max(1, len({trade.entry_time_utc.date() for trade in bucket_trades})))
        rows.append(
            {
                "symbol": symbol,
                "recipe_id": recipe_id,
                "bucket_type": bucket_type,
                "bucket_key": bucket_key,
                "trades": int(summary["trades"]),
                "win_rate": round(summary["win_rate"], 1),
                "net_usd": round(summary["net_usd"], 3),
                "exp_usd": round(summary["exp_usd"], 3),
                "trades_per_day": round(summary["trades_per_day"], 2),
            }
        )
    rows.sort(
        key=lambda row: (
            float(row["exp_usd"]),
            float(row["net_usd"]),
            int(row["trades"]),
        ),
        reverse=True,
    )
    return rows


def robust_score(
    window_stats: list[dict[str, float]],
    fold_stats: list[dict[str, float]],
    overall: dict[str, float],
) -> float:
    positive_windows = sum(1 for stat in window_stats if stat["exp_usd"] > 0.0)
    positive_folds = sum(1 for stat in fold_stats if stat["exp_usd"] > 0.0)
    min_window_exp = min((stat["exp_usd"] for stat in window_stats), default=0.0)
    min_fold_exp = min((stat["exp_usd"] for stat in fold_stats), default=min_window_exp)
    fold_volatility = pstdev([stat["exp_usd"] for stat in fold_stats]) if len(fold_stats) > 1 else 0.0
    penalty = abs(min(0.0, min_window_exp)) + abs(min(0.0, min_fold_exp)) + fold_volatility
    bonus = (
        overall["exp_usd"] * 4.0
        + overall["trades_per_day"] * 0.25
        + positive_windows * 0.4
        + positive_folds * 0.25
    )
    return bonus - penalty - abs(overall["worst_cluster_5"]) * 0.2


def evaluate_symbol_recipe(
    symbol: str,
    bars: list[dict],
    info,
    recipe: Recipe,
    windows: list[int],
    fold_days: int,
    min_pocket_trades: int,
) -> tuple[dict[str, float | str], list[dict[str, float | str]]]:
    full_trades = simulate_symbol(symbol, bars, info, recipe)
    overall = summarize_trades(full_trades, max(windows) if windows else 30)
    window_stats = [window_summary(symbol, bars, info, recipe, window) for window in windows]
    fold_stats = walk_forward_summaries(symbol, bars[-1440 * max(windows) :], info, recipe, fold_days)
    pockets = pocket_rows(full_trades, symbol, recipe.recipe_id, min_pocket_trades)
    min_window_exp = min((stat["exp_usd"] for stat in window_stats), default=0.0)
    min_fold_exp = min((stat["exp_usd"] for stat in fold_stats), default=0.0)
    row: dict[str, float | str] = {
        "symbol": symbol,
        "recipe_id": recipe.recipe_id,
        "spread_pips": round(spread_price(info) / max(pip_size_for(info), 1e-9), 2),
        "trades_30d": int(overall["trades"]),
        "per_day_30d": round(overall["trades_per_day"], 2),
        "per_hour_30d": round(overall["trades_per_hour"], 3),
        "wr_30d": round(overall["win_rate"], 1),
        "exp_30d": round(overall["exp_usd"], 3),
        "net_30d": round(overall["net_usd"], 3),
        "avg_hold_bars": round(overall["avg_hold_bars"], 1),
        "avg_mfe_pips": round(overall["avg_mfe_pips"], 1),
        "avg_mae_pips": round(overall["avg_mae_pips"], 1),
        "max_loss_streak": int(overall["max_loss_streak"]),
        "worst_cluster_5": round(overall["worst_cluster_5"], 3),
        "positive_windows": sum(1 for stat in window_stats if stat["exp_usd"] > 0.0),
        "positive_folds": sum(1 for stat in fold_stats if stat["exp_usd"] > 0.0),
        "min_window_exp": round(min_window_exp, 3),
        "min_fold_exp": round(min_fold_exp, 3),
        "fold_count": len(fold_stats),
        "fold_exp_std": round(pstdev([stat["exp_usd"] for stat in fold_stats]) if len(fold_stats) > 1 else 0.0, 3),
        "robust_score": round(robust_score(window_stats, fold_stats, overall), 3),
        "best_pocket": pockets[0]["bucket_key"] if pockets else "",
        "best_pocket_exp": round(float(pockets[0]["exp_usd"]), 3) if pockets else 0.0,
    }
    for stat in window_stats:
        row[f"exp_{int(stat['days'])}d"] = round(stat["exp_usd"], 3)
        row[f"trades_{int(stat['days'])}d"] = int(stat["trades"])
    return row, pockets


def print_summary(rows: list[dict[str, float | str]], pocket_map: dict[tuple[str, str], list[dict[str, float | str]]], top_pockets: int) -> None:
    print("Symbol vivisection")
    print("Recipe | count, expectancy, walk-forward stability, regime pockets, stress")
    print()
    print(
        f"{'symbol':<10} {'recipe':<28} {'score':>7} {'exp30':>7} {'tr30':>5} "
        f"{'minW':>7} {'minF':>7} {'streak':>6} {'pocket':<18}"
    )
    print("-" * 104)
    for row in rows:
        key = (str(row["symbol"]), str(row["recipe_id"]))
        pocket = ""
        ranked = pocket_map.get(key, [])
        if ranked:
            pocket = str(ranked[0]["bucket_key"])
        print(
            f"{str(row['symbol']):<10} {str(row['recipe_id']):<28} {float(row['robust_score']):>+7.3f} "
            f"{float(row['exp_30d']):>+7.3f} {int(row['trades_30d']):>5d} "
            f"{float(row['min_window_exp']):>+7.3f} {float(row['min_fold_exp']):>+7.3f} "
            f"{int(row['max_loss_streak']):>6d} {pocket:<18}"
        )
        if ranked:
            for pocket_row in ranked[:top_pockets]:
                print(
                    "  "
                    f"{pocket_row['bucket_type']:<17} {pocket_row['bucket_key']:<18} "
                    f"tr={int(pocket_row['trades']):>3d} exp={float(pocket_row['exp_usd']):>+6.3f} "
                    f"wr={float(pocket_row['win_rate']):>5.1f}%"
                )
    print()


def write_csv(path: Path, rows: list[dict[str, float | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    windows = sorted({int(value) for value in args.windows if int(value) > 0})
    if not windows:
        raise SystemExit("No valid windows provided.")
    symbols = []

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbols = discover_symbols(list(args.symbols), list(args.asset_groups))
        if not symbols:
            print("No symbols discovered from the requested universe.")
            return 1

        recipes = selected_recipes(args.recipe_set)
        rows: list[dict[str, float | str]] = []
        all_pockets: list[dict[str, float | str]] = []
        pocket_map: dict[tuple[str, str], list[dict[str, float | str]]] = {}

        max_days = max(max(windows), int(args.days), int(args.fold_days))
        for symbol in symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, max_days)
            if len(bars) < 1440 * max(windows):
                continue
            for recipe in recipes:
                row, pockets = evaluate_symbol_recipe(
                    symbol=symbol,
                    bars=bars,
                    info=info,
                    recipe=recipe,
                    windows=windows,
                    fold_days=int(args.fold_days),
                    min_pocket_trades=int(args.min_pocket_trades),
                )
                rows.append(row)
                all_pockets.extend(pockets)
                pocket_map[(symbol, recipe.recipe_id)] = pockets
    finally:
        mt5.shutdown()

    rows.sort(
        key=lambda row: (
            float(row["robust_score"]),
            float(row["min_window_exp"]),
            float(row["min_fold_exp"]),
            float(row["exp_30d"]),
            int(row["trades_30d"]),
        ),
        reverse=True,
    )
    print_summary(rows, pocket_map, int(args.top_pockets))
    write_csv(Path(args.output_csv), rows)
    write_csv(Path(args.pockets_csv), all_pockets)
    print(f"Saved symbol report to {Path(args.output_csv)}")
    print(f"Saved pocket report to {Path(args.pockets_csv)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
