#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from live_coinbase_rsi_shadow import compute_rsi


ROOT = Path(__file__).resolve().parent.parent
RAVE_CACHE_PATH = ROOT / "reports" / "candle_cache" / "RAVE_USD_FIVE_MINUTE_30d.json"
BTC_CACHE_PATH = ROOT / "reports" / "candle_cache" / "BTC_USD_FIVE_MINUTE_7d.json"
OUT_JSON_PATH = ROOT / "reports" / "omni_vip_fortress_v4_salvage_benchmark.json"
OUT_MD_PATH = ROOT / "reports" / "omni_vip_fortress_v4_salvage_benchmark.md"

STARTING_CASH = 48.0
DEPLOY_PCT = 0.9
FEE_BPS = 5.0
FEE_RATE = FEE_BPS / 10000.0
RSI_PERIOD = 7
OVERSOLD = 30.0
OVERBOUGHT = 70.0
PROFIT_TARGET_PCT = 0.02
STOP_LOSS_PCT = 0.003
MAX_HOLD_BARS = 48


@dataclass
class Trade:
    entry_time: int
    entry_price: float
    quantity: float
    entry_fee: float
    entry_bar: int


def load_candles(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candles = payload.get("candles") or []
    out: list[dict[str, Any]] = []
    for candle in candles:
        out.append(
            {
                "time": int(candle["time"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume") or 0.0),
            }
        )
    out.sort(key=lambda row: int(row["time"]))
    return out


def magnetic_pass(price: float, proximity_pct: float) -> bool:
    if price <= 0:
        return False
    magnetic_level = round(price * 20.0) / 20.0
    return abs(price - magnetic_level) / magnetic_level <= proximity_pct


def btc_gate_pass(btc_lookup: dict[int, dict[str, Any]], ts: int, threshold_usd: float) -> bool:
    btc = btc_lookup.get(int(ts))
    if not btc:
        return False
    return (float(btc["close"]) - float(btc["open"])) >= float(threshold_usd)


def gate_pass(
    gate_name: str,
    candle: dict[str, Any],
    *,
    btc_lookup: dict[int, dict[str, Any]],
    magnetic_proximity: float,
    btc_threshold_usd: float,
) -> bool:
    if gate_name == "baseline":
        return True
    if gate_name == "recovery":
        return float(candle["close"]) > float(candle["open"])
    if gate_name == "magnetic":
        return magnetic_pass(float(candle["close"]), magnetic_proximity)
    if gate_name == "btc":
        return btc_gate_pass(btc_lookup, int(candle["time"]), btc_threshold_usd)
    if gate_name == "recovery_btc":
        return (
            float(candle["close"]) > float(candle["open"])
            and btc_gate_pass(btc_lookup, int(candle["time"]), btc_threshold_usd)
        )
    if gate_name == "magnetic_btc":
        return (
            magnetic_pass(float(candle["close"]), magnetic_proximity)
            and btc_gate_pass(btc_lookup, int(candle["time"]), btc_threshold_usd)
        )
    if gate_name == "combo":
        return (
            float(candle["close"]) > float(candle["open"])
            and magnetic_pass(float(candle["close"]), magnetic_proximity)
            and btc_gate_pass(btc_lookup, int(candle["time"]), btc_threshold_usd)
        )
    return False


def simulate_variant(
    rave_candles: list[dict[str, Any]],
    btc_lookup: dict[int, dict[str, Any]],
    *,
    gate_name: str,
    magnetic_proximity: float,
    btc_threshold_usd: float,
) -> dict[str, Any]:
    price_history: list[float] = []
    cash = STARTING_CASH
    trade: Trade | None = None
    realized_net = 0.0
    realized_closes = 0
    wins = 0
    losses = 0
    signals = 0

    overlapping = [candle for candle in rave_candles if int(candle["time"]) in btc_lookup]

    for idx, candle in enumerate(overlapping):
        close_price = float(candle["close"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        ts = int(candle["time"])

        price_history.append(close_price)
        if len(price_history) > RSI_PERIOD + 50:
            price_history = price_history[-(RSI_PERIOD + 50):]

        if trade is not None:
            current_rsi = compute_rsi(price_history, RSI_PERIOD)
            tp_price = trade.entry_price * (1.0 + PROFIT_TARGET_PCT)
            sl_price = trade.entry_price * (1.0 - STOP_LOSS_PCT)
            exit_reason = ""
            exit_price = close_price

            if high_price >= tp_price:
                exit_reason = "tp"
                exit_price = tp_price
            elif low_price <= sl_price:
                exit_reason = "sl"
                exit_price = sl_price
            elif current_rsi >= OVERBOUGHT:
                exit_reason = "rsi_exit"
            elif (idx - trade.entry_bar) >= MAX_HOLD_BARS:
                exit_reason = "timeout"

            if exit_reason:
                gross = (exit_price - trade.entry_price) * trade.quantity
                exit_fee = exit_price * trade.quantity * FEE_RATE
                net = gross - exit_fee
                cash += exit_price * trade.quantity - exit_fee
                realized_net += net
                realized_closes += 1
                if net >= 0:
                    wins += 1
                else:
                    losses += 1
                trade = None

        if trade is None and len(price_history) >= RSI_PERIOD + 1:
            current_rsi = compute_rsi(price_history, RSI_PERIOD)
            signals += 1
            if current_rsi <= OVERSOLD and gate_pass(
                gate_name,
                candle,
                btc_lookup=btc_lookup,
                magnetic_proximity=magnetic_proximity,
                btc_threshold_usd=btc_threshold_usd,
            ):
                deploy_usd = cash * DEPLOY_PCT
                if deploy_usd >= 1.0:
                    entry_fee = deploy_usd * FEE_RATE
                    quantity = (deploy_usd - entry_fee) / close_price
                    if quantity > 0:
                        cash -= deploy_usd
                        trade = Trade(
                            entry_time=ts,
                            entry_price=close_price,
                            quantity=quantity,
                            entry_fee=entry_fee,
                            entry_bar=idx,
                        )

    if trade is not None:
        exit_price = float(overlapping[-1]["close"])
        exit_fee = exit_price * trade.quantity * FEE_RATE
        gross = (exit_price - trade.entry_price) * trade.quantity
        net = gross - exit_fee
        cash += exit_price * trade.quantity - exit_fee
        realized_net += net
        realized_closes += 1
        if net >= 0:
            wins += 1
        else:
            losses += 1

    win_rate = (wins / realized_closes * 100.0) if realized_closes else 0.0
    return {
        "gate": gate_name,
        "magnetic_proximity_pct": round(magnetic_proximity * 100.0, 4),
        "btc_threshold_usd": round(btc_threshold_usd, 4),
        "bars": len(overlapping),
        "signals": signals,
        "realized_net_usd": round(realized_net, 4),
        "realized_closes": realized_closes,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "ending_cash_usd": round(cash, 4),
    }


def pick_best(results: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        results,
        key=lambda row: (
            float(row["realized_net_usd"]),
            int(row["realized_closes"]),
            float(row["win_rate_pct"]),
        ),
        reverse=True,
    )
    return ranked[0] if ranked else {}


def iso_from_epoch(value: int | None) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Omni VIP Fortress V4 Salvage Benchmark",
        "",
        "This benchmark replays the `RAVE-USD` supervised RSI baseline and tests simple fortress-style gates that are replayable from candle data.",
        "It is not proof of Kraken micro-lag alpha. The BTC gate here is only a coarse 5-minute Coinbase BTC proxy.",
        "",
        f"Source window: `{iso_from_epoch(payload['window_start_utc'])}` -> `{iso_from_epoch(payload['window_end_utc'])}`",
        "",
        "## Best Variant",
        "",
        f"- Gate: `{payload['best_variant'].get('gate')}`",
        f"- Net USD: `{payload['best_variant'].get('realized_net_usd')}`",
        f"- Closes: `{payload['best_variant'].get('realized_closes')}`",
        f"- Win rate: `{payload['best_variant'].get('win_rate_pct')}`",
        "",
        "## Variants",
        "",
        "| Gate | BTC Threshold | Magnetic Proximity % | Net USD | Closes | Win Rate % | Signals |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        lines.append(
            f"| {row['gate']} | {row['btc_threshold_usd']} | {row['magnetic_proximity_pct']} | "
            f"{row['realized_net_usd']} | {row['realized_closes']} | {row['win_rate_pct']} | {row['signals']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    rave_candles = load_candles(RAVE_CACHE_PATH)
    btc_candles = load_candles(BTC_CACHE_PATH)
    btc_lookup = {int(candle["time"]): candle for candle in btc_candles}

    results: list[dict[str, Any]] = []
    gate_configs = [
        ("baseline", 0.005, 0.0),
        ("recovery", 0.005, 0.0),
        ("magnetic", 0.0025, 0.0),
        ("magnetic", 0.0050, 0.0),
        ("btc", 0.005, 5.0),
        ("btc", 0.005, 10.0),
        ("recovery_btc", 0.005, 5.0),
        ("recovery_btc", 0.005, 10.0),
        ("magnetic_btc", 0.0025, 5.0),
        ("magnetic_btc", 0.0050, 5.0),
        ("combo", 0.0025, 5.0),
        ("combo", 0.0050, 5.0),
    ]

    for gate_name, magnetic_proximity, btc_threshold_usd in gate_configs:
        results.append(
            simulate_variant(
                rave_candles,
                btc_lookup,
                gate_name=gate_name,
                magnetic_proximity=magnetic_proximity,
                btc_threshold_usd=btc_threshold_usd,
            )
        )

    best_variant = pick_best(results)
    overlapping_rave = [c for c in rave_candles if int(c["time"]) in btc_lookup]
    payload = {
        "generated_at": int(__import__("time").time()),
        "window_start_utc": overlapping_rave[0]["time"] if overlapping_rave else None,
        "window_end_utc": overlapping_rave[-1]["time"] if overlapping_rave else None,
        "note": "BTC gate uses Coinbase BTC five-minute candles as a coarse proxy; this does not validate Kraken sub-minute lead claims.",
        "results": results,
        "best_variant": best_variant,
    }
    OUT_JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD_PATH.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(best_variant, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
