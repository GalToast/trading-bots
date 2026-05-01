#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import REARM_VARIANTS, StatefulRearmRawEngine, Ticket
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "universal_10symbol_rearm.json"
ROWS_OUT = ROOT / "reports" / "crypto_btc_eth_focus_rows.csv"
SUMMARY_OUT = ROOT / "reports" / "crypto_btc_eth_focus_summary.md"

SYMBOLS = ("BTCUSD", "ETHUSD")
ALPHAS = (0.9, 1.0)
REARM_VARIANT_NAMES = ("rearm_lvl2_exc1", "rearm_lvl2_exc2", "rearm_lvl3_exc1")
MAX_OPEN_CHOICES = (30, 40, 50, 60)
STEP_MULTIPLIERS = (0.9, 1.0, 1.1, 1.25, 1.5)
DAYS = 90


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_closed_h1_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 24 * days)
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


def main() -> int:
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        config = load_config()
        symbol_cfgs = config["symbols"]
        bars_by_symbol: dict[str, list[dict]] = {}
        info_by_symbol = {}

        for symbol in SYMBOLS:
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f"Missing symbol info for {symbol}")
                return 1
            bars = load_closed_h1_bars(symbol, DAYS)
            if not bars:
                print(f"No bars for {symbol}")
                return 1
            info_by_symbol[symbol] = info
            bars_by_symbol[symbol] = bars

        rows: list[dict] = []
        for symbol in SYMBOLS:
            info = info_by_symbol[symbol]
            bars = bars_by_symbol[symbol]
            base_step = float(symbol_cfgs[symbol]["step"])
            for alpha in ALPHAS:
                for variant_name in REARM_VARIANT_NAMES:
                    variant = REARM_VARIANTS[variant_name]
                    for max_open in MAX_OPEN_CHOICES:
                        for step_mult in STEP_MULTIPLIERS:
                            step = base_step * step_mult
                            engine = StatefulRearmRawEngine(
                                symbol,
                                RawConfig(
                                    step_pips=step,
                                    max_open_per_side=max_open,
                                    close_mode="one_level",
                                    step_is_price_units=True,
                                ),
                                info,
                                variant=variant,
                                close_alpha=alpha,
                                cooldown_bars=0,
                                momentum_gate=True,
                                sell_gap=1,
                                buy_gap=1,
                            )
                            engine.replay(bars)
                            final_close = float(bars[-1]["close"])
                            spread_px = spread_price(info)
                            tickets = [Ticket(**t) for t in engine.state.open_tickets]
                            floating = 0.0
                            for ticket in tickets:
                                floating += unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, final_close, spread_px)
                            realized = float(engine.state.realized_net_usd)
                            combined = realized + floating
                            rows.append(
                                {
                                    "symbol": symbol,
                                    "days": DAYS,
                                    "alpha": alpha,
                                    "rearm_variant": variant_name,
                                    "max_open_per_side": max_open,
                                    "step": round(step, 6),
                                    "step_multiplier": step_mult,
                                    "realized_net_usd": round(realized, 2),
                                    "floating_net_usd": round(floating, 2),
                                    "combined_net_usd": round(combined, 2),
                                    "realized_closes": int(engine.state.realized_closes),
                                    "open_tickets_left": len(tickets),
                                    "max_open_total": int(engine.state.max_open_total),
                                    "rearm_opens": int(engine.state.rearm_opens),
                                    "avg_close_usd": round(realized / engine.state.realized_closes, 4)
                                    if int(engine.state.realized_closes) > 0
                                    else 0.0,
                                }
                            )

        rows.sort(key=lambda row: (row["symbol"], -row["combined_net_usd"], -row["realized_closes"]))
        ROWS_OUT.parent.mkdir(parents=True, exist_ok=True)
        with ROWS_OUT.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        lines = [
            "# BTC / ETH No-Stops Focus",
            "",
            "Focused H1 search around the current no-stops ridge.",
            "",
            "- `gap = 1`",
            "- `momentum_gate = true`",
            "- `close_mode = one_level`",
            "",
            "## Best Per Symbol",
            "",
            "| Symbol | Combined | Realized | Floating | Closes | Avg/Close | Alpha | Variant | MaxOpen | Step | StepMult |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |",
        ]
        for symbol in SYMBOLS:
            best = next(row for row in rows if row["symbol"] == symbol)
            lines.append(
                f"| {symbol} | {best['combined_net_usd']:.2f} | {best['realized_net_usd']:.2f} | {best['floating_net_usd']:.2f} | "
                f"{best['realized_closes']} | {best['avg_close_usd']:.4f} | {best['alpha']} | {best['rearm_variant']} | "
                f"{best['max_open_per_side']} | {best['step']:.6f} | {best['step_multiplier']} |"
            )

        SUMMARY_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Saved {ROWS_OUT}")
        print(f"Saved {SUMMARY_OUT}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
