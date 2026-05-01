#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
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
class Lane:
    lane_id: str
    motif_id: str
    exit_kind: str
    lookback: int = 8
    min_body_pips: float = 3.0
    min_body_ratio: float = 0.65
    sweep_pips: float = 0.8
    reclaim_close_pips: float = 0.3
    max_hold_bars: int = 6
    floor_pips: float = 0.5
    min_mfe_for_trail_pips: float = 3.0
    min_range_expansion: float = 0.0
    confirm_pips: float = 0.0
    confirm_window_bars: int = 1


@dataclass
class Trade:
    symbol: str
    lane_id: str
    motif_id: str
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


LANES = (
    Lane(
        lane_id="confirm_disp_1p5_rx2p5_ret60",
        motif_id="confirmed_displacement",
        exit_kind="retain_60",
        lookback=20,
        min_range_expansion=2.5,
        confirm_pips=1.5,
        confirm_window_bars=1,
        max_hold_bars=30,
        floor_pips=0.5,
        min_mfe_for_trail_pips=3.0,
    ),
    Lane(
        lane_id="ctrl_break_ret75",
        motif_id="control_breakout",
        exit_kind="retain_75",
        lookback=8,
        min_body_pips=3.0,
        min_body_ratio=0.65,
        max_hold_bars=6,
        floor_pips=0.5,
        min_mfe_for_trail_pips=3.0,
    ),
    Lane(
        lane_id="stoprun_reclaim_opp",
        motif_id="stop_run_reclaim",
        exit_kind="opp_close",
        lookback=8,
        min_body_pips=1.5,
        sweep_pips=0.8,
        reclaim_close_pips=0.3,
        max_hold_bars=6,
    ),
    Lane(
        lane_id="stoprun_reclaim_ret60",
        motif_id="stop_run_reclaim",
        exit_kind="retain_60",
        lookback=8,
        min_body_pips=1.5,
        sweep_pips=0.8,
        reclaim_close_pips=0.3,
        max_hold_bars=6,
        floor_pips=0.5,
        min_mfe_for_trail_pips=1.0,
    ),
    Lane(
        lane_id="failed_cont_fade_time3",
        motif_id="failed_continuation_fade",
        exit_kind="time_3",
        lookback=8,
        min_body_pips=4.0,
        min_body_ratio=0.75,
        max_hold_bars=4,
    ),
    Lane(
        lane_id="failed_cont_fade_ret60",
        motif_id="failed_continuation_fade",
        exit_kind="retain_60",
        lookback=8,
        min_body_pips=4.0,
        min_body_ratio=0.75,
        max_hold_bars=5,
        floor_pips=0.5,
        min_mfe_for_trail_pips=1.0,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Vivisect multiple motif families across symbols with the same "
            "windows/folds/pocket discipline used for confirmed displacement."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument(
        "--asset-groups",
        nargs="*",
        default=[],
        help="Groups: fx_majors, fx_all, crypto, metals, indices",
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--windows", nargs="*", type=int, default=list(DEFAULT_WINDOWS))
    parser.add_argument("--fold-days", type=int, default=10)
    parser.add_argument("--min-pocket-trades", type=int, default=8)
    parser.add_argument(
        "--min-trades",
        type=int,
        default=10,
        help="Minimum anchor-window trade count required for a lane to appear in the ranked frontier",
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "motif_frontier.csv"),
        help="Path for symbol-lane CSV output",
    )
    parser.add_argument(
        "--pockets-csv",
        default=str(ROOT / "reports" / "motif_frontier_pockets.csv"),
        help="Path for pocket-level CSV output",
    )
    return parser.parse_args()


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

    groups = list(asset_groups)
    if not groups and not picked:
        groups = ["fx_majors"]

    for group in groups:
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


def range_pips(bar: dict, pip_size: float) -> float:
    return max((bar["high"] - bar["low"]) / pip_size, 0.01)


def bar_dir(bar: dict) -> str | None:
    if bar["close"] > bar["open"]:
        return "BUY"
    if bar["close"] < bar["open"]:
        return "SELL"
    return None


def signed_pips(direction: str, start: float, end: float, pip_size: float) -> float:
    move = (end - start) / pip_size
    return move if direction == "BUY" else -move


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


def session_bucket(entry_time_utc: datetime) -> str:
    hour = entry_time_utc.hour
    for label, start, end in SESSION_BUCKETS:
        if start <= hour < end:
            return label
    return "late"


def detect_confirmed_displacement(
    bars: list[dict],
    idx: int,
    lane: Lane,
    pip_size: float,
) -> tuple[str, float] | None:
    if idx < lane.lookback + 1:
        return None
    cur = bars[idx]
    prior = bars[idx - lane.lookback : idx]
    prior_high = max(bar["high"] for bar in prior)
    prior_low = min(bar["low"] for bar in prior)
    atr_pips = compute_atr_pips(bars, idx, pip_size)
    body = body_pips(cur, pip_size)
    if atr_pips <= 0 or body < lane.min_range_expansion * atr_pips:
        return None
    if cur["close"] > prior_high + lane.confirm_pips * pip_size:
        return "BUY", prior_high
    if cur["close"] < prior_low - lane.confirm_pips * pip_size:
        return "SELL", prior_low
    return None


def detect_control_breakout(bars: list[dict], idx: int, lane: Lane, pip_size: float) -> str | None:
    if idx < lane.lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - lane.lookback : idx]
    prior_high = max(bar["high"] for bar in prior)
    prior_low = min(bar["low"] for bar in prior)
    body = body_pips(cur, pip_size)
    ratio = body / range_pips(cur, pip_size)
    if body < lane.min_body_pips or ratio < lane.min_body_ratio:
        return None
    if cur["close"] > prior_high:
        return "BUY"
    if cur["close"] < prior_low:
        return "SELL"
    return None


def detect_stop_run_reclaim(bars: list[dict], idx: int, lane: Lane, pip_size: float) -> str | None:
    if idx < lane.lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - lane.lookback : idx]
    prior_high = max(bar["high"] for bar in prior)
    prior_low = min(bar["low"] for bar in prior)
    direction = bar_dir(cur)
    if direction == "BUY":
        swept = cur["low"] <= (prior_low - lane.sweep_pips * pip_size)
        reclaimed = cur["close"] >= (prior_low + lane.reclaim_close_pips * pip_size)
        if swept and reclaimed and body_pips(cur, pip_size) >= lane.min_body_pips:
            return "BUY"
    if direction == "SELL":
        swept = cur["high"] >= (prior_high + lane.sweep_pips * pip_size)
        reclaimed = cur["close"] <= (prior_high - lane.reclaim_close_pips * pip_size)
        if swept and reclaimed and body_pips(cur, pip_size) >= lane.min_body_pips:
            return "SELL"
    return None


