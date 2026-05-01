#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_coinbase_momentum_reconciliation_queue as recon_runner
import strategy_library as strategy_lib


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

FRONTIER_PATH = REPORTS / "coinbase_strategy_family_frontier.json"
VOL_FRONTIER_PATH = REPORTS / "coinbase_volatility_frontier.json"
JSON_PATH = REPORTS / "coinbase_atr_expansion_rescue_sweep.json"
MD_PATH = REPORTS / "coinbase_atr_expansion_rescue_sweep.md"

FEE_RATE = 0.004
STARTING_CASH = 48.0
TARGET_COIN_LIMIT = 4

ATR_PERIODS = [8, 14]
ATR_MULTS = [1.05, 1.25]
TP_PCTS = [6.0, 10.0]
SL_PCTS = [0.0, 4.0]
MAX_HOLDS = [24, 36]

DEFAULT_ATR_PARAMS = {"atr_period": 14, "atr_mult": 1.5, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24}


@dataclass
class CandleArrays:
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    timestamps: list[int]
    session_open: list[bool]
    tr_prefix: list[float]


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_coin_list(coins: list[str]) -> str:
    labels = [coin.replace("-USD", "") for coin in coins]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def plural_verb(items: list[str], singular: str, plural: str) -> str:
    return singular if len(items) == 1 else plural


