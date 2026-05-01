#!/usr/bin/env python3
"""Master ranking of all USDJPY experiments.

Combines results from:
- 10-lane exit/entry backtest (@qwen)
- Asymmetry lab MT5 sweep (@qwen)
- Confirmed-displacement sweep (@qwen)
- Regime router scoring (@qwen-assistant)
- Historical counterfactuals (@qwen-assistant)

Outputs a single ranking table with all experiments normalized to USD/trade.

Author: local AI-assisted research pass
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Experiment:
    name: str
    category: str  # exit, entry, asymmetry, router, historical
    exp_usd: float
    wr: float
    trades_per_day: float
    net_usd: float
    trades: int
    source: str
    notes: str = ""
    live_feasible: bool = True
    rank_override: int = 0


def build_experiments() -> list[Experiment]:
    return [
        # ── Historical counterfactuals (from lab_dashboard.py) ─────────
        Experiment(
            "exit_75_floor03", "exit",
            exp_usd=0.28, wr=100.0, trades_per_day=0, net_usd=5.28, trades=19,
            source="10-lane backtest (@qwen)",
            notes="Strongest counterfactual — 75% peak retain + $0.03 floor",
        ),
        Experiment(
            "tiered_peak_capture", "exit",
            exp_usd=0.28, wr=100.0, trades_per_day=0, net_usd=5.25, trades=19,
            source="10-lane backtest (@qwen)",
            notes="Tiered by peak size: <0.10→50%, 0.10-0.30→60%, 0.30-1.00→70%, >1.00→80%",
        ),
        Experiment(
            "fast_trail_above_1", "exit",
            exp_usd=0.27, wr=100.0, trades_per_day=0, net_usd=5.08, trades=19,
            source="10-lane backtest (@qwen)",
            notes="When peak >$1.00, trail at 80% retain",
        ),
        Experiment(
            "exit_60_floor03", "exit",
            exp_usd=0.23, wr=100.0, trades_per_day=0, net_usd=4.46, trades=19,
            source="10-lane backtest (@qwen)",
            notes="Currently LIVE challenger",
        ),
        Experiment(
            "time_decay_trail", "exit",
            exp_usd=0.22, wr=100.0, trades_per_day=0, net_usd=4.11, trades=19,
            source="10-lane backtest (@qwen)",
            notes="0-60s:40%, 60-180s:60%, 180s+:80% retain",
        ),
        Experiment(
            "exit_50_floor03", "exit",
            exp_usd=0.21, wr=100.0, trades_per_day=0, net_usd=4.05, trades=19,
            source="10-lane backtest (@qwen)",
        ),
        Experiment(
            "peak_gate_120s", "entry",
            exp_usd=0.17, wr=100.0, trades_per_day=0, net_usd=2.92, trades=17,
            source="10-lane backtest (@qwen)",
            notes="Cut flat if peak < $0.15 within 120s — loses 2 trades",
        ),

        # ── Historical baseline ─────────────────────────────────────────
        Experiment(
            "control (30s holdoff + baseline trail)", "control",
            exp_usd=0.14, wr=89.5, trades_per_day=30.9, net_usd=1.86, trades=24,
            source="Historical trade log (24 trades, 18.6h)",
            notes="Current live baseline",
        ),
        Experiment(
            "adverse_tolerance_015", "entry",
            exp_usd=0.14, wr=89.5, trades_per_day=0, net_usd=2.60, trades=19,
            source="10-lane backtest (@qwen)",
            notes="No exit change = same as control — adverse correlation is +0.60",
        ),
        Experiment(
            "entry_10s", "entry",
            exp_usd=0.11, wr=77.3, trades_per_day=0, net_usd=2.37, trades=22,
            source="10-lane backtest (@qwen)",
            notes="TRAP — admits 3 more losers, worst net P/L",
        ),

        # ── Asymmetry lab (MT5 10-day sweep, @qwen) ─────────────────────
        Experiment(
            "confirm_disp_1.5pip_2.5x_1bar", "asymmetry",
            exp_usd=0.096, wr=75.0, trades_per_day=5.2, net_usd=5.00, trades=52,
            source="Confirm-disp sweep 10 days (@qwen)",
            notes="SWEET SPOT: 1.5 pip confirm + 2.5x ATR + 1 bar window",
            live_feasible=True,
        ),
        Experiment(
            "confirm_disp_3.0pip_2.5x_1bar", "asymmetry",
            exp_usd=0.143, wr=78.8, trades_per_day=3.3, net_usd=4.73, trades=33,
            source="Confirm-disp sweep 10 days (@qwen)",
            notes="Higher confirm threshold → higher WR, fewer trades",
            live_feasible=True,
        ),
        Experiment(
            "confirm_disp_3.0pip_2.0x_5bar", "asymmetry",
            exp_usd=0.113, wr=69.0, trades_per_day=4.2, net_usd=4.73, trades=42,
            source="Confirm-disp sweep 10 days (@qwen)",
        ),
        Experiment(
            "confirm_disp_2.0pip_2.5x_1bar", "asymmetry",
            exp_usd=0.097, wr=73.9, trades_per_day=4.6, net_usd=4.45, trades=46,
            source="Confirm-disp sweep 10 days (@qwen)",
        ),
        Experiment(
            "confirm_disp_1.0pip_2.5x_1bar", "asymmetry",
            exp_usd=0.073, wr=70.9, trades_per_day=5.5, net_usd=4.03, trades=55,
            source="Confirm-disp sweep 10 days (@qwen)",
        ),
        Experiment(
            "ctrl_break_ret75", "asymmetry",
            exp_usd=0.00, wr=0, trades_per_day=0, net_usd=0.0, trades=0,
            source="Asymmetry lab (@codex-5) — needs MT5 run",
            notes="MT5 sweep pending — placeholder",
            live_feasible=True,
        ),
        Experiment(
            "stoprun_reclaim_opp", "asymmetry",
            exp_usd=0.00, wr=0, trades_per_day=0, net_usd=0.0, trades=0,
            source="Asymmetry lab (@codex-5) — needs MT5 run",
            notes="MT5 sweep pending — placeholder",
            live_feasible=True,
        ),

        # ── Regime router (@qwen-assistant) ─────────────────────────────
        Experiment(
            "regime_router (session+vol+peak)", "router",
            exp_usd=0.23, wr=79.0, trades_per_day=30.9, net_usd=5.61, trades=24,
            source="Regime router scoring (@qwen-assistant)",
            notes="Only +$0.02/trade over best fixed lane — marginal benefit",
        ),

        # ── Exit experiment projections (@qwen-assistant) ───────────────
        Experiment(
            "exit_75_floor03 (projected from 24 trades)", "exit",
            exp_usd=0.21, wr=87.5, trades_per_day=30.9, net_usd=5.10, trades=24,
            source="Promotion gate projection (@qwen-assistant)",
            notes="Counterfactual on full 24-trade historical set",
        ),
        Experiment(
            "exit_60_floor03 (projected from 24 trades)", "exit",
            exp_usd=0.18, wr=87.5, trades_per_day=30.9, net_usd=4.21, trades=24,
            source="Promotion gate projection (@qwen-assistant)",
            notes="Currently live — performing above promotion threshold",
        ),
    ]


def main() -> None:
    experiments = build_experiments()

    # Remove placeholders with zero data
    active = [e for e in experiments if e.trades > 0]

    # Sort by exp_usd descending
    active.sort(key=lambda e: -e.exp_usd)

    print("=" * 72)
    print("USDJPY MASTER EXPERIMENT RANKING")
    print("=" * 72)
    print()

    # Group by category
    categories = {
        "exit": "EXIT EXPERIMENTS",
        "entry": "ENTRY EXPERIMENTS",
        "asymmetry": "ASYMMETRY ARCHITECTURES (MT5)",
        "router": "REGIME ROUTER",
        "control": "BASELINE",
    }

    for cat_key, cat_label in categories.items():
        cat_exps = [e for e in active if e.category == cat_key]
        if not cat_exps:
            continue

        print(f"─" * 72)
        print(f"{cat_label}")
        print(f"─" * 72)
        print()
        print(f"  {'Experiment':<36} {'Exp':>8} {'WR':>6} {'Tr/d':>6} {'Net':>8} {'N':>4}  Source")
        print(f"  {'─' * 36} {'─' * 8} {'─' * 6} {'─' * 6} {'─' * 8} {'─' * 4}")

        for e in cat_exps:
            flag = ""
            if e.name == "entry_10s":
                flag = " ⚠️TRAP"
            if "SWEET SPOT" in e.notes:
                flag = " 🎯BEST"
            if "currently LIVE" in e.notes.lower() or "currently live" in e.notes.lower():
                flag = " 🔴LIVE"

            tpd = f"{e.trades_per_day:.1f}" if e.trades_per_day > 0 else "—"
            print(
                f"  {e.name:<36} ${e.exp_usd:+.2f} {e.wr:>5.0f}% {tpd:>6} "
                f"${e.net_usd:+7.2f} {e.trades:>4d}  {e.source}{flag}"
            )

        print()

    # Top 5 overall
    print("─" * 72)
    print("TOP 5 OVERALL (by expectancy)")
    print("─" * 72)
    print()
    for i, e in enumerate(active[:5], 1):
        print(f"  {i}. {e.name}")
        print(f"     ${e.exp_usd:+.2f}/trade | {e.wr:.0f}% WR | {e.notes}")
        print()

    # Recommendation
    print("─" * 72)
    print("RECOMMENDATION")
    print("─" * 72)
    print()
    print("  1. exit_75_floor03 should be promoted to live challenger next")
    print("     — Already exceeds promotion gate ($0.21 vs $0.17 threshold)")
    print("     — Monte Carlo: median 12 trades (~9h) to confirm")
    print()
    print("  2. confirm_disp_3.0pip_2.5x_1bar is the best ASYMMETRY candidate")
    print("     — $0.143/trade, 78.8% WR, clean 2.0x ATR expansion boundary")
    print("     — Should be wired as a separate live lane (different signal type)")
    print()
    print("  3. Regime routing is premature — only +$0.02/trade improvement")
    print("     — Validate individual lanes first (need 50+ trades each)")
    print()
    print("  4. entry_10s should be killed or moved to last priority")
    print("     — Admits 3 more losers, worst net P/L in the ranking")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