def detect_failed_continuation_fade(
    bars: list[dict],
    idx: int,
    lane: Lane,
    pip_size: float,
) -> str | None:
    if idx < lane.lookback + 1:
        return None
    prev = bars[idx - 1]
    cur = bars[idx]
    prior = bars[idx - 1 - lane.lookback : idx - 1]
    if len(prior) < lane.lookback:
        return None
    prior_high = max(bar["high"] for bar in prior)
    prior_low = min(bar["low"] for bar in prior)
    prev_body = body_pips(prev, pip_size)
    prev_ratio = prev_body / range_pips(prev, pip_size)
    if prev_body < lane.min_body_pips or prev_ratio < lane.min_body_ratio:
        return None

    if prev["close"] > prior_high:
        if cur["close"] < prior_high and cur["close"] < prev["open"]:
            return "SELL"
    if prev["close"] < prior_low:
        if cur["close"] > prior_low and cur["close"] > prev["open"]:
            return "BUY"
    return None


def find_entry_plan(
    bars: list[dict],
    idx: int,
    lane: Lane,
    pip_size: float,
) -> tuple[str, int, float, float] | None:
    entry_atr_pips = compute_atr_pips(bars, idx, pip_size)
    if lane.motif_id == "confirmed_displacement":
        detected = detect_confirmed_displacement(bars, idx, lane, pip_size)
        if not detected:
            return None
        direction, structure_level = detected
        end_idx = min(len(bars), idx + 1 + lane.confirm_window_bars)
        for entry_idx in range(idx + 1, end_idx):
            bar = bars[entry_idx]
            if direction == "BUY" and bar["close"] >= structure_level:
                return direction, entry_idx, bar["open"], entry_atr_pips
            if direction == "SELL" and bar["close"] <= structure_level:
                return direction, entry_idx, bar["open"], entry_atr_pips
        return None

    if lane.motif_id == "control_breakout":
        direction = detect_control_breakout(bars, idx, lane, pip_size)
    elif lane.motif_id == "stop_run_reclaim":
        direction = detect_stop_run_reclaim(bars, idx, lane, pip_size)
    elif lane.motif_id == "failed_continuation_fade":
        direction = detect_failed_continuation_fade(bars, idx, lane, pip_size)
    else:
        direction = None
    if not direction:
        return None
    entry_idx = idx + 1
    if entry_idx >= len(bars):
        return None
    return direction, entry_idx, bars[entry_idx]["open"], entry_atr_pips


