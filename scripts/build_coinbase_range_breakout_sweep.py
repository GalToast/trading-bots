#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_coinbase_momentum_reconciliation_queue as recon_runner
import strategy_library as strategy_lib


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

FRONTIER_PATH = REPORTS / "coinbase_strategy_family_frontier.json"
JSON_PATH = REPORTS / "coinbase_range_breakout_sweep.json"
MD_PATH = REPORTS / "coinbase_range_breakout_sweep.md"

FEE_RATE = 0.004
STARTING_CASH = 48.0
TARGET_COIN_LIMIT = 4

LOOKBACKS = [8, 10, 12, 15, 20, 25, 30, 40, 50]
TP_PCTS = [3.0, 5.0, 8.0, 10.0, 12.0, 15.0]
SL_PCTS = [0.0, 1.0, 2.0, 3.0, 5.0]
MAX_HOLDS = [12, 24, 36, 48]

DEFAULT_RANGE_PARAMS = {"lookback": 20, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 48}
DEFAULT_MOMENTUM_PARAMS = {"lookback": 10, "tp_pct": 10.0, "sl_pct": 10.0, "max_hold": 48}


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


def load_target_coins(limit: int = TARGET_COIN_LIMIT) -> list[str]:
    payload = load_json(FRONTIER_PATH)
    coin_rows = list(payload.get("coin_rows") or [])
    winners = [
        row for row in coin_rows
        if str(row.get("best_family") or "") == "range_breakout" and to_float(row.get("best_net_pnl")) > 0.0
    ]
    winners.sort(key=lambda row: (-to_float(row.get("best_net_pnl")), str(row.get("coin") or "")))
    return [str(row["coin"]) for row in winners[:limit]]


def load_coin_candles(coin: str) -> tuple[list[dict[str, str]], str]:
    snapshot_map = recon_runner.load_snapshot_map()
    candles = snapshot_map.get(coin)
    if candles:
        return candles, "snapshot"
    return recon_runner.load_cache_candles(coin), "cache"


def run_range_breakout(candles: list[dict[str, str]], *, lookback: int, tp_pct: float, sl_pct: float, max_hold: int) -> dict[str, Any]:
    return strategy_lib.range_breakout(
        candles,
        range_lookback=lookback,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        max_hold=max_hold,
        fee_rate=FEE_RATE,
        starting_cash=STARTING_CASH,
        entry_slip=0.0,
        exit_slip=0.0,
        fill_prob=1.0,
    )


def run_momentum_baseline(candles: list[dict[str, str]]) -> dict[str, Any]:
    return strategy_lib.momentum(
        candles,
        fee_rate=FEE_RATE,
        starting_cash=STARTING_CASH,
        entry_slip=0.0,
        exit_slip=0.0,
        fill_prob=1.0,
        **DEFAULT_MOMENTUM_PARAMS,
    )


def build_coin_payload(coin: str, candles: list[dict[str, str]], source: str) -> dict[str, Any]:
    default_breakout = run_range_breakout(candles, **DEFAULT_RANGE_PARAMS)
    default_momentum = run_momentum_baseline(candles)

    combos: list[dict[str, Any]] = []
    for lookback in LOOKBACKS:
        for tp_pct in TP_PCTS:
            for sl_pct in SL_PCTS:
                for max_hold in MAX_HOLDS:
                    result = run_range_breakout(
                        candles,
                        lookback=lookback,
                        tp_pct=tp_pct,
                        sl_pct=sl_pct,
                        max_hold=max_hold,
                    )
                    combos.append(
                        {
                            "coin": coin,
                            "range_lookback": lookback,
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
            row["range_lookback"],
            row["tp_pct"],
            row["sl_pct"],
            row["max_hold"],
        )
    )
    profitable = [row for row in combos if to_float(row["net_pnl"]) > 0.0]
    best = combos[0] if combos else {}
    return {
        "coin": coin,
        "source": source,
        "candles": len(candles),
        "default_range_breakout_net_pnl": round(to_float(default_breakout["net_pnl"]), 4),
        "default_range_breakout_trades": int(default_breakout["trades"]),
        "default_momentum_net_pnl": round(to_float(default_momentum["net_pnl"]), 4),
        "default_momentum_trades": int(default_momentum["trades"]),
        "total_combos": len(combos),
        "profitable_combos": len(profitable),
        "profitable_rate": round(len(profitable) / len(combos) * 100.0, 1) if combos else 0.0,
        "best_range_lookback": best.get("range_lookback"),
        "best_tp_pct": best.get("tp_pct"),
        "best_sl_pct": best.get("sl_pct"),
        "best_max_hold": best.get("max_hold"),
        "best_net_pnl": round(to_float(best.get("net_pnl")), 4),
        "best_trades": int(best.get("trades") or 0),
        "best_win_rate": round(to_float(best.get("win_rate")), 1),
        "best_max_drawdown": round(to_float(best.get("max_drawdown")), 1),
        "best_total_fees": round(to_float(best.get("total_fees")), 4),
        "uplift_vs_default_breakout": round(to_float(best.get("net_pnl")) - to_float(default_breakout["net_pnl"]), 4),
        "uplift_vs_default_momentum": round(to_float(best.get("net_pnl")) - to_float(default_momentum["net_pnl"]), 4),
        "top_combos": combos[:10],
    }


