#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import mean

import MetaTrader5 as mt5


DEFAULT_SYMBOLS = [
    "USDJPY",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "NZDJPY",
    "CADJPY",
    "CHFJPY",
    "USDCHF",
    "GBPUSD",
    "EURUSD",
    "AUDUSD",
    "NZDCAD",
    "AUDCHF",
]
VOLUME = 0.01
WINDOWS = (10, 20, 30)


@dataclass(frozen=True)
class LaneConfig:
    lookback: int = 20
    min_range_expansion: float = 2.5
    confirm_pips: float = 1.5
    confirm_window_bars: int = 1
    max_hold_bars: int = 30
    min_mfe_for_trail_pips: float = 3.0
    retain_ratio: float = 0.60


@dataclass
class Trade:
    pnl_usd: float
    hold_bars: int
    entry_idx: int
    exit_idx: int


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
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def body_pips(bar: dict, pip_size: float) -> float:
    return abs(bar["close"] - bar["open"]) / pip_size


def range_pips(bar: dict, pip_size: float) -> float:
    return max((bar["high"] - bar["low"]) / pip_size, 0.01)


def avg_volume(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    if not window:
        return 0.0
    return mean(bar["tick_volume"] for bar in window)


def avg_range_pips(bars: list[dict], start: int, end: int, pip_size: float) -> float:
    window = bars[max(0, start):end]
    if not window:
        return 0.0
    return mean(range_pips(bar, pip_size) for bar in window)


def compute_atr_pips(bars: list[dict], idx: int, pip_size: float, period: int = 14) -> float:
    if idx < period:
        return 0.0
    trs: list[float] = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        trs.append(tr / pip_size)
    return mean(trs) if trs else 0.0


def bar_dir(bar: dict) -> str | None:
    if bar["close"] > bar["open"]:
        return "BUY"
    if bar["close"] < bar["open"]:
        return "SELL"
    return None


def signed_pips(direction: str, start: float, end: float, pip_size: float) -> float:
    move = (end - start) / pip_size
    return move if direction == "BUY" else -move


def spread_price(symbol_info) -> float:
    return float(symbol_info.spread or 0.0) * float(symbol_info.point or 0.0)


def spread_pips(symbol_info) -> float:
    pip_size = pip_size_for(symbol_info)
    return spread_price(symbol_info) / pip_size if pip_size > 0 else 0.0


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


def detect_confirmed_displacement(
    bars: list[dict],
    idx: int,
    cfg: LaneConfig,
    pip_size: float,
) -> tuple[str, float] | None:
    if idx < cfg.lookback + 1:
        return None
    cur = bars[idx]
    prior = bars[idx - cfg.lookback:idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    atr_pips = compute_atr_pips(bars, idx, pip_size)
    body = body_pips(cur, pip_size)
    if atr_pips <= 0 or body < cfg.min_range_expansion * atr_pips:
        return None
    if cur["close"] > prior_high + cfg.confirm_pips * pip_size:
        return "BUY", prior_high
    if cur["close"] < prior_low - cfg.confirm_pips * pip_size:
        return "SELL", prior_low
    return None


def find_entry_plan(
    bars: list[dict],
    idx: int,
    cfg: LaneConfig,
    pip_size: float,
) -> tuple[str, int, float] | None:
    detected = detect_confirmed_displacement(bars, idx, cfg, pip_size)
    if not detected:
        return None
    direction, structure_level = detected
    end_idx = min(len(bars), idx + 1 + cfg.confirm_window_bars)
    for entry_idx in range(idx + 1, end_idx):
        bar = bars[entry_idx]
        if direction == "BUY" and bar["close"] >= structure_level:
            return direction, entry_idx, bar["open"]
        if direction == "SELL" and bar["close"] <= structure_level:
            return direction, entry_idx, bar["open"]
    return None


def should_exit(
    direction: str,
    entry_price: float,
    bar: dict,
    mfe_pips: float,
    cfg: LaneConfig,
    pip_size: float,
) -> bool:
    if mfe_pips < cfg.min_mfe_for_trail_pips:
        return False
    close_pips = signed_pips(direction, entry_price, bar["close"], pip_size)
    floor = mfe_pips * cfg.retain_ratio
    return close_pips <= floor


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, cfg: LaneConfig) -> list[Trade]:
    pip_size = pip_size_for(symbol_info)
    spr_px = spread_price(symbol_info)
    trades: list[Trade] = []
    idx = cfg.lookback + 2
    while idx < len(bars) - 2:
        entry_plan = find_entry_plan(bars, idx, cfg, pip_size)
        if not entry_plan:
            idx += 1
            continue
        direction, entry_idx, entry_price = entry_plan
        exit_idx = None
        exit_price = None
        mfe_pips = 0.0
        for j in range(entry_idx, min(len(bars) - 1, entry_idx + cfg.max_hold_bars + 1)):
            bar = bars[j]
            favorable = signed_pips(direction, entry_price, bar["high"] if direction == "BUY" else bar["low"], pip_size)
            mfe_pips = max(mfe_pips, favorable)
            if should_exit(direction, entry_price, bar, mfe_pips, cfg, pip_size):
                exit_idx = j
                exit_price = bar["close"]
                break
            if (j - entry_idx + 1) >= cfg.max_hold_bars:
                exit_idx = j
                exit_price = bar["close"]
                break
        if exit_idx is None or exit_price is None:
            exit_idx = min(len(bars) - 1, entry_idx + cfg.max_hold_bars)
            exit_price = bars[exit_idx]["close"]
        trades.append(
            Trade(
                pnl_usd=trade_pnl_usd(symbol, direction, entry_price, exit_price, spr_px),
                hold_bars=exit_idx - entry_idx + 1,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
            )
        )
        idx = exit_idx + 1
    return trades


def summarize(trades: list[Trade], days: int) -> dict:
    wins = [t for t in trades if t.pnl_usd > 0]
    return {
        "trades": len(trades),
        "per_day": len(trades) / max(days, 1),
        "wr": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "net_usd": sum(t.pnl_usd for t in trades),
        "exp_usd": (sum(t.pnl_usd for t in trades) / len(trades)) if trades else 0.0,
        "avg_hold": mean(t.hold_bars for t in trades) if trades else 0.0,
    }


def evaluate_symbol(symbol: str, cfg: LaneConfig, max_days: int) -> tuple[object, dict[int, dict]] | None:
    info = mt5.symbol_info(symbol)
    if info is None or not info.visible:
        return None
    bars = load_bars(symbol, max_days)
    if not bars or len(bars) < 1440 * min(WINDOWS):
        return None
    by_window: dict[int, dict] = {}
    for window in WINDOWS:
        sub = bars[-1440 * window:]
        by_window[window] = summarize(simulate_symbol(symbol, sub, info, cfg), window)
    return info, by_window


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-symbol confirmed-displacement sweep")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=max(WINDOWS))
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        cfg = LaneConfig()
        rows: list[tuple[str, object, dict[int, dict]]] = []
        for symbol in args.symbols:
            evaluated = evaluate_symbol(symbol, cfg, args.days)
            if evaluated is None:
                continue
            info, by_window = evaluated
            rows.append((symbol, info, by_window))

        rows.sort(
            key=lambda row: (
                min(row[2][window]["exp_usd"] for window in WINDOWS),
                row[2][30]["exp_usd"],
                row[2][30]["trades"],
            ),
            reverse=True,
        )

        print(
            "Confirmed displacement symbol sweep | "
            f"confirm=1.5p window=2 range_x=2.5 retain=60 floor=0.5 | volume={VOLUME:.2f}"
        )
        print()
        print(
            f"{'symbol':<10} {'spr':>5} "
            f"{'exp10':>8} {'tr10':>6} "
            f"{'exp20':>8} {'tr20':>6} "
            f"{'exp30':>8} {'tr30':>6} {'wr30':>6}"
        )
        print("-" * 78)
        for symbol, info, by_window in rows:
            print(
                f"{symbol:<10} {spread_pips(info):>5.1f} "
                f"{by_window[10]['exp_usd']:>+8.3f} {by_window[10]['trades']:>6d} "
                f"{by_window[20]['exp_usd']:>+8.3f} {by_window[20]['trades']:>6d} "
                f"{by_window[30]['exp_usd']:>+8.3f} {by_window[30]['trades']:>6d} "
                f"{by_window[30]['wr']:>5.1f}%"
            )

        stable = [
            (symbol, info, by_window)
            for symbol, info, by_window in rows
            if min(by_window[window]["exp_usd"] for window in WINDOWS) > 0
        ]
        print()
        print(f"Stable positives across {WINDOWS}d: {len(stable)}")
        for symbol, info, by_window in stable[:10]:
            print(
                f"- {symbol}: spread={spread_pips(info):.1f}p | "
                f"exp30={by_window[30]['exp_usd']:+.3f} | trades30={by_window[30]['trades']} | "
                f"per_day30={by_window[30]['per_day']:.1f}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
