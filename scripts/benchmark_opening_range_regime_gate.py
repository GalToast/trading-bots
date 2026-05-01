#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from candle_cache_service import load_candles
import strategy_library as strategy_lib


ROOT = SCRIPTS_DIR.parent
REPORT_JSON = ROOT / "reports" / "opening_range_regime_gate_research.json"
REPORT_MD = ROOT / "reports" / "opening_range_regime_gate_research.md"

COINS = [
    "RAVE-USD",
    "NOM-USD",
    "SUP-USD",
    "TRU-USD",
    "GHST-USD",
    "A8-USD",
    "BAL-USD",
    "PRL-USD",
    "IOTX-USD",
    "CFG-USD",
]

OPENING_RANGE_PARAM_SETS = [
    {"opening_bars": 6, "breakout_buffer_pct": 0.0, "tp_pct": 6.0, "sl_pct": 3.0, "max_hold": 18, "label": "orb_6_6tp_3sl_18h"},
    {"opening_bars": 6, "breakout_buffer_pct": 0.2, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24, "label": "orb_6_8tp_4sl_24h"},
    {"opening_bars": 12, "breakout_buffer_pct": 0.0, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24, "label": "orb_12_8tp_4sl_24h"},
    {"opening_bars": 12, "breakout_buffer_pct": 0.3, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 30, "label": "orb_12_10tp_5sl_30h"},
]

REGIME_GATED_PARAM_SETS = [
    {"lookback": 10, "ema_period": 30, "atr_period": 10, "trend_lookback": 8, "min_atr_pct": 0.8, "min_trend_pct": 0.8, "min_ema_slope_pct": 0.02, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 18, "label": "rgm_fast"},
    {"lookback": 12, "ema_period": 40, "atr_period": 14, "trend_lookback": 10, "min_atr_pct": 1.0, "min_trend_pct": 1.0, "min_ema_slope_pct": 0.03, "tp_pct": 8.0, "sl_pct": 4.0, "max_hold": 24, "label": "rgm_mid"},
    {"lookback": 20, "ema_period": 50, "atr_period": 14, "trend_lookback": 12, "min_atr_pct": 1.2, "min_trend_pct": 1.2, "min_ema_slope_pct": 0.04, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 24, "label": "rgm_strict"},
    {"lookback": 20, "ema_period": 80, "atr_period": 20, "trend_lookback": 16, "min_atr_pct": 1.5, "min_trend_pct": 1.5, "min_ema_slope_pct": 0.05, "tp_pct": 12.0, "sl_pct": 6.0, "max_hold": 30, "label": "rgm_trend"},
]


def run_family(
    coin: str,
    candles: list[dict],
    family_name: str,
    fn,
    param_sets: list[dict],
) -> list[dict]:
    rows: list[dict] = []
    for params in param_sets:
        run_params = dict(params)
        label = str(run_params.pop("label"))
        result = fn(
            candles,
            starting_cash=48.0,
            fee_rate=0.004,
            entry_slip=0.0,
            exit_slip=0.0,
            fill_prob=1.0,
            **run_params,
        )
        rows.append(
            {
                "coin": coin,
                "family": family_name,
                "label": label,
                "params": params,
                "net_pnl": result["net_pnl"],
                "trades": result["trades"],
                "win_rate": result["win_rate"],
                "max_drawdown": result["max_drawdown"],
                "signals": result["signals"],
                "total_fees": result["total_fees"],
            }
        )
    return rows


def summarize_best(rows: list[dict], family_name: str) -> list[dict]:
    by_coin: dict[str, list[dict]] = {}
    for row in rows:
        if row["family"] != family_name:
            continue
        by_coin.setdefault(row["coin"], []).append(row)
    best_rows: list[dict] = []
    for coin, coin_rows in by_coin.items():
        coin_rows.sort(key=lambda row: (float(row["net_pnl"]), float(row["win_rate"])), reverse=True)
        best_rows.append(coin_rows[0])
    best_rows.sort(key=lambda row: float(row["net_pnl"]), reverse=True)
    return best_rows


def main() -> int:
    all_rows: list[dict] = []
    missing: list[str] = []

    for coin in COINS:
        candles = load_candles(coin, "FIVE_MINUTE", days=30, max_age_minutes=1440)
        if len(candles) < 200:
            missing.append(coin)
            continue
        all_rows.extend(run_family(coin, candles, "opening_range_breakout", strategy_lib.opening_range_breakout, OPENING_RANGE_PARAM_SETS))
        all_rows.extend(run_family(coin, candles, "regime_gated_momentum", strategy_lib.regime_gated_momentum, REGIME_GATED_PARAM_SETS))

    payload = {
        "coins_requested": COINS,
        "coins_missing": missing,
        "rows": all_rows,
        "best_opening_range_breakout": summarize_best(all_rows, "opening_range_breakout"),
        "best_regime_gated_momentum": summarize_best(all_rows, "regime_gated_momentum"),
    }
    REPORT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Opening Range + Regime-Gated Momentum Research",
        "",
        "30d focused sweep through `strategy_library.py` with 40bps fees, $48 start, zero slippage for comparability.",
        "",
    ]
    if missing:
        lines.append(f"Missing cache: {', '.join(missing)}")
        lines.append("")

    for family_name, title in (
        ("opening_range_breakout", "Best Opening Range Breakout"),
        ("regime_gated_momentum", "Best Regime-Gated Momentum"),
    ):
        lines.extend(
            [
                f"## {title}",
                "",
                "| Coin | Label | Net $ | Trades | Win Rate % | Max DD % | Signals |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in summarize_best(all_rows, family_name):
            lines.append(
                "| {coin} | {label} | {net_pnl:.2f} | {trades} | {win_rate:.1f} | {max_drawdown:.1f} | {signals} |".format(
                    **row
                )
            )
        lines.append("")

    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Wrote {REPORT_JSON}")
    print(f"Wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
