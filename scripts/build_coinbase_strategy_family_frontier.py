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

NEXT_LAUNCH_WAVE_PATH = REPORTS / "coinbase_spot_next_launch_wave.json"
JSON_PATH = REPORTS / "coinbase_strategy_family_frontier.json"
MD_PATH = REPORTS / "coinbase_strategy_family_frontier.md"

FEE_RATE = 0.004
STARTING_CASH = 48.0
MAX_COINS = 12

STRATEGY_SPECS = [
    {
        "family": "momentum",
        "strategy_id": "momentum_lb10",
        "fn": "momentum",
        "params": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 10.0, "max_hold": 48},
    },
    {
        "family": "rsi_mr",
        "strategy_id": "rsi_mr_default",
        "fn": "rsi_mr",
        "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 25.0, "sl_pct": 0.0, "max_hold": 48},
    },
    {
        "family": "bb_reversion",
        "strategy_id": "bb_reversion_default",
        "fn": "bb_reversion",
        "params": {"bb_period": 20, "rsi_period": 3, "rsi_thresh": 30, "proximity_pct": 3.0, "sl_pct": 5.0, "max_hold": 24},
    },
    {
        "family": "vol_squeeze",
        "strategy_id": "vol_squeeze_default",
        "fn": "vol_squeeze",
        "params": {"bb_period": 20, "squeeze_thresh": 2.0, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 48},
    },
    {
        "family": "atr_expansion",
        "strategy_id": "atr_expansion_default",
        "fn": "atr_expansion",
        "params": {"atr_period": 14, "atr_mult": 1.5, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24},
    },
    {
        "family": "keltner_breakout",
        "strategy_id": "keltner_breakout_default",
        "fn": "keltner_breakout",
        "params": {"k_period": 20, "k_mult": 2.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 24},
    },
    {
        "family": "hist_vol_squeeze",
        "strategy_id": "hist_vol_squeeze_default",
        "fn": "hist_vol_squeeze",
        "params": {"hv_period": 20, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 36},
    },
    {
        "family": "ema_pullback",
        "strategy_id": "ema_pullback_default",
        "fn": "ema_pullback",
        "params": {"ema_period": 50, "rsi_period": 3, "rsi_thresh": 40, "tp_pct": 5.0, "sl_pct": 5.0, "max_hold": 48},
    },
    {
        "family": "range_breakout",
        "strategy_id": "range_breakout_default",
        "fn": "range_breakout",
        "params": {"range_lookback": 20, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 24},
    },
    {
        "family": "vwap_reversion",
        "strategy_id": "vwap_reversion_default",
        "fn": "vwap_reversion",
        "params": {"vwap_window": 48, "vwap_dev_pct": 2.0, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 24},
    },
    {
        "family": "volume_spike_reversion",
        "strategy_id": "volume_spike_reversion_default",
        "fn": "volume_spike_reversion",
        "params": {"rsi_period": 3, "os_thresh": 30, "vol_mult": 2.0, "vol_lookback": 20, "tp_pct": 15.0, "sl_pct": 5.0, "max_hold": 36},
    },
    {
        "family": "multi_tf_rsi",
        "strategy_id": "multi_tf_rsi_default",
        "fn": "multi_tf_rsi",
        "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 20.0, "sl_pct": 5.0, "max_hold": 36},
    },
]


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


def verb(items: list[Any], singular: str, plural: str) -> str:
    return singular if len(items) == 1 else plural


def load_candidate_coins(max_coins: int = MAX_COINS) -> list[str]:
    payload = load_json(NEXT_LAUNCH_WAVE_PATH)
    rows = list(payload.get("rows") or [])
    allowed_waves = {"maintain_live", "launch_now", "launch_after_wave_1", "watch_only", "router_hold"}
    candidates: list[str] = []
    seen: set[str] = set()
    for row in rows:
        coin = str(row.get("coin") or "")
        if not coin or coin in seen:
            continue
        if str(row.get("launch_wave") or "") not in allowed_waves:
            continue
        candidates.append(coin)
        seen.add(coin)
        if len(candidates) >= max_coins:
            break
    return candidates


def run_strategy(spec: dict[str, Any], candles: list[dict[str, str]]) -> dict[str, Any]:
    strategy_fn = getattr(strategy_lib, str(spec["fn"]))
    return strategy_fn(
        candles,
        fee_rate=FEE_RATE,
        starting_cash=STARTING_CASH,
        entry_slip=0.0,
        exit_slip=0.0,
        **dict(spec["params"]),
    )


