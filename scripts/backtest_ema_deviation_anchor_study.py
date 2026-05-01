#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from backtest_adaptive_deployment_study import load_bars
from penetration_lattice_lab_v2 import unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "ema_deviation_anchor_study.csv"
OUTPUT_JSON = ROOT / "reports" / "ema_deviation_anchor_study.json"
OUTPUT_MD = ROOT / "reports" / "ema_deviation_anchor_study.md"

DEFAULT_TIMEFRAME_BY_SYMBOL = {
    "BTCUSD": "M15",
    "ETHUSD": "M5",
    "SOLUSD": "M15",
    "XRPUSD": "M15",
    "ADAUSD": "M15",
    "LTCUSD": "M15",
}


@dataclass(frozen=True)
class StudyContract:
    symbol: str
    timeframe: str
    trigger_mode: str
    anchor_mode: str
    close_mode: str
    ema_period: int
    deviation_atr: float
    volume: float

    @property
    def label(self) -> str:
        return (
            f"{self.trigger_mode}|{self.anchor_mode}|{self.close_mode}|"
            f"ema{self.ema_period}|dev{self.deviation_atr:.2f}atr"
        )


@dataclass
class Ticket:
    direction: str
    entry_price: float
    anchor_price: float
    opened_time: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_ema(values: list[float], period: int) -> list[float]:
    rows: list[float] = []
    alpha = 2.0 / (float(period) + 1.0)
    ema_prev: float | None = None
    for value in values:
        if ema_prev is None:
            ema_prev = float(value)
        else:
            ema_prev = ((float(value) - ema_prev) * alpha) + ema_prev
        rows.append(float(ema_prev))
    return rows


