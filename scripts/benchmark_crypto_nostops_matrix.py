#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import REARM_VARIANTS as ENGINE_REARM_VARIANTS, StatefulRearmRawEngine, Ticket
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "universal_10symbol_rearm.json"
ROWS_OUT = ROOT / "reports" / "crypto_nostops_matrix_rows.csv"
BASKET_OUT = ROOT / "reports" / "crypto_nostops_matrix_basket.csv"
SUMMARY_OUT = ROOT / "reports" / "crypto_nostops_matrix_summary.md"


SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
ALPHAS = (0.5, 0.75, 1.0)
GAPS = (1, 2)
REARM_VARIANT_NAMES = ("rearm_lvl2_exc1", "rearm_lvl2_exc2", "rearm_lvl3_exc1")
MAX_OPEN_CHOICES = (20, 30, 40)
STEP_MULTIPLIERS = (0.75, 1.0, 1.5)
MOMENTUM_GATES = (True, False)
DAYS = 90


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_closed_h1_bars(symbol: str, days: int) -> list[dict]:
    bars_needed = 24 * days
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, bars_needed)
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


def basket_key(row: dict) -> tuple:
    return (
        row["alpha"],
        row["gap"],
        row["rearm_variant"],
        row["max_open_per_side"],
        row["step_multiplier"],
        row["momentum_gate"],
    )


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
        basket_totals: dict[tuple, dict] = defaultdict(lambda: {
            "combined_net_usd": 0.0,
            "realized_net_usd": 0.0,
            "floating_net_usd": 0.0,
            "realized_closes": 0,
            "max_open_total": 0,
            "rearm_opens": 0,
            "symbols": [],
        })

        for symbol in SYMBOLS:
            info = info_by_symbol[symbol]
            bars = bars_by_symbol[symbol]
            base_step = float(symbol_cfgs[symbol]["step"])
            base_max_open = int(symbol_cfgs[symbol]["max_open_per_side"])

            for alpha in ALPHAS:
                for gap in GAPS:
                    for variant_name in REARM_VARIANT_NAMES:
                        variant = ENGINE_REARM_VARIANTS[variant_name]
                        for max_open in MAX_OPEN_CHOICES:
                            for step_mult in STEP_MULTIPLIERS:
                                for momentum_gate in MOMENTUM_GATES:
                                    step = base_step * step_mult
                                    # Cap search around the verified baseline rather than exploding upward forever.
                                    effective_max_open = max_open if max_open >= base_max_open else base_max_open
                                    close_mode = "one_level" if gap == 1 else "two_level"
                                    cfg = RawConfig(
                                        step_pips=step,
                                        max_open_per_side=effective_max_open,
                                        close_mode=close_mode,
                                        step_is_price_units=True,
                                    )
                                    engine = StatefulRearmRawEngine(
                                        symbol,
                                        cfg,
                                        info,
                                        variant=variant,
                                        close_alpha=alpha,
                                        cooldown_bars=0,
                                        momentum_gate=momentum_gate,
                                        sell_gap=gap,
                                        buy_gap=gap,
                                    )
                                    engine.replay(bars)

                                    final_close = float(bars[-1]["close"])
                                    spread_px = spread_price(info)
                                    floating_net = 0.0
                                    tickets = [Ticket(**t) for t in engine.state.open_tickets]
                                    for ticket in tickets:
                                        floating_net += unit_pnl_usd(
                                            symbol,
                                            ticket.direction,
                                            ticket.entry_price,
                                            final_close,
                                            spread_px,
                                        )

                                    combined = float(engine.state.realized_net_usd) + float(floating_net)
                                    avg_close = (
                                        float(engine.state.realized_net_usd) / int(engine.state.realized_closes)
                                        if int(engine.state.realized_closes) > 0
                                        else 0.0
                                    )

                                    row = {
                                        "symbol": symbol,
                                        "days": DAYS,
                                        "alpha": alpha,
                                        "gap": gap,
                                        "rearm_variant": variant_name,
                                        "max_open_per_side": effective_max_open,
                                        "step": round(step, 6),
                                        "step_multiplier": step_mult,
                                        "momentum_gate": momentum_gate,
                                        "realized_net_usd": round(float(engine.state.realized_net_usd), 2),
                                        "floating_net_usd": round(float(floating_net), 2),
                                        "combined_net_usd": round(float(combined), 2),
                                        "realized_closes": int(engine.state.realized_closes),
                                        "open_tickets_left": len(tickets),
                                        "max_open_total": int(engine.state.max_open_total),
                                        "rearm_opens": int(engine.state.rearm_opens),
                                        "avg_close_usd": round(avg_close, 4),
                                    }
                                    rows.append(row)

                                    key = basket_key(row)
                                    agg = basket_totals[key]
                                    agg["combined_net_usd"] += combined
                                    agg["realized_net_usd"] += float(engine.state.realized_net_usd)
                                    agg["floating_net_usd"] += floating_net
                                    agg["realized_closes"] += int(engine.state.realized_closes)
                                    agg["max_open_total"] += int(engine.state.max_open_total)
                                    agg["rearm_opens"] += int(engine.state.rearm_opens)
                                    agg["symbols"].append(symbol)

        rows.sort(key=lambda r: (r["symbol"], -r["combined_net_usd"], -r["realized_closes"]))
        ROWS_OUT.parent.mkdir(parents=True, exist_ok=True)
        with ROWS_OUT.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        basket_rows = []
        for key, agg in basket_totals.items():
            alpha, gap, variant_name, max_open, step_mult, momentum_gate = key
            basket_rows.append({
                "alpha": alpha,
                "gap": gap,
                "rearm_variant": variant_name,
                "max_open_per_side": max_open,
                "step_multiplier": step_mult,
                "momentum_gate": momentum_gate,
                "symbols": ",".join(sorted(agg["symbols"])),
                "combined_net_usd": round(agg["combined_net_usd"], 2),
                "realized_net_usd": round(agg["realized_net_usd"], 2),
                "floating_net_usd": round(agg["floating_net_usd"], 2),
                "realized_closes": agg["realized_closes"],
                "max_open_total_sum": agg["max_open_total"],
                "rearm_opens_sum": agg["rearm_opens"],
                "avg_close_usd": round(agg["realized_net_usd"] / agg["realized_closes"], 4) if agg["realized_closes"] else 0.0,
            })
        basket_rows.sort(key=lambda r: (-r["combined_net_usd"], -r["realized_closes"]))

        with BASKET_OUT.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(basket_rows[0].keys()))
            writer.writeheader()
            writer.writerows(basket_rows)

        best_by_symbol = {}
        for symbol in SYMBOLS:
            symbol_rows = [row for row in rows if row["symbol"] == symbol]
            best_by_symbol[symbol] = symbol_rows[0]

        top_basket = basket_rows[:10]
        lines = [
            "# Crypto No-Stops Matrix",
            "",
            "Validated raw stateful rearm H1 sweep over BTCUSD/ETHUSD/SOLUSD/XRPUSD.",
            "",
            "## Top Basket Configs",
            "",
            "| Rank | Combined | Realized | Floating | Closes | Avg/Close | Alpha | Gap | Variant | MaxOpen | StepMult | Momentum |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
        ]
        for idx, row in enumerate(top_basket, start=1):
            lines.append(
                f"| {idx} | {row['combined_net_usd']:.2f} | {row['realized_net_usd']:.2f} | {row['floating_net_usd']:.2f} | "
                f"{row['realized_closes']} | {row['avg_close_usd']:.4f} | {row['alpha']} | {row['gap']} | "
                f"{row['rearm_variant']} | {row['max_open_per_side']} | {row['step_multiplier']} | {row['momentum_gate']} |"
            )

        lines.extend([
            "",
            "## Best Per Symbol",
            "",
            "| Symbol | Combined | Realized | Floating | Closes | Avg/Close | Alpha | Gap | Variant | MaxOpen | Step | StepMult | Momentum |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
        ])
        for symbol in SYMBOLS:
            row = best_by_symbol[symbol]
            lines.append(
                f"| {symbol} | {row['combined_net_usd']:.2f} | {row['realized_net_usd']:.2f} | {row['floating_net_usd']:.2f} | "
                f"{row['realized_closes']} | {row['avg_close_usd']:.4f} | {row['alpha']} | {row['gap']} | "
                f"{row['rearm_variant']} | {row['max_open_per_side']} | {row['step']:.6f} | {row['step_multiplier']} | {row['momentum_gate']} |"
            )

        SUMMARY_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Saved {ROWS_OUT}")
        print(f"Saved {BASKET_OUT}")
        print(f"Saved {SUMMARY_OUT}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