def load_target_coins(limit: int = TARGET_COIN_LIMIT) -> list[str]:
    frontier_payload = load_json(FRONTIER_PATH)
    frontier_rows = {str(row.get("coin") or ""): row for row in list(frontier_payload.get("coin_rows") or []) if row.get("coin")}
    vol_payload = load_json(VOL_FRONTIER_PATH)
    vol_rows = list(vol_payload.get("coin_rows") or [])

    selected: list[str] = []
    seen: set[str] = set()

    for row in vol_rows:
        coin = str(row.get("coin") or "")
        if not coin or coin in seen:
            continue
        if bool(row.get("beats_family_frontier")):
            selected.append(coin)
            seen.add(coin)
        if len(selected) >= limit:
            return selected

    candidates = []
    for row in vol_rows:
        coin = str(row.get("coin") or "")
        if not coin or coin in seen:
            continue
        frontier_row = frontier_rows.get(coin) or {}
        frontier_best = to_float(frontier_row.get("best_net_pnl"))
        candidates.append(
            (
                frontier_best > 0.0,
                frontier_best,
                -to_float(row.get("best_vol_net_pnl")),
                coin,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    for _, _, _, coin in candidates:
        if coin in seen:
            continue
        selected.append(coin)
        seen.add(coin)
        if len(selected) >= limit:
            break
    return selected


def load_coin_candles(coin: str) -> tuple[list[dict[str, str]], str]:
    snapshot_map = recon_runner.load_snapshot_map()
    candles = snapshot_map.get(coin)
    if candles:
        return candles, "snapshot"
    return recon_runner.load_cache_candles(coin), "cache"


def build_candle_arrays(candles: list[dict[str, str]]) -> CandleArrays:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    timestamps: list[int] = []
    session_open: list[bool] = []
    tr_prefix = [0.0]

    prev_close = 0.0
    for idx, candle in enumerate(candles):
        candle_open = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        ts = int(candle.get("start", candle.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

        opens.append(candle_open)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        timestamps.append(ts)
        session_open.append(hour not in strategy_lib.SESSION_DEAD_HOURS)

        tr = 0.0
        if idx > 0:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_prefix.append(tr_prefix[-1] + tr)
        prev_close = close

    return CandleArrays(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        timestamps=timestamps,
        session_open=session_open,
        tr_prefix=tr_prefix,
    )


def tr_window_sum(prefix: list[float], start_idx: int, end_idx: int) -> float:
    return prefix[end_idx + 1] - prefix[start_idx]


def build_atr_signal_map(arrays: CandleArrays) -> dict[tuple[int, float], list[bool]]:
    signal_map: dict[tuple[int, float], list[bool]] = {}
    count = len(arrays.closes)
    for atr_period in ATR_PERIODS + [DEFAULT_ATR_PARAMS["atr_period"]]:
        if atr_period <= 0:
            continue
        for atr_mult in ATR_MULTS + [DEFAULT_ATR_PARAMS["atr_mult"]]:
            key = (atr_period, float(atr_mult))
            if key in signal_map:
                continue
            signals = [False] * count
            for idx in range(count):
                if idx < 29 or idx < atr_period + 1:
                    continue
                if arrays.closes[idx - 1] <= arrays.closes[idx - 2]:
                    continue
                current_sum = tr_window_sum(arrays.tr_prefix, idx - atr_period + 1, idx)
                prev_sum = tr_window_sum(arrays.tr_prefix, idx - atr_period, idx - 1)
                current_atr = current_sum / atr_period
                prev_atr = prev_sum / atr_period
                signals[idx] = current_atr > prev_atr * atr_mult
            signal_map[key] = signals
    return signal_map


def run_precomputed_atr_backtest(
    arrays: CandleArrays,
    signals: list[bool],
    *,
    tp_pct: float,
    sl_pct: float,
    max_hold: int,
) -> dict[str, Any]:
    cash = STARTING_CASH
    pos: dict[str, float | int] | None = None
    closes_count = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    peak = STARTING_CASH
    max_dd = 0.0
    signals_count = 0
    signals_filtered = 0
    signal_filtered_reason = {"session": 0, "fill": 0, "capital": 0}

    for idx, signal in enumerate(signals):
        high = arrays.highs[idx]
        low = arrays.lows[idx]
        close = arrays.closes[idx]
        candle_open = arrays.opens[idx]

        if pos is not None:
            pos["hold"] = int(pos["hold"]) + 1
            exit_price = None
            if high >= float(pos["tp"]):
                exit_price = float(pos["tp"])
            elif float(pos["sl"]) > 0.0 and low <= float(pos["sl"]):
                exit_price = float(pos["sl"])
            elif int(pos["hold"]) >= max_hold:
                exit_price = close

            if exit_price is not None:
                actual_exit = exit_price
                units = float(pos["units"])
                entry_price = float(pos["ep"])
                gross = (actual_exit - entry_price) * units
                entry_fee = float(pos["entry_fee"])
                exit_fee = actual_exit * units * FEE_RATE
                net = gross - entry_fee - exit_fee

                cash += float(pos["q"]) + net
                closes_count += 1
                total_fees += entry_fee + exit_fee
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                pos = None

        if pos is None and signal:
            signals_count += 1
            if not arrays.session_open[idx]:
                signals_filtered += 1
                signal_filtered_reason["session"] += 1
                continue
            if cash < 10.0:
                signals_filtered += 1
                signal_filtered_reason["capital"] += 1
                continue

            actual_entry = candle_open
            deploy = cash
            entry_fee = deploy * FEE_RATE
            units = (deploy - entry_fee) / actual_entry
            tp = actual_entry * (1 + tp_pct / 100.0)
            sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0.0

            cash -= deploy
            pos = {
                "ep": actual_entry,
                "q": deploy,
                "hold": 0,
                "tp": tp,
                "sl": sl,
                "units": units,
                "entry_fee": entry_fee,
            }

    if pos is not None:
        actual_exit = arrays.closes[-1]
        units = float(pos["units"])
        entry_price = float(pos["ep"])
        gross = (actual_exit - entry_price) * units
        entry_fee = float(pos["entry_fee"])
        exit_fee = actual_exit * units * FEE_RATE
        net = gross - entry_fee - exit_fee
        cash += float(pos["q"]) + net
        closes_count += 1
        total_fees += entry_fee + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1
        peak = max(peak, cash)
        dd = (peak - cash) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    net = cash - STARTING_CASH
    win_rate = wins / max(closes_count, 1) * 100.0
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / STARTING_CASH * 100.0, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "max_drawdown": round(max_dd * 100.0, 1),
        "signals": signals_count,
        "signals_filtered": signals_filtered,
        "signal_filtered_reason": signal_filtered_reason,
        "fill_rate": round(closes_count / max(signals_count, 1) * 100.0, 1),
        "total_fees": round(total_fees, 2),
    }


def build_frontier_row_map() -> dict[str, dict[str, Any]]:
    payload = load_json(FRONTIER_PATH)
    return {str(row.get("coin") or ""): row for row in list(payload.get("coin_rows") or []) if row.get("coin")}


def build_coin_payload(
    coin: str,
    candles: list[dict[str, str]],
    source: str,
    frontier_row: dict[str, Any] | None,
) -> dict[str, Any]:
    frontier_row = frontier_row or {}
    arrays = build_candle_arrays(candles)
    signal_map = build_atr_signal_map(arrays)
    default_atr = run_precomputed_atr_backtest(
        arrays,
        signal_map[(DEFAULT_ATR_PARAMS["atr_period"], float(DEFAULT_ATR_PARAMS["atr_mult"]))],
        tp_pct=DEFAULT_ATR_PARAMS["tp_pct"],
        sl_pct=DEFAULT_ATR_PARAMS["sl_pct"],
        max_hold=DEFAULT_ATR_PARAMS["max_hold"],
    )

    combos: list[dict[str, Any]] = []
    for atr_period in ATR_PERIODS:
        for atr_mult in ATR_MULTS:
            signals = signal_map[(atr_period, float(atr_mult))]
            for tp_pct in TP_PCTS:
                for sl_pct in SL_PCTS:
                    for max_hold in MAX_HOLDS:
                        result = run_precomputed_atr_backtest(
                            arrays,
                            signals,
                            tp_pct=tp_pct,
                            sl_pct=sl_pct,
                            max_hold=max_hold,
                        )
                        combos.append(
                            {
                                "coin": coin,
                                "atr_period": atr_period,
                                "atr_mult": atr_mult,
                                "tp_pct": tp_pct,
                                "sl_pct": sl_pct,
                                "max_hold": max_hold,
                                "net_pnl": round(to_float(result["net_pnl"]), 4),
                                "trades": int(result["trades"]),
                                "win_rate": round(to_float(result["win_rate"]), 1),
                                "max_drawdown": round(to_float(result["max_drawdown"]), 1),
                                "signals": int(result["signals"]),
                                "total_fees": round(to_float(result["total_fees"]), 4),
                            }
                        )

    combos.sort(
        key=lambda row: (
            -to_float(row["net_pnl"]),
            -to_float(row["win_rate"]),
            to_float(row["max_drawdown"]),
            row["atr_period"],
            row["atr_mult"],
            row["tp_pct"],
            row["sl_pct"],
            row["max_hold"],
        )
    )
    profitable = [row for row in combos if to_float(row["net_pnl"]) > 0.0]
    best = combos[0] if combos else {}
    frontier_best = to_float(frontier_row.get("best_net_pnl"))
    return {
        "coin": coin,
        "source": source,
        "candles": len(candles),
        "frontier_best_family": str(frontier_row.get("best_family") or "unknown"),
        "frontier_best_strategy_id": str(frontier_row.get("best_strategy_id") or "unknown"),
        "frontier_best_net_pnl": round(frontier_best, 4),
        "default_atr_net_pnl": round(to_float(default_atr["net_pnl"]), 4),
        "default_atr_trades": int(default_atr["trades"]),
        "total_combos": len(combos),
        "profitable_combos": len(profitable),
        "profitable_rate": round(len(profitable) / len(combos) * 100.0, 1) if combos else 0.0,
        "best_atr_period": best.get("atr_period"),
        "best_atr_mult": best.get("atr_mult"),
        "best_tp_pct": best.get("tp_pct"),
        "best_sl_pct": best.get("sl_pct"),
        "best_max_hold": best.get("max_hold"),
        "best_net_pnl": round(to_float(best.get("net_pnl")), 4),
        "best_trades": int(best.get("trades") or 0),
        "best_win_rate": round(to_float(best.get("win_rate")), 1),
        "best_max_drawdown": round(to_float(best.get("max_drawdown")), 1),
        "best_total_fees": round(to_float(best.get("total_fees")), 4),
        "uplift_vs_default_atr": round(to_float(best.get("net_pnl")) - to_float(default_atr["net_pnl"]), 4),
        "uplift_vs_frontier": round(to_float(best.get("net_pnl")) - frontier_best, 4),
        "beats_frontier_after_sweep": to_float(best.get("net_pnl")) > frontier_best,
        "top_combos": combos[:10],
    }


def build_leadership_read(coin_rows: list[dict[str, Any]]) -> list[str]:
    if not coin_rows:
        return ["No weak-board ATR rescue candidates were available from the current frontier reports."]

    strongest = max(coin_rows, key=lambda row: to_float(row["best_net_pnl"]))
    rescued = [row["coin"] for row in coin_rows if bool(row.get("beats_frontier_after_sweep")) and to_float(row.get("best_net_pnl")) > 0.0]
    robust = [row["coin"] for row in coin_rows if to_float(row["profitable_rate"]) >= 25.0 and to_float(row["best_net_pnl"]) > 0.0]
    thin = [row["coin"] for row in coin_rows if 0.0 < to_float(row["best_net_pnl"]) <= 10.0]

    lines = [
        f"{strongest['coin'].replace('-USD', '')} is the strongest ATR rescue candidate at ${strongest['best_net_pnl']:.2f} with `atr={strongest['best_atr_period']}, mult={strongest['best_atr_mult']}, tp={strongest['best_tp_pct']}, sl={strongest['best_sl_pct']}, hold={strongest['best_max_hold']}`.",
    ]
    if rescued:
        lines.append(
            f"{format_coin_list(rescued)} {plural_verb(rescued, 'beats', 'beat')} {plural_verb(rescued, 'its', 'their')} current family-frontier champion after ATR parameter discovery, so the rescue lane is real on those names."
        )
    else:
        lines.append("No weak-board coin beat its current family-frontier champion after ATR parameter discovery, so the rescue lane is still more triage than promotion.")
    if robust:
        lines.append(
            f"{format_coin_list(robust)} {plural_verb(robust, 'has', 'have')} a usable ATR search surface with at least 25% of tested combos staying positive."
        )
    if thin:
        lines.append(f"{format_coin_list(thin)} only scrape out thin positives, so they stay shadow-only even if ATR is technically their local best.")
    return lines


def build_payload(*, target_coins: list[str] | None = None) -> dict[str, Any]:
    target_coins = target_coins or load_target_coins()
    frontier_rows = build_frontier_row_map()
    coin_rows: list[dict[str, Any]] = []
    for coin in target_coins:
        candles, source = load_coin_candles(coin)
        if not candles:
            continue
        coin_rows.append(build_coin_payload(coin, candles, source, frontier_rows.get(coin)))
    coin_rows.sort(key=lambda row: (-to_float(row["best_net_pnl"]), row["coin"]))
    leaderboard_rows = [
        {
            "coin": row["coin"],
            "best_net_pnl": row["best_net_pnl"],
            "frontier_best_family": row["frontier_best_family"],
            "frontier_best_net_pnl": row["frontier_best_net_pnl"],
            "uplift_vs_default_atr": row["uplift_vs_default_atr"],
            "uplift_vs_frontier": row["uplift_vs_frontier"],
            "profitable_rate": row["profitable_rate"],
            "best_atr_period": row["best_atr_period"],
            "best_atr_mult": row["best_atr_mult"],
            "best_tp_pct": row["best_tp_pct"],
            "best_sl_pct": row["best_sl_pct"],
            "best_max_hold": row["best_max_hold"],
            "beats_frontier_after_sweep": row["beats_frontier_after_sweep"],
        }
        for row in coin_rows
    ]
    return {
        "generated_at": utc_now_iso(),
        "target_coins": target_coins,
        "leadership_read": build_leadership_read(coin_rows),
        "leaderboard_rows": leaderboard_rows,
        "coin_rows": coin_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase ATR Expansion Rescue Sweep",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Leaderboard",
            "",
            "| Coin | Best Net $ | Frontier Family | Frontier Net $ | Uplift vs Default ATR | Uplift vs Frontier | Profitable Rate | Best Params | Beats Frontier |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["leaderboard_rows"]:
        lines.append(
            "| {coin} | {best_net_pnl:.4f} | {frontier_best_family} | {frontier_best_net_pnl:.4f} | {uplift_vs_default_atr:.4f} | {uplift_vs_frontier:.4f} | {profitable_rate:.1f}% | atr={best_atr_period},mult={best_atr_mult},tp={best_tp_pct},sl={best_sl_pct},hold={best_max_hold} | {beats_frontier_after_sweep} |".format(
                **row
            )
        )
    for row in payload["coin_rows"]:
        lines.extend(
            [
                "",
                f"## {row['coin']}",
                "",
                f"- Source: `{row['source']}`",
                f"- Candles: `{row['candles']}`",
                f"- Frontier best: `{row['frontier_best_family']}` / `{row['frontier_best_strategy_id']}` at `${row['frontier_best_net_pnl']:.4f}`",
                f"- Default ATR: `${row['default_atr_net_pnl']:.4f}` over `{row['default_atr_trades']}` closes",
                f"- Best ATR: `${row['best_net_pnl']:.4f}` over `{row['best_trades']}` closes at `atr={row['best_atr_period']}, mult={row['best_atr_mult']}, tp={row['best_tp_pct']}, sl={row['best_sl_pct']}, hold={row['best_max_hold']}`",
                f"- Profitable combos: `{row['profitable_combos']}/{row['total_combos']}` (`{row['profitable_rate']:.1f}%`)",
                "",
                "| Rank | ATR | Mult | TP | SL | Hold | Net $ | WR | DD | Trades | Fees |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for idx, combo in enumerate(row["top_combos"], start=1):
            lines.append(
                "| {idx} | {atr_period} | {atr_mult:.2f} | {tp_pct:.1f} | {sl_pct:.1f} | {max_hold} | {net_pnl:.4f} | {win_rate:.1f} | {max_drawdown:.1f} | {trades} | {total_fees:.4f} |".format(
                    idx=idx,
                    **combo,
                )
            )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