def compute_atr(bars: list[dict[str, Any]], period: int = 14) -> list[float]:
    if not bars:
        return []
    true_ranges: list[float] = []
    prev_close = float(bars[0]["close"])
    for bar in bars:
        high = float(bar["high"])
        low = float(bar["low"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(float(tr))
        prev_close = float(bar["close"])
    atr_rows: list[float] = []
    window: list[float] = []
    for tr in true_ranges:
        window.append(float(tr))
        if len(window) > period:
            window.pop(0)
        atr_rows.append(sum(window) / float(len(window)))
    return atr_rows


def spread_price(symbol_info: Any) -> float:
    point = safe_float(getattr(symbol_info, "point", 0.0)) or 0.0
    spread_points = safe_float(getattr(symbol_info, "spread", 0.0)) or 0.0
    return float(point * spread_points)


def scaled_unit_pnl_usd(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    spread_px: float,
    volume: float,
) -> float:
    # penetration_lattice_lab_v2.unit_pnl_usd is normalized to a 0.01-lot baseline.
    baseline_volume = 0.01
    baseline_pnl = unit_pnl_usd(symbol, direction, entry_price, exit_price, spread_px)
    if baseline_volume <= 0.0:
        return 0.0
    return float(baseline_pnl) * (float(volume) / baseline_volume)


def entry_price_for_mode(*, direction: str, level: float, spread_px: float, trigger_mode: str) -> float:
    if trigger_mode == "executable":
        if direction == "BUY":
            return float(level)
        return float(level)
    return float(level)


def bar_reaches_entry(*, direction: str, level: float, bar: dict[str, Any], spread_px: float, trigger_mode: str) -> bool:
    high = float(bar["high"])
    low = float(bar["low"])
    if trigger_mode == "executable":
        if direction == "SELL":
            return high >= float(level)
        ask_low = low + float(spread_px)
        return ask_low <= float(level)
    if direction == "SELL":
        return high >= float(level)
    return low <= float(level)


def bar_reaches_ema_close(
    *,
    ticket: Ticket,
    ema_value: float,
    bar: dict[str, Any],
    spread_px: float,
    trigger_mode: str,
    positive_only: bool,
    symbol: str,
    volume: float,
) -> tuple[bool, float | None, float | None]:
    high = float(bar["high"])
    low = float(bar["low"])
    if ticket.direction == "BUY":
        if trigger_mode == "executable":
            touched = high >= float(ema_value)
            exit_price = float(ema_value)
        else:
            touched = high >= float(ema_value)
            exit_price = float(ema_value)
    else:
        if trigger_mode == "executable":
            ask_low = low + float(spread_px)
            touched = ask_low <= float(ema_value)
            exit_price = float(ema_value)
        else:
            touched = low <= float(ema_value)
            exit_price = float(ema_value)
    if not touched:
        return False, None, None
    pnl = scaled_unit_pnl_usd(
        symbol,
        ticket.direction,
        float(ticket.entry_price),
        float(exit_price),
        0.0,
        volume,
    )
    if positive_only and pnl < 0:
        return False, None, None
    return True, float(exit_price), float(pnl)


def build_contracts(args: argparse.Namespace) -> list[StudyContract]:
    contracts: list[StudyContract] = []
    symbols = [str(symbol).upper() for symbol in args.symbols]
    for symbol in symbols:
        timeframe = str(args.timeframe_by_symbol.get(symbol) or args.timeframe).upper()
        for trigger_mode in args.trigger_modes:
            for anchor_mode in args.anchor_modes:
                for close_mode in args.close_modes:
                    for ema_period in args.ema_periods:
                        for deviation_atr in args.deviation_atr:
                            contracts.append(
                                StudyContract(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    trigger_mode=str(trigger_mode),
                                    anchor_mode=str(anchor_mode),
                                    close_mode=str(close_mode),
                                    ema_period=int(ema_period),
                                    deviation_atr=float(deviation_atr),
                                    volume=float(args.volume),
                                )
                            )
    return contracts


def simulate_contract(contract: StudyContract, bars: list[dict[str, Any]], symbol_info: Any) -> dict[str, Any]:
    if len(bars) < max(40, contract.ema_period + 5):
        return {}
    closes = [float(bar["close"]) for bar in bars]
    ema_rows = compute_ema(closes, contract.ema_period)
    atr_rows = compute_atr(bars, 14)
    spread_px = spread_price(symbol_info)
    tickets: list[Ticket] = []
    stats: dict[str, Any] = {
        "realized_net_usd": 0.0,
        "realized_closes": 0,
        "wins": 0,
        "losses": 0,
        "max_open_total": 0,
        "final_open_count": 0,
        "min_floating_usd": 0.0,
        "max_floating_usd": 0.0,
        "closes": [],
        "seed_opens": 0,
        "follow_opens": 0,
    }

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ema_value = float(ema_rows[idx])
        atr_value = max(float(atr_rows[idx]), 1e-9)
        deviation_px = float(contract.deviation_atr) * atr_value
        upper_band = ema_value + deviation_px
        lower_band = ema_value - deviation_px

        remaining: list[Ticket] = []
        positive_only = contract.close_mode == "ema_touch_positive_only"
        for ticket in tickets:
            touched, exit_price, pnl = bar_reaches_ema_close(
                ticket=ticket,
                ema_value=ema_value,
                bar=bar,
                spread_px=spread_px,
                trigger_mode=contract.trigger_mode,
                positive_only=positive_only,
                symbol=contract.symbol,
                volume=contract.volume,
            )
            if touched and exit_price is not None and pnl is not None:
                stats["realized_net_usd"] += pnl
                stats["realized_closes"] += 1
                stats["wins"] += int(pnl > 0)
                stats["losses"] += int(pnl < 0)
                stats["closes"].append(float(pnl))
            else:
                remaining.append(ticket)
        tickets = remaining

        if contract.anchor_mode == "ema_shared" or not tickets:
            if bar_reaches_entry(
                direction="SELL",
                level=upper_band,
                bar=bar,
                spread_px=spread_px,
                trigger_mode=contract.trigger_mode,
            ):
                tickets.append(
                    Ticket(
                        direction="SELL",
                        entry_price=entry_price_for_mode(
                            direction="SELL",
                            level=upper_band,
                            spread_px=spread_px,
                            trigger_mode=contract.trigger_mode,
                        ),
                        anchor_price=float(upper_band),
                        opened_time=int(bar["time"]),
                    )
                )
                stats["seed_opens" if len(tickets) == 1 else "follow_opens"] += 1
            if bar_reaches_entry(
                direction="BUY",
                level=lower_band,
                bar=bar,
                spread_px=spread_px,
                trigger_mode=contract.trigger_mode,
            ):
                tickets.append(
                    Ticket(
                        direction="BUY",
                        entry_price=entry_price_for_mode(
                            direction="BUY",
                            level=lower_band,
                            spread_px=spread_px,
                            trigger_mode=contract.trigger_mode,
                        ),
                        anchor_price=float(lower_band),
                        opened_time=int(bar["time"]),
                    )
                )
                stats["seed_opens" if len(tickets) == 1 else "follow_opens"] += 1
        else:
            upper_anchor = max(float(ticket.anchor_price) for ticket in tickets)
            lower_anchor = min(float(ticket.anchor_price) for ticket in tickets)
            sell_level = upper_anchor + deviation_px
            buy_level = lower_anchor - deviation_px
            if bar_reaches_entry(
                direction="SELL",
                level=sell_level,
                bar=bar,
                spread_px=spread_px,
                trigger_mode=contract.trigger_mode,
            ):
                tickets.append(
                    Ticket(
                        direction="SELL",
                        entry_price=entry_price_for_mode(
                            direction="SELL",
                            level=sell_level,
                            spread_px=spread_px,
                            trigger_mode=contract.trigger_mode,
                        ),
                        anchor_price=float(sell_level),
                        opened_time=int(bar["time"]),
                    )
                )
                stats["follow_opens"] += 1
            if bar_reaches_entry(
                direction="BUY",
                level=buy_level,
                bar=bar,
                spread_px=spread_px,
                trigger_mode=contract.trigger_mode,
            ):
                tickets.append(
                    Ticket(
                        direction="BUY",
                        entry_price=entry_price_for_mode(
                            direction="BUY",
                            level=buy_level,
                            spread_px=spread_px,
                            trigger_mode=contract.trigger_mode,
                        ),
                        anchor_price=float(buy_level),
                        opened_time=int(bar["time"]),
                    )
                )
                stats["follow_opens"] += 1

        mark_mid = float(bar["close"])
        floating = sum(
            scaled_unit_pnl_usd(
                contract.symbol,
                ticket.direction,
                float(ticket.entry_price),
                float(mark_mid),
                0.0,
                contract.volume,
            )
            for ticket in tickets
        )
        stats["max_open_total"] = max(int(stats["max_open_total"]), len(tickets))
        stats["min_floating_usd"] = min(float(stats["min_floating_usd"]), float(floating))
        stats["max_floating_usd"] = max(float(stats["max_floating_usd"]), float(floating))

    stats["final_open_count"] = len(tickets)
    bars_per_day = {
        "M1": 1440,
        "M5": 288,
        "M15": 96,
        "H1": 24,
    }[contract.timeframe]
    days = max(1.0, float(len(bars)) / float(bars_per_day))
    realized_usd_per_hour = float(stats["realized_net_usd"]) / float(days * 24.0)
    avg_close_usd = (
        float(stats["realized_net_usd"]) / float(stats["realized_closes"])
        if int(stats["realized_closes"]) > 0
        else 0.0
    )
    return {
        "symbol": contract.symbol,
        "timeframe": contract.timeframe,
        "label": contract.label,
        "trigger_mode": contract.trigger_mode,
        "anchor_mode": contract.anchor_mode,
        "close_mode": contract.close_mode,
        "ema_period": contract.ema_period,
        "deviation_atr": contract.deviation_atr,
        "spread_px": round(float(spread_px), 6),
        "realized_net_usd": round(float(stats["realized_net_usd"]), 2),
        "realized_closes": int(stats["realized_closes"]),
        "wins": int(stats["wins"]),
        "losses": int(stats["losses"]),
        "seed_opens": int(stats["seed_opens"]),
        "follow_opens": int(stats["follow_opens"]),
        "max_open_total": int(stats["max_open_total"]),
        "final_open_count": int(stats["final_open_count"]),
        "min_floating_usd": round(float(stats["min_floating_usd"]), 2),
        "max_floating_usd": round(float(stats["max_floating_usd"]), 2),
        "avg_close_usd": round(float(avg_close_usd), 3),
        "realized_usd_per_hour": round(float(realized_usd_per_hour), 3),
    }


def sort_key(row: dict[str, Any]) -> tuple[float, float, float, int]:
    return (
        float(row.get("realized_usd_per_hour") or 0.0),
        float(row.get("realized_net_usd") or 0.0),
        float(row.get("avg_close_usd") or 0.0),
        -int(row.get("max_open_total") or 0),
    )


def build_markdown(rows: list[dict[str, Any]], *, symbols: list[str], days: int) -> str:
    lines = [
        "# EMA Deviation Anchor Study",
        "",
        f"- Generated: `{utc_now_iso()}`",
        f"- Symbols: `{', '.join(symbols)}`",
        f"- Days: `{days}`",
        "- Objective: compare executable-side EMA-deviation entries against midpoint-style triggers, shared EMA anchor vs self-anchored extremes, and EMA-touch exit laws.",
        "- Backtest fidelity note: this is a bar-based study using executable-side threshold approximations (`SELL` via high/bid touch, `BUY` via low+spread/ask touch). It is useful for ranking ideas, not as a promotion-grade live proof artifact.",
        "",
    ]
    for symbol in symbols:
        symbol_rows = [row for row in rows if str(row.get("symbol")) == symbol]
        if not symbol_rows:
            continue
        symbol_rows.sort(key=sort_key, reverse=True)
        best = symbol_rows[0]
        lines.extend(
            [
                f"## {symbol}",
                "",
                f"- Best row: `{best['label']}`",
                f"- Realized USD/hour: `${best['realized_usd_per_hour']}`",
                f"- Realized net USD: `${best['realized_net_usd']}`",
                f"- Closes: `{best['realized_closes']}`",
                f"- Avg close USD: `${best['avg_close_usd']}`",
                f"- Max/final open: `{best['max_open_total']}` / `{best['final_open_count']}`",
                f"- Min floating USD: `${best['min_floating_usd']}`",
                "",
                "| Label | Trigger | Anchor | Close | EMA | Dev ATR | $/hr | Net USD | Closes | Avg close | Max/final open | Min float |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in symbol_rows[:8]:
            lines.append(
                f"| `{row['label']}` | `{row['trigger_mode']}` | `{row['anchor_mode']}` | `{row['close_mode']}` | "
                f"`{row['ema_period']}` | `{row['deviation_atr']}` | `${row['realized_usd_per_hour']}` | `${row['realized_net_usd']}` | "
                f"`{row['realized_closes']}` | `${row['avg_close_usd']}` | `{row['max_open_total']}/{row['final_open_count']}` | `${row['min_floating_usd']}` |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest executable-side EMA deviation and anchor variants.")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "LTCUSD"])
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--ema-periods", nargs="+", type=int, default=[8, 12, 20, 34, 50])
    parser.add_argument("--deviation-atr", nargs="+", type=float, default=[0.5, 1.0, 1.5])
    parser.add_argument("--trigger-modes", nargs="+", default=["executable", "midpoint"])
    parser.add_argument("--anchor-modes", nargs="+", default=["ema_shared", "self_anchor_extremes"])
    parser.add_argument("--close-modes", nargs="+", default=["ema_touch", "ema_touch_positive_only"])
    parser.add_argument("--volume", type=float, default=0.01)
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    args = parser.parse_args()
    args.timeframe_by_symbol = dict(DEFAULT_TIMEFRAME_BY_SYMBOL)
    return args


def main() -> None:
    args = parse_args()
    if not mt5.initialize():
        raise SystemExit("MetaTrader5 initialize() failed")
    try:
        contracts = build_contracts(args)
        rows: list[dict[str, Any]] = []
        for contract in contracts:
            info = mt5.symbol_info(contract.symbol)
            if info is None:
                continue
            bars = load_bars(contract.symbol, contract.timeframe, args.days)
            row = simulate_contract(contract, bars, info)
            if row:
                rows.append(row)
        rows.sort(key=sort_key, reverse=True)

        output_csv = Path(args.output_csv)
        output_json = Path(args.output_json)
        output_md = Path(args.output_md)
        output_csv.parent.mkdir(parents=True, exist_ok=True)

        if rows:
            with output_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

        payload = {
            "generated_at": utc_now_iso(),
            "days": int(args.days),
            "symbols": [str(symbol).upper() for symbol in args.symbols],
            "rows": rows,
        }
        output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        output_md.write_text(
            build_markdown(rows, symbols=[str(symbol).upper() for symbol in args.symbols], days=int(args.days)),
            encoding="utf-8",
        )
        print(json.dumps({"output_csv": str(output_csv), "output_json": str(output_json), "output_md": str(output_md), "row_count": len(rows)}, indent=2))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
