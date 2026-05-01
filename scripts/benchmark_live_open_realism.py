#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import REARM_VARIANTS, StatefulRearmRawEngine
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import pip_size_for


TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}

TIMEFRAME_BARS_PER_DAY = {
    "M1": 1440,
    "M5": 288,
    "M15": 96,
    "H1": 24,
    "H4": 6,
}


def load_bars(symbol: str, timeframe_name: str, days: int) -> list[dict]:
    timeframe = TIMEFRAME_MAP[timeframe_name]
    count = max(10, TIMEFRAME_BARS_PER_DAY[timeframe_name] * max(1, int(days)))
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None:
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


def summarize_events(event_path: Path) -> dict[str, int]:
    if not event_path.exists():
        return {
            "open_events": 0,
            "close_events": 0,
            "same_bar_roundtrips": 0,
        }
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    opens = Counter(
        (
            int(evt.get("bar_time", 0) or 0),
            str(evt.get("direction", "")),
            round(float(evt.get("entry_price", 0.0) or 0.0), 6),
        )
        for evt in events
        if evt.get("action") == "open_ticket"
    )
    closes = Counter(
        (
            int(evt.get("bar_time", 0) or 0),
            str(evt.get("direction", "")),
            round(float(evt.get("entry_price", 0.0) or 0.0), 6),
        )
        for evt in events
        if evt.get("action") == "close_ticket"
    )
    same_bar_roundtrips = sum(min(count, closes.get(key, 0)) for key, count in opens.items())
    return {
        "open_events": sum(opens.values()),
        "close_events": sum(closes.values()),
        "same_bar_roundtrips": same_bar_roundtrips,
    }


def replay_config(
    *,
    symbol: str,
    timeframe: str,
    step: float,
    max_open_per_side: int,
    close_alpha: float,
    variant_name: str,
    cooldown_bars: int,
    momentum_gate: bool,
    sell_gap: int,
    buy_gap: int,
    close_realism_mode: str,
    open_realism_mode: str,
    days: int,
) -> dict:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Missing symbol info for {symbol}")
    pip_size = float(pip_size_for(info) or 0.0)
    if pip_size <= 0.0:
        raise RuntimeError(f"Invalid pip size for {symbol}")
    variant = REARM_VARIANTS.get(variant_name)
    if variant is None:
        raise RuntimeError(f"Unknown rearm variant: {variant_name}")
    cfg = RawConfig(step_pips=float(step) / pip_size, max_open_per_side=int(max_open_per_side), close_mode="two_level")
    bars = load_bars(symbol, timeframe, days)
    if not bars:
        raise RuntimeError(f"No bars loaded for {symbol} {timeframe}")
    engine = StatefulRearmRawEngine(
        symbol,
        cfg,
        info,
        variant=variant,
        close_alpha=float(close_alpha),
        cooldown_bars=int(cooldown_bars),
        momentum_gate=bool(momentum_gate),
        sell_gap=int(sell_gap),
        buy_gap=int(buy_gap),
        close_realism_mode=close_realism_mode,
        open_realism_mode=open_realism_mode,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        event_path = Path(tmpdir) / "events.jsonl"
        for bar in bars:
            engine.process_bar(bar, event_path=event_path, emit=True)
        event_summary = summarize_events(event_path)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "days": int(days),
        "open_realism_mode": open_realism_mode,
        "close_realism_mode": close_realism_mode,
        "realized_net_usd": round(float(engine.state.realized_net_usd or 0.0), 3),
        "realized_closes": int(engine.state.realized_closes or 0),
        "rearm_opens": int(engine.state.rearm_opens or 0),
        "final_open_count": len(engine.state.open_tickets or []),
        "max_open_total": int(engine.state.max_open_total or 0),
        "next_buy_level": round(float(engine.state.next_buy_level or 0.0), 6),
        "next_sell_level": round(float(engine.state.next_sell_level or 0.0), 6),
        **event_summary,
    }


def print_table(rows: list[dict]) -> None:
    headers = [
        "open_mode",
        "close_mode",
        "realized",
        "closes",
        "rearm",
        "final_open",
        "max_open",
        "open_ev",
        "close_ev",
        "same_bar_rt",
    ]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join([" --- " for _ in headers]) + "|")
    for row in rows:
        print(
            "| "
            + " | ".join(
                [
                    str(row["open_realism_mode"]),
                    str(row["close_realism_mode"]),
                    f'{row["realized_net_usd"]:.3f}',
                    str(row["realized_closes"]),
                    str(row["rearm_opens"]),
                    str(row["final_open_count"]),
                    str(row["max_open_total"]),
                    str(row["open_events"]),
                    str(row["close_events"]),
                    str(row["same_bar_roundtrips"]),
                ]
            )
            + " |"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay raw stateful rearm configs under different live open-trigger realism modes.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP.keys()), required=True)
    parser.add_argument("--step", type=float, required=True)
    parser.add_argument("--max-open-per-side", type=int, default=50)
    parser.add_argument("--raw-close-alpha", type=float, default=1.0)
    parser.add_argument("--raw-rearm-variant", default="rearm_lvl2_exc2")
    parser.add_argument("--raw-rearm-cooldown-bars", type=int, default=0)
    parser.add_argument("--raw-rearm-momentum-gate", action="store_true")
    parser.add_argument("--raw-sell-gap", type=int, default=1)
    parser.add_argument("--raw-buy-gap", type=int, default=1)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--close-realism-mode", choices=["intrabar", "bar_close"], default="bar_close")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        rows = []
        for open_mode in ("intrabar", "broker_touch"):
            rows.append(
                replay_config(
                    symbol=args.symbol.upper(),
                    timeframe=args.timeframe,
                    step=float(args.step),
                    max_open_per_side=int(args.max_open_per_side),
                    close_alpha=float(args.raw_close_alpha),
                    variant_name=str(args.raw_rearm_variant),
                    cooldown_bars=int(args.raw_rearm_cooldown_bars),
                    momentum_gate=bool(args.raw_rearm_momentum_gate),
                    sell_gap=int(args.raw_sell_gap),
                    buy_gap=int(args.raw_buy_gap),
                    close_realism_mode=str(args.close_realism_mode),
                    open_realism_mode=open_mode,
                    days=int(args.days),
                )
            )
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(
                f"# Live Open Realism Replay\n\n"
                f"symbol={args.symbol.upper()} timeframe={args.timeframe} step={args.step} "
                f"close_mode={args.close_realism_mode} days={args.days}\n"
            )
            print_table(rows)
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
