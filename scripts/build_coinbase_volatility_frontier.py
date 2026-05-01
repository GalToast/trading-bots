#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_coinbase_strategy_family_frontier as family_frontier
import run_coinbase_momentum_reconciliation_queue as recon_runner
import strategy_library as strategy_lib


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

JSON_PATH = REPORTS / "coinbase_volatility_frontier.json"
MD_PATH = REPORTS / "coinbase_volatility_frontier.md"
FAMILY_FRONTIER_JSON_PATH = REPORTS / "coinbase_strategy_family_frontier.json"

STRATEGY_SPECS = [
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
]


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_family_frontier_map(payload: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    raw_payload = payload if payload is not None else load_json(FAMILY_FRONTIER_JSON_PATH)
    rows = list(raw_payload.get("coin_rows") or [])
    return {str(row.get("coin") or ""): row for row in rows if row.get("coin")}


def run_strategy(spec: dict[str, Any], candles: list[dict[str, str]]) -> dict[str, Any]:
    strategy_fn = getattr(strategy_lib, str(spec["fn"]))
    return strategy_fn(
        candles,
        fee_rate=family_frontier.FEE_RATE,
        starting_cash=family_frontier.STARTING_CASH,
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
            net_pnl = round(family_frontier.to_float(result["net_pnl"]), 4)
            rows.append(
                {
                    "coin": coin,
                    "family": spec["family"],
                    "strategy_id": spec["strategy_id"],
                    "source": source,
                    "net_pnl": net_pnl,
                    "trades": int(result["trades"]),
                    "win_rate": round(family_frontier.to_float(result["win_rate"]), 1),
                    "max_drawdown": round(family_frontier.to_float(result["max_drawdown"]), 1),
                    "signals": int(result["signals"]),
                    "total_fees": round(family_frontier.to_float(result["total_fees"]), 4),
                    "verdict": "positive" if net_pnl > 0.0 else ("negative" if net_pnl < 0.0 else "flat"),
                }
            )
    return rows


def build_coin_rows(
    result_rows: list[dict[str, Any]],
    family_frontier_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    coin_map: dict[str, list[dict[str, Any]]] = {}
    for row in result_rows:
        if row.get("family") == "missing":
            continue
        coin_map.setdefault(str(row["coin"]), []).append(row)

    rows: list[dict[str, Any]] = []
    for coin, coin_rows in coin_map.items():
        best_row = max(coin_rows, key=lambda row: family_frontier.to_float(row.get("net_pnl")))
        benchmark_row = family_frontier_map.get(coin) or {}
        benchmark_net = round(family_frontier.to_float(benchmark_row.get("best_net_pnl")), 4)
        best_net = round(family_frontier.to_float(best_row.get("net_pnl")), 4)
        rows.append(
            {
                "coin": coin,
                "best_vol_family": str(best_row["family"]),
                "best_vol_strategy_id": str(best_row["strategy_id"]),
                "best_vol_net_pnl": best_net,
                "best_vol_trades": int(best_row.get("trades") or 0),
                "best_vol_win_rate": round(family_frontier.to_float(best_row.get("win_rate")), 1),
                "best_vol_max_drawdown": round(family_frontier.to_float(best_row.get("max_drawdown")), 1),
                "positive_vol_family_count": len([row for row in coin_rows if family_frontier.to_float(row.get("net_pnl")) > 0.0]),
                "tested_vol_family_count": len(coin_rows),
                "family_frontier_best_family": str(benchmark_row.get("best_family") or "unknown"),
                "family_frontier_best_net_pnl": benchmark_net,
                "beats_family_frontier": best_net > benchmark_net,
            }
        )
    rows.sort(key=lambda row: (-family_frontier.to_float(row["best_vol_net_pnl"]), row["coin"]))
    return rows


def build_leadership_read(
    family_rows: list[dict[str, Any]],
    coin_rows: list[dict[str, Any]],
    missing_coins: list[str],
) -> list[str]:
    if not family_rows:
        return ["No volatility frontier could be built because there were no cache or snapshot candles for the selected coins."]

    top_family = family_rows[0]
    lines = [
        f"{top_family['family']} is the current volatility-family leader with ${top_family['total_net_pnl']:.2f} across {top_family['tested_coins']} coins and {top_family['positive_coins']} positives.",
    ]

    positive_families = [row for row in family_rows if family_frontier.to_float(row.get("total_net_pnl")) > 0.0]
    if positive_families:
        lines.append(
            f"At least one volatility family survives fees on representative defaults: {', '.join(row['family'] for row in positive_families)}."
        )
    else:
        lines.append("All three volatility families are net negative on representative defaults, so this lane is research-only until parameter discovery proves otherwise.")

    crossover_rows = [row for row in coin_rows if row.get("beats_family_frontier")]
    if crossover_rows:
        winner_list = family_frontier.format_coin_list([str(row["coin"]) for row in crossover_rows[:4]])
        lines.append(f"Volatility logic actually beats the existing family frontier on {winner_list}, so those coins deserve follow-up instead of blanket rejection.")
    else:
        lines.append("No tested coin is better served by a default volatility family than by its current best family-frontier lane, so there is no immediate promotion candidate here.")

    if missing_coins:
        lines.append(f"Missing candle data kept {family_frontier.format_coin_list(missing_coins)} out of the volatility pass.")
    return lines


def build_payload_from_rows(
    result_rows: list[dict[str, Any]],
    *,
    family_frontier_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    missing_coins = [str(row["coin"]) for row in result_rows if row.get("verdict") == "missing_candles"]
    tested_rows = [row for row in result_rows if row.get("verdict") != "missing_candles"]
    family_rows = family_frontier.build_family_rows(tested_rows)
    family_frontier_map = build_family_frontier_map(family_frontier_payload)
    coin_rows = build_coin_rows(tested_rows, family_frontier_map)
    return {
        "generated_at": now.isoformat(),
        "candidate_coins": sorted({str(row["coin"]) for row in result_rows}),
        "leadership_read": build_leadership_read(family_rows, coin_rows, missing_coins),
        "family_rows": family_rows,
        "coin_rows": coin_rows,
        "result_rows": tested_rows,
        "missing_coins": missing_coins,
    }


def build_payload(*, max_coins: int = family_frontier.MAX_COINS) -> dict[str, Any]:
    candidate_coins = family_frontier.load_candidate_coins(max_coins=max_coins)
    result_rows = build_result_rows(candidate_coins)
    return build_payload_from_rows(result_rows)


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)

    lines = [
        "# Coinbase Volatility Frontier",
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
            "## Coin Rows",
            "",
            "| Coin | Best Vol Family | Best Vol Strategy | Best Vol Net $ | Trades | WR | DD | Positive Vol Families | Tested Vol Families | Frontier Best Family | Frontier Best $ | Beats Frontier |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for row in payload["coin_rows"]:
        lines.append(
            "| {coin} | {best_vol_family} | {best_vol_strategy_id} | {best_vol_net_pnl:.4f} | {best_vol_trades} | {best_vol_win_rate:.1f} | {best_vol_max_drawdown:.1f} | {positive_vol_family_count} | {tested_vol_family_count} | {family_frontier_best_family} | {family_frontier_best_net_pnl:.4f} | {beats_family_frontier} |".format(
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
