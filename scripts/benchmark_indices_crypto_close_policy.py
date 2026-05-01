#!/usr/bin/env python3
"""
Benchmark close policy ladder for indices and crypto:
NAS100, US30, BTCUSD, ETHUSD.

Reuses the same simulation engine as benchmark_fx_fixed_step_close_policy.py,
but with symbol-specific step sizes and max_open values tuned for indices/crypto.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "indices_crypto_close_policy_ladder.csv"


@dataclass(frozen=True)
class ClosePolicy:
    name: str
    close_gap: int
    close_alpha: float
    close_style: str  # outer | inner | all_profitable


# Subset of policies matching the FX ladder; focused on the user-requested four.
POLICIES = [
    ClosePolicy(name="outer_gap2_alpha0", close_gap=2, close_alpha=0.0, close_style="outer"),       # baseline
    ClosePolicy(name="outer_gap2_alpha50", close_gap=2, close_alpha=0.5, close_style="outer"),
    ClosePolicy(name="outer_gap1_alpha100", close_gap=1, close_alpha=1.0, close_style="outer"),
    ClosePolicy(name="allprof_gap1_alpha50", close_gap=1, close_alpha=0.5, close_style="all_profitable"),
]


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    step_pips: float       # price step in pip units (or price units for crypto)
    max_open_per_side: int


SYMBOL_CONFIGS: dict[str, SymbolConfig] = {
    "NAS100": SymbolConfig(symbol="NAS100", step_pips=32, max_open_per_side=10),
    "US30":   SymbolConfig(symbol="US30",   step_pips=49, max_open_per_side=10),
    "BTCUSD": SymbolConfig(symbol="BTCUSD", step_pips=289, max_open_per_side=6),
    "ETHUSD": SymbolConfig(symbol="ETHUSD", step_pips=11, max_open_per_side=6),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark close policy ladder for indices and crypto (NAS100, US30, BTCUSD, ETHUSD)."
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=["NAS100", "US30", "BTCUSD", "ETHUSD"],
    )
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    return parser.parse_args()


def _interp_close(level_price: float, bar_extreme: float, direction: str, alpha: float) -> float:
    if direction == "SELL":
        return level_price + alpha * (bar_extreme - level_price)
    return level_price + alpha * (bar_extreme - level_price)


def select_close_positions(side_len: int, gap: int, style: str, profitable_positions: list[int] | None) -> list[int]:
    if side_len <= gap:
        return []
    if style == "outer":
        return [0]
    if style == "inner":
        return [max(0, gap - 1)]
    if style == "all_profitable":
        return list(profitable_positions or [])
    raise ValueError(f"Unsupported close style: {style}")


def simulate_close_policy(symbol: str, bars: list[dict], symbol_info, step_pips: float, max_open_per_side: int, policy: ClosePolicy) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    alpha_closes = 0
    close_events = 0
    tickets_closed = 0

    adapt_cfg = type(
        "Cfg",
        (),
        {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        },
    )()

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")
        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        # --- Close: sell side ---
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > policy.close_gap and bar["low"] <= sells[policy.close_gap].entry_price:
            level_price = sells[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["low"], "SELL", policy.close_alpha)
            profitable_positions = [
                pos
                for pos, ticket in enumerate(sells)
                if unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = select_close_positions(len(sells), policy.close_gap, policy.close_style, profitable_positions)
            if not close_positions:
                break
            close_indices = sorted(set(close_positions), reverse=True)
            closed_any = False
            for pos in close_indices:
                ticket = sells[pos]
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
                tickets_closed += 1
                closed_any = True
            if not closed_any:
                break
            close_events += 1
            if policy.close_alpha > 0:
                alpha_closes += len(close_indices)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # --- Close: buy side ---
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > policy.close_gap and bar["high"] >= buys[policy.close_gap].entry_price:
            level_price = buys[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["high"], "BUY", policy.close_alpha)
            profitable_positions = [
                pos
                for pos, ticket in enumerate(buys)
                if unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = select_close_positions(len(buys), policy.close_gap, policy.close_style, profitable_positions)
            if not close_positions:
                break
            close_indices = sorted(set(close_positions), reverse=True)
            closed_any = False
            for pos in close_indices:
                ticket = buys[pos]
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
                tickets_closed += 1
                closed_any = True
            if not closed_any:
                break
            close_events += 1
            if policy.close_alpha > 0:
                alpha_closes += len(close_indices)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    return {
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized_pnls),
        "close_events": close_events,
        "tickets_closed": tickets_closed,
        "alpha_closes": alpha_closes,
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbols = [s for s in args.symbols if s in SYMBOL_CONFIGS]
        if not symbols:
            print(f"No recognized symbols. Available: {list(SYMBOL_CONFIGS.keys())}")
            return 1

        rows: list[dict[str, object]] = []
        summary_rows: list[dict[str, object]] = []
        baseline_total = 0.0
        policy_totals: dict[str, float] = {policy.name: 0.0 for policy in POLICIES}
        policy_symbol_totals: dict[str, dict[str, float]] = {policy.name: {} for policy in POLICIES}

        baseline_policy = next(p for p in POLICIES if p.name == "outer_gap2_alpha0")

        for symbol in symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f"Symbol info not available for {symbol}, skipping.")
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                print(f"No bars loaded for {symbol}, skipping.")
                continue

            sym_cfg = SYMBOL_CONFIGS[symbol]
            print(f"Simulating {symbol}: step={sym_cfg.step_pips}, max_open={sym_cfg.max_open_per_side}, bars={len(bars)}")

            baseline = simulate_close_policy(
                symbol, bars, info, sym_cfg.step_pips, sym_cfg.max_open_per_side, baseline_policy
            )
            baseline_total += float(baseline["combined_net_usd"])
            print(f"  baseline ({baseline_policy.name}): combined_net = ${float(baseline['combined_net_usd']):.3f}")

            for policy in POLICIES:
                result = simulate_close_policy(
                    symbol, bars, info, sym_cfg.step_pips, sym_cfg.max_open_per_side, policy
                )
                policy_totals[policy.name] += float(result["combined_net_usd"])
                policy_symbol_totals[policy.name][symbol] = float(result["combined_net_usd"])

                delta = round(float(result["combined_net_usd"]) - float(baseline["combined_net_usd"]), 3)
                print(f"  {policy.name}: combined_net = ${float(result['combined_net_usd']):.3f} (delta ${delta:+.3f})")

                rows.append(
                    {
                        "symbol": symbol,
                        "policy": policy.name,
                        "days": args.days,
                        "step_pips": sym_cfg.step_pips,
                        "max_open_per_side": sym_cfg.max_open_per_side,
                        "close_gap": policy.close_gap,
                        "close_alpha": policy.close_alpha,
                        "close_style": policy.close_style,
                        "baseline_combined_usd": baseline["combined_net_usd"],
                        "baseline_closes": baseline["realized_closes"],
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "close_events": result["close_events"],
                        "tickets_closed": result["tickets_closed"],
                        "variant_alpha_closes": result["alpha_closes"],
                        "variant_max_open": result["max_open_total"],
                        "delta_combined_usd": delta,
                    }
                )

        # Summary
        for policy in POLICIES:
            summary_rows.append(
                {
                    "policy": policy.name,
                    "close_gap": policy.close_gap,
                    "close_alpha": policy.close_alpha,
                    "close_style": policy.close_style,
                    "baseline_total_usd": round(baseline_total, 3),
                    "variant_total_usd": round(policy_totals[policy.name], 3),
                    "delta_total_usd": round(policy_totals[policy.name] - baseline_total, 3),
                    **{
                        s: round(policy_symbol_totals[policy.name].get(s, 0.0), 3)
                        for s in symbols
                    },
                }
            )

        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)

        detail_fieldnames = [
            "symbol", "policy", "days", "step_pips", "max_open_per_side",
            "close_gap", "close_alpha", "close_style",
            "baseline_combined_usd", "baseline_closes",
            "variant_combined_usd", "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "close_events", "tickets_closed",
            "variant_alpha_closes", "variant_max_open", "delta_combined_usd",
        ]
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=detail_fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        summary_fieldnames = ["policy", "close_gap", "close_alpha", "close_style",
                              "baseline_total_usd", "variant_total_usd", "delta_total_usd"] + symbols
        summary_path = out_csv.with_name("indices_crypto_close_policy_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=summary_fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)

        # Print summary table
        print()
        print("=" * 100)
        print("INDICES & CRYPTO CLOSE POLICY LADDER — SUMMARY")
        print("=" * 100)
        print(f"{'Policy':<25} {'Basket Total':>12} {'Delta':>12}  " + "  ".join(f"{s:>12}" for s in symbols))
        print("-" * 100)
        for sr in summary_rows:
            vals = "  ".join(f"${float(sr[s]):>10.3f}" for s in symbols)
            print(
                f"{sr['policy']:<25}  ${float(sr['variant_total_usd']):>10.3f}  "
                f"${float(sr['delta_total_usd']):>+10.3f}  {vals}"
            )
        print("=" * 100)

        best = max(summary_rows, key=lambda r: float(r["variant_total_usd"]))
        print(f"\nBest basket policy: {best['policy']} at ${float(best['variant_total_usd']):.3f} "
              f"(delta ${float(best['delta_total_usd']):+.3f} vs baseline)")

        print(f"\nWrote {out_csv}")
        print(f"Wrote {summary_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