def should_exit(
    lane: Lane,
    direction: str,
    entry_price: float,
    bars: list[dict],
    idx: int,
    mfe_pips: float,
    pip_size: float,
) -> bool:
    bar = bars[idx]
    prev = bars[idx - 1] if idx > 0 else bar
    close_pips = signed_pips(direction, entry_price, bar["close"], pip_size)
    current_dir = bar_dir(bar)

    if lane.exit_kind == "opp_close":
        return current_dir is not None and current_dir != direction

    if lane.exit_kind == "time_3":
        progressed = signed_pips(direction, prev["close"], bar["close"], pip_size) > 0
        return not progressed

    if lane.exit_kind in {"retain_60", "retain_75"} and mfe_pips >= lane.min_mfe_for_trail_pips:
        keep = 0.60 if lane.exit_kind == "retain_60" else 0.75
        floor = max(lane.floor_pips, mfe_pips * keep)
        return close_pips <= floor

    return False


def assign_volatility_buckets(trades: list[Trade]) -> None:
    values = sorted(trade.entry_atr_pips for trade in trades if trade.entry_atr_pips > 0)
    if len(values) < 3:
        for trade in trades:
            trade.vol_bucket = "mid"
        return
    lo_idx = max(0, int(len(values) * 0.33) - 1)
    hi_idx = max(0, int(len(values) * 0.66) - 1)
    low_cut = values[lo_idx]
    high_cut = values[hi_idx]
    for trade in trades:
        if trade.entry_atr_pips <= low_cut:
            trade.vol_bucket = "low"
        elif trade.entry_atr_pips <= high_cut:
            trade.vol_bucket = "mid"
        else:
            trade.vol_bucket = "high"


def simulate_lane(symbol: str, bars: list[dict], symbol_info, lane: Lane) -> list[Trade]:
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    trades: list[Trade] = []
    idx = max(lane.lookback + 2, 16)
    while idx < len(bars) - 2:
        entry_plan = find_entry_plan(bars, idx, lane, pip_size)
        if not entry_plan:
            idx += 1
            continue

        direction, entry_idx, entry_price, entry_atr_pips = entry_plan
        exit_idx = None
        exit_price = None
        mfe_pips = 0.0
        mae_pips = 0.0

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + lane.max_hold_bars + 1)):
            bar = bars[j]
            favorable = signed_pips(
                direction,
                entry_price,
                bar["high"] if direction == "BUY" else bar["low"],
                pip_size,
            )
            adverse = -signed_pips(
                direction,
                entry_price,
                bar["low"] if direction == "BUY" else bar["high"],
                pip_size,
            )
            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)
            if should_exit(lane, direction, entry_price, bars, j, mfe_pips, pip_size):
                exit_idx = j
                exit_price = bar["close"]
                break
            if (j - entry_idx + 1) >= lane.max_hold_bars:
                exit_idx = j
                exit_price = bar["close"]
                break

        if exit_idx is None or exit_price is None:
            exit_idx = min(len(bars) - 1, entry_idx + lane.max_hold_bars)
            exit_price = bars[exit_idx]["close"]

        entry_time_utc = datetime.fromtimestamp(int(bars[entry_idx]["time"]), tz=timezone.utc)
        trades.append(
            Trade(
                symbol=symbol,
                lane_id=lane.lane_id,
                motif_id=lane.motif_id,
                direction=direction,
                pnl_usd=trade_pnl_usd(symbol, direction, entry_price, exit_price, spread_px),
                hold_bars=exit_idx - entry_idx + 1,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                entry_time_utc=entry_time_utc,
                mfe_pips=max(0.0, mfe_pips),
                mae_pips=max(0.0, mae_pips),
                entry_atr_pips=max(0.0, entry_atr_pips),
                session_bucket=session_bucket(entry_time_utc),
            )
        )
        idx = exit_idx + 1

    assign_volatility_buckets(trades)
    return trades