def build_leadership_read(coin_rows: list[dict[str, Any]]) -> list[str]:
    if not coin_rows:
        return ["No target range-breakout coins were available from the strategy-family frontier board."]

    strongest = max(coin_rows, key=lambda row: to_float(row["best_net_pnl"]))
    breakout_beats_momentum = [row["coin"] for row in coin_rows if to_float(row["uplift_vs_default_momentum"]) > 0.0]
    robust = [row["coin"] for row in coin_rows if to_float(row["profitable_rate"]) >= 40.0 and to_float(row["best_net_pnl"]) > 0.0]
    thin = [row["coin"] for row in coin_rows if 0.0 < to_float(row["best_net_pnl"]) <= 20.0]

    lines = [
        f"{strongest['coin'].replace('-USD', '')} is the headline breakout-continuation lane with best range_breakout net ${strongest['best_net_pnl']:.2f} at `lb={strongest['best_range_lookback']}, tp={strongest['best_tp_pct']}, sl={strongest['best_sl_pct']}, hold={strongest['best_max_hold']}`.",
    ]
    if breakout_beats_momentum:
        lines.append(
            f"{format_coin_list(breakout_beats_momentum)} still beat the shared momentum baseline after optimization, so the frontier handoff is real rather than a default-param fluke."
        )
    if robust:
        lines.append(
            f"{format_coin_list(robust)} have a useful breakout search surface, with at least 40% of the tested combos staying profitable."
        )
    if thin:
        lines.append(
            f"{format_coin_list(thin)} remain thin even after the sweep, so they belong in watch-only or router-secondary roles instead of immediate promotion."
        )
    return lines


def build_payload(*, target_coins: list[str] | None = None) -> dict[str, Any]:
    target_coins = target_coins or load_target_coins()
    coin_rows: list[dict[str, Any]] = []
    for coin in target_coins:
        candles, source = load_coin_candles(coin)
        if not candles:
            continue
        coin_rows.append(build_coin_payload(coin, candles, source))
    coin_rows.sort(key=lambda row: (-to_float(row["best_net_pnl"]), row["coin"]))
    leaderboard_rows = [
        {
            "coin": row["coin"],
            "best_net_pnl": row["best_net_pnl"],
            "uplift_vs_default_breakout": row["uplift_vs_default_breakout"],
            "uplift_vs_default_momentum": row["uplift_vs_default_momentum"],
            "profitable_rate": row["profitable_rate"],
            "best_range_lookback": row["best_range_lookback"],
            "best_tp_pct": row["best_tp_pct"],
            "best_sl_pct": row["best_sl_pct"],
            "best_max_hold": row["best_max_hold"],
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
        "# Coinbase Range Breakout Sweep",
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
            "| Coin | Best Net $ | Uplift vs Default Breakout | Uplift vs Momentum | Profitable Rate | Best Params |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["leaderboard_rows"]:
        lines.append(
            "| {coin} | {best_net_pnl:.4f} | {uplift_vs_default_breakout:.4f} | {uplift_vs_default_momentum:.4f} | {profitable_rate:.1f}% | lb={best_range_lookback},tp={best_tp_pct},sl={best_sl_pct},hold={best_max_hold} |".format(
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
                f"- Default range_breakout: `${row['default_range_breakout_net_pnl']:.4f}` over `{row['default_range_breakout_trades']}` closes",
                f"- Default momentum: `${row['default_momentum_net_pnl']:.4f}` over `{row['default_momentum_trades']}` closes",
                f"- Best breakout: `${row['best_net_pnl']:.4f}` over `{row['best_trades']}` closes at `lb={row['best_range_lookback']}, tp={row['best_tp_pct']}, sl={row['best_sl_pct']}, hold={row['best_max_hold']}`",
                f"- Profitable combos: `{row['profitable_combos']}/{row['total_combos']}` (`{row['profitable_rate']:.1f}%`)",
                "",
                "| Rank | LB | TP | SL | Hold | Net $ | WR | DD | Trades | Fees |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for idx, combo in enumerate(row["top_combos"], start=1):
            lines.append(
                "| {idx} | {range_lookback} | {tp_pct:.1f} | {sl_pct:.1f} | {max_hold} | {net_pnl:.4f} | {win_rate:.1f} | {max_drawdown:.1f} | {trades} | {total_fees:.4f} |".format(
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