def build_result_rows(candidate_coins: list[str]) -> list[dict[str, Any]]:
    snapshot_map = recon_runner.load_snapshot_map()
    rows: list[dict[str, Any]] = []
    for coin in candidate_coins:
        candles = snapshot_map.get(coin) or recon_runner.load_cache_candles(coin)
        if not candles:
            rows.append(
                {
                    "coin": coin,
                    "family": "missing",
                    "strategy_id": "missing",
                    "source": "missing",
                    "verdict": "missing_candles",
                }
            )
            continue
        source = "snapshot" if snapshot_map.get(coin) else "cache"
        for spec in STRATEGY_SPECS:
            result = run_strategy(spec, candles)
            net_pnl = round(to_float(result["net_pnl"]), 4)
            rows.append(
                {
                    "coin": coin,
                    "family": spec["family"],
                    "strategy_id": spec["strategy_id"],
                    "source": source,
                    "net_pnl": net_pnl,
                    "trades": int(result["trades"]),
                    "win_rate": round(to_float(result["win_rate"]), 1),
                    "max_drawdown": round(to_float(result["max_drawdown"]), 1),
                    "signals": int(result["signals"]),
                    "total_fees": round(to_float(result["total_fees"]), 4),
                    "verdict": "positive" if net_pnl > 0.0 else ("negative" if net_pnl < 0.0 else "flat"),
                }
            )
    return rows