def summarize_trades(trades: list[Trade], days: int) -> dict:
    wins = [trade for trade in trades if trade.pnl_usd > 0]
    pnl_values = [trade.pnl_usd for trade in trades]
    worst_cluster = 0.0
    if len(pnl_values) >= 5:
        worst_cluster = min(sum(pnl_values[i : i + 5]) for i in range(len(pnl_values) - 4))
    loss_streak = 0
    max_loss_streak = 0
    for pnl in pnl_values:
        if pnl <= 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0
    return {
        "trades": len(trades),
        "per_day": len(trades) / max(days, 1),
        "wr": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "net_usd": sum(pnl_values),
        "exp_usd": mean(pnl_values) if pnl_values else 0.0,
        "avg_hold_bars": mean(trade.hold_bars for trade in trades) if trades else 0.0,
        "avg_mfe_pips": mean(trade.mfe_pips for trade in trades) if trades else 0.0,
        "avg_mae_pips": mean(trade.mae_pips for trade in trades) if trades else 0.0,
        "max_loss_streak": max_loss_streak,
        "worst_cluster_5": worst_cluster,
    }


def build_pocket_rows(symbol: str, lane: Lane, trades: list[Trade], min_trades: int) -> list[dict]:
    rows: list[dict] = []
    buckets: dict[str, list[Trade]] = {}
    for trade in trades:
        for pocket in (
            f"{trade.direction}|{trade.session_bucket}",
            f"{trade.direction}|{trade.vol_bucket}",
        ):
            buckets.setdefault(pocket, []).append(trade)

    for pocket, pocket_trades in buckets.items():
        if len(pocket_trades) < min_trades:
            continue
        pnl_values = [trade.pnl_usd for trade in pocket_trades]
        rows.append(
            {
                "symbol": symbol,
                "lane_id": lane.lane_id,
                "motif_id": lane.motif_id,
                "pocket": pocket,
                "trades": len(pocket_trades),
                "wr": round(sum(1 for pnl in pnl_values if pnl > 0) / len(pnl_values) * 100.0, 1),
                "exp_usd": round(mean(pnl_values), 3),
                "net_usd": round(sum(pnl_values), 3),
            }
        )
    rows.sort(key=lambda row: row["exp_usd"], reverse=True)
    return rows


def evaluate_symbol_lane(
    symbol: str,
    lane: Lane,
    bars_full: list[dict],
    symbol_info,
    windows: list[int],
    fold_days: int,
    min_pocket_trades: int,
    min_trades: int,
) -> tuple[dict, list[dict]] | None:
    if not bars_full:
        return None

    per_window: dict[int, list[Trade]] = {}
    for window in windows:
        window_bars = bars_full[-1440 * window :]
        trades = simulate_lane(symbol, window_bars, symbol_info, lane)
        per_window[window] = trades

    anchor_window = max(windows)
    anchor_trades = per_window[anchor_window]
    if not anchor_trades or len(anchor_trades) < min_trades:
        return None

    fold_size = 1440 * fold_days
    fold_exps: list[float] = []
    for start in range(0, len(bars_full), fold_size):
        fold_bars = bars_full[start : start + fold_size]
        if len(fold_bars) < max(lane.lookback * 10, 500):
            continue
        fold_trades = simulate_lane(symbol, fold_bars, symbol_info, lane)
        if fold_trades:
            fold_exps.append(mean(trade.pnl_usd for trade in fold_trades))

    window_exps = [mean(trade.pnl_usd for trade in trades) for trades in per_window.values() if trades]
    positive_windows = sum(1 for value in window_exps if value > 0)
    positive_folds = sum(1 for value in fold_exps if value > 0)
    pocket_rows = build_pocket_rows(symbol, lane, anchor_trades, min_pocket_trades)
    best_pocket = pocket_rows[0]["pocket"] if pocket_rows else ""
    best_pocket_exp = pocket_rows[0]["exp_usd"] if pocket_rows else 0.0
    pocket_breadth = sum(1 for row in pocket_rows if row["exp_usd"] > 0)
    spread_pips = spread_price(symbol_info) / max(pip_size_for(symbol_info), 1e-9)

    stats_30 = summarize_trades(anchor_trades, anchor_window)
    robust_score = (
        stats_30["exp_usd"]
        + (min(window_exps) if window_exps else 0.0)
        + (min(fold_exps) if fold_exps else 0.0)
        + 0.05 * pocket_breadth
    )

    row = {
        "symbol": symbol,
        "lane_id": lane.lane_id,
        "motif_id": lane.motif_id,
        "window_anchor_days": anchor_window,
        "spread_pips": round(spread_pips, 3),
        "trades_30d": stats_30["trades"],
        "per_day_30d": round(stats_30["per_day"], 2),
        "per_hour_30d": round(stats_30["per_day"] / 24.0, 3),
        "wr_30d": round(stats_30["wr"], 1),
        "exp_30d": round(stats_30["exp_usd"], 3),
        "net_30d": round(stats_30["net_usd"], 3),
        "avg_hold_bars": round(stats_30["avg_hold_bars"], 1),
        "avg_mfe_pips": round(stats_30["avg_mfe_pips"], 1),
        "avg_mae_pips": round(stats_30["avg_mae_pips"], 1),
        "max_loss_streak": stats_30["max_loss_streak"],
        "worst_cluster_5": round(stats_30["worst_cluster_5"], 2),
        "positive_windows": positive_windows,
        "positive_folds": positive_folds,
        "min_window_exp": round(min(window_exps) if window_exps else 0.0, 3),
        "min_fold_exp": round(min(fold_exps) if fold_exps else 0.0, 3),
        "fold_count": len(fold_exps),
        "fold_exp_std": round(pstdev(fold_exps), 3) if len(fold_exps) > 1 else 0.0,
        "pocket_breadth": pocket_breadth,
        "robust_score": round(robust_score, 3),
        "best_pocket": best_pocket,
        "best_pocket_exp": round(best_pocket_exp, 3),
    }
    for window in sorted(windows):
        row[f"exp_{window}d"] = round(mean(trade.pnl_usd for trade in per_window[window]), 3) if per_window[window] else 0.0
        row[f"trades_{window}d"] = len(per_window[window])
    return row, pocket_rows


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbols = discover_symbols(args.symbols, args.asset_groups)
        if not symbols:
            print("No symbols selected")
            return 1

        rows: list[dict] = []
        pocket_rows: list[dict] = []
        for symbol in symbols:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                continue
            bars_full = load_bars(symbol, args.days)
            if not bars_full:
                continue
            for lane in LANES:
                evaluated = evaluate_symbol_lane(
                    symbol=symbol,
                    lane=lane,
                    bars_full=bars_full,
                    symbol_info=symbol_info,
                    windows=list(args.windows),
                    fold_days=args.fold_days,
                    min_pocket_trades=args.min_pocket_trades,
                    min_trades=args.min_trades,
                )
                if not evaluated:
                    continue
                row, lane_pockets = evaluated
                rows.append(row)
                pocket_rows.extend(lane_pockets)
                print(
                    f"{symbol:<7} {lane.lane_id:<28} "
                    f"exp={row['exp_30d']:+.3f} trades={row['trades_30d']:>4} "
                    f"min_win={row['min_window_exp']:+.3f} min_fold={row['min_fold_exp']:+.3f} "
                    f"score={row['robust_score']:+.3f}"
                )

        rows.sort(key=lambda row: row["robust_score"], reverse=True)
        pocket_rows.sort(key=lambda row: row["exp_usd"], reverse=True)

        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Saved {output_path}")

        pockets_path = Path(args.pockets_csv)
        pockets_path.parent.mkdir(parents=True, exist_ok=True)
        if pocket_rows:
            with pockets_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(pocket_rows[0].keys()))
                writer.writeheader()
                writer.writerows(pocket_rows)
            print(f"Saved {pockets_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