def build_family_rows(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    family_map: dict[str, list[dict[str, Any]]] = {}
    for row in result_rows:
        if row.get("family") == "missing":
            continue
        family_map.setdefault(str(row["family"]), []).append(row)

    rows: list[dict[str, Any]] = []
    for family, family_rows in family_map.items():
        tested = len(family_rows)
        positive_rows = [row for row in family_rows if to_float(row.get("net_pnl")) > 0.0]
        best_row = max(family_rows, key=lambda row: to_float(row.get("net_pnl")))
        worst_row = min(family_rows, key=lambda row: to_float(row.get("net_pnl")))
        total_net = round(sum(to_float(row.get("net_pnl")) for row in family_rows), 4)
        rows.append(
            {
                "family": family,
                "tested_coins": tested,
                "positive_coins": len(positive_rows),
                "positive_rate": round(len(positive_rows) / tested * 100.0, 1) if tested else 0.0,
                "total_net_pnl": total_net,
                "avg_net_pnl": round(total_net / tested, 4) if tested else 0.0,
                "total_fees": round(sum(to_float(row.get("total_fees")) for row in family_rows), 4),
                "best_coin": str(best_row["coin"]),
                "best_coin_net_pnl": round(to_float(best_row.get("net_pnl")), 4),
                "worst_coin": str(worst_row["coin"]),
                "worst_coin_net_pnl": round(to_float(worst_row.get("net_pnl")), 4),
            }
        )
    rows.sort(key=lambda row: (-to_float(row["total_net_pnl"]), -to_float(row["positive_rate"]), row["family"]))
    return rows


def build_coin_rows(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coin_map: dict[str, list[dict[str, Any]]] = {}
    for row in result_rows:
        if row.get("family") == "missing":
            continue
        coin_map.setdefault(str(row["coin"]), []).append(row)

    rows: list[dict[str, Any]] = []
    for coin, coin_rows in coin_map.items():
        best_row = max(coin_rows, key=lambda row: to_float(row.get("net_pnl")))
        positive = [row for row in coin_rows if to_float(row.get("net_pnl")) > 0.0]
        rows.append(
            {
                "coin": coin,
                "best_family": str(best_row["family"]),
                "best_strategy_id": str(best_row["strategy_id"]),
                "best_net_pnl": round(to_float(best_row.get("net_pnl")), 4),
                "best_trades": int(best_row.get("trades") or 0),
                "best_win_rate": round(to_float(best_row.get("win_rate")), 1),
                "best_max_drawdown": round(to_float(best_row.get("max_drawdown")), 1),
                "positive_family_count": len(positive),
                "tested_family_count": len(coin_rows),
            }
        )
    rows.sort(key=lambda row: (-to_float(row["best_net_pnl"]), row["coin"]))
    return rows


def build_leadership_read(family_rows: list[dict[str, Any]], coin_rows: list[dict[str, Any]], missing_coins: list[str]) -> list[str]:
    if not family_rows:
        return ["No strategy-family frontier could be built because there were no cache or snapshot candles for the selected coins."]

    top_family = family_rows[0]
    lines = [
        f"{top_family['family']} is the current frontier leader on the shared engine with ${top_family['total_net_pnl']:.2f} across {top_family['tested_coins']} coins and {top_family['positive_coins']} positives.",
    ]

    non_momentum = [row for row in family_rows if row["family"] not in {"momentum", "rsi_mr"} and to_float(row["total_net_pnl"]) > 0.0]
    if non_momentum:
        lines.append(
            f"Non-momentum families are not dead here: {', '.join(row['family'] for row in non_momentum[:3])} {verb(non_momentum[:3], 'stays', 'stay')} net positive on representative defaults."
        )
    else:
        lines.append("Representative default settings still say most non-momentum families are fee-fragile on this board, so creativity needs parameter discovery rather than blind promotion.")

    non_momentum_coin_winners = [row for row in coin_rows if row["best_family"] not in {"momentum", "rsi_mr"} and to_float(row["best_net_pnl"]) > 0.0]
    if non_momentum_coin_winners:
        winner_list = format_coin_list([row["coin"] for row in non_momentum_coin_winners[:4]])
        lines.append(f"The most interesting cross-family handoff coins right now are {winner_list}, where the best representative family is neither plain momentum nor plain RSI mean reversion.")
    else:
        lines.append("No coin in this first pass is best served by a non-momentum, non-RSI family yet.")

    if missing_coins:
        lines.append(f"Missing candle data kept {format_coin_list(missing_coins)} out of the first-pass frontier board.")
    return lines


def build_payload_from_rows(result_rows: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    missing_coins = [str(row["coin"]) for row in result_rows if row.get("verdict") == "missing_candles"]
    tested_rows = [row for row in result_rows if row.get("verdict") != "missing_candles"]
    family_rows = build_family_rows(tested_rows)
    coin_rows = build_coin_rows(tested_rows)
    return {
        "generated_at": now.isoformat(),
        "candidate_coins": sorted({str(row["coin"]) for row in result_rows}),
        "leadership_read": build_leadership_read(family_rows, coin_rows, missing_coins),
        "family_rows": family_rows,
        "coin_rows": coin_rows,
        "result_rows": tested_rows,
        "missing_coins": missing_coins,
    }


def build_payload(*, max_coins: int = MAX_COINS) -> dict[str, Any]:
    candidate_coins = load_candidate_coins(max_coins=max_coins)
    result_rows = build_result_rows(candidate_coins)
    return build_payload_from_rows(result_rows)


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)

    lines = [
        "# Coinbase Strategy Family Frontier",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Family Rows",
            "",
            "| Family | Tested Coins | Positive Coins | Positive Rate | Total Net $ | Avg Net $ | Best Coin | Best $ | Worst Coin | Worst $ | Total Fees |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: |",
        ]
    )
    for row in payload["family_rows"]:
        lines.append(
            "| {family} | {tested_coins} | {positive_coins} | {positive_rate:.1f}% | {total_net_pnl:.4f} | {avg_net_pnl:.4f} | {best_coin} | {best_coin_net_pnl:.4f} | {worst_coin} | {worst_coin_net_pnl:.4f} | {total_fees:.4f} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Coin Winners",
            "",
            "| Coin | Best Family | Best Strategy | Best Net $ | Trades | WR | DD | Positive Families | Tested Families |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["coin_rows"]:
        lines.append(
            "| {coin} | {best_family} | {best_strategy_id} | {best_net_pnl:.4f} | {best_trades} | {best_win_rate:.1f} | {best_max_drawdown:.1f} | {positive_family_count} | {tested_family_count} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Result Rows",
            "",
            "| Coin | Family | Strategy | Source | Net $ | Trades | WR | DD | Signals | Fees | Verdict |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["result_rows"]:
        lines.append(
            "| {coin} | {family} | {strategy_id} | {source} | {net_pnl:.4f} | {trades} | {win_rate:.1f} | {max_drawdown:.1f} | {signals} | {total_fees:.4f} | {verdict} |".format(
                **row
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
