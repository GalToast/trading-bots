#!/usr/bin/env python3
from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_eurusd_forward_shadow import fetch_m15_bars, pip_size_for, simulate
from benchmark_inside_geometry_churn import default_raw_configs


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "eurusd_salvage_forward_shadow.csv"
OUTPUT_MD = ROOT / "reports" / "eurusd_salvage_forward_shadow.md"

SYMBOL = "EURUSD"
REFERENCE_DAYS = 60
FORWARD_DAYS = 7
CLOSE_STYLE = "all_profitable"
CLOSE_GAP = 1
CLOSE_ALPHA = 1.0
OPEN_REALISM_MODE = "broker_touch"
CLOSE_REALISM_MODE = "bar_close"


@dataclass(frozen=True)
class Candidate:
    label: str
    step_sell: float
    step_buy: float

    @property
    def shape(self) -> str:
        return f"sell={self.step_sell:g}/buy={self.step_buy:g}"


CANDIDATES = [
    Candidate("corrected_winner", step_sell=0.5, step_buy=0.5),
    Candidate("runner_up_tight_sell", step_sell=0.5, step_buy=1.0),
    Candidate("legacy_reference", step_sell=1.0, step_buy=1.0),
    Candidate("wider_symmetric", step_sell=1.5, step_buy=1.5),
]


def build_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# EURUSD Salvage Forward Shadow",
        "",
        "This audit tests the corrected EURUSD salvage neighborhood on a held-out 7-day forward window using modeled-live bar semantics.",
        "",
        "## Current Read",
        "",
    ]

    forward_rows = [row for row in rows if row["period"] == "forward_7d"]
    forward_rows.sort(key=lambda row: float(row["combined_net_usd"]), reverse=True)
    corrected = next(row for row in forward_rows if row["candidate"] == "corrected_winner")
    legacy = next(row for row in forward_rows if row["candidate"] == "legacy_reference")
    top_forward = forward_rows[0]

    lines.append(
        f"- Corrected winner `{corrected['shape']}` prints `${float(corrected['combined_net_usd']):.2f}` on the held-out 7-day window "
        f"with `{corrected['realized_closes']}` closes and `{corrected['same_bar_roundtrip_pct']}`% same-bar round-trips."
    )
    lines.append(
        f"- Legacy `{legacy['shape']}` prints `${float(legacy['combined_net_usd']):.2f}` on the same window, so the corrected winner is "
        f"`{float(corrected['delta_vs_legacy_forward']):+.2f}` versus legacy."
    )
    lines.append(
        f"- The best forward row is `{top_forward['shape']}` (`{top_forward['candidate']}`) at "
        f"`${float(top_forward['combined_net_usd']):.2f}`."
    )

    corrected_positive = float(corrected["combined_net_usd"]) > 0.0
    corrected_has_closes = int(corrected["realized_closes"]) > 0
    top_forward_positive = float(top_forward["combined_net_usd"]) > 0.0

    if top_forward["candidate"] == "corrected_winner" and corrected_positive and corrected_has_closes:
        lines.append("- The corrected offline winner also tops the held-out forward window with positive, realized evidence, so the rescue survives both the bug fix and the first forward gate.")
    elif top_forward["candidate"] == "corrected_winner":
        lines.append("- The corrected offline winner ranks first on the held-out week, but it is still not promotable because the forward row is negative or lacks realized closes.")
    else:
        lines.append(
            f"- The corrected offline winner does not lead the held-out forward window; `{top_forward['shape']}` takes the forward top spot instead."
        )

    if not top_forward_positive:
        lines.append("- Every tested candidate is still negative on the held-out forward week, so the EUR salvage branch remains offline-only.")

    lines.extend(
        [
            "",
            "## Forward Ranking",
            "",
            "| Rank | Candidate | Shape | Forward Combined | Forward Realized | Forward Floating | Forward Closes | Same-Bar RT | Prior-60d Combined | Prior 60d -> 7d Scaled | Delta vs Legacy Forward |",
            "|------|-----------|-------|------------------|------------------|------------------|----------------|-------------|--------------------|------------------------|-------------------------|",
        ]
    )
    for idx, row in enumerate(forward_rows, start=1):
        lines.append(
            f"| {idx} | {row['candidate']} | {row['shape']} | ${float(row['combined_net_usd']):.2f} | "
            f"${float(row['realized_net_usd']):.2f} | ${float(row['floating_net_usd']):.2f} | {row['realized_closes']} | "
            f"{row['same_bar_roundtrip_pct']}% | ${float(row['reference_combined_usd']):.2f} | "
            f"${float(row['reference_scaled_to_forward_usd']):.2f} | ${float(row['delta_vs_legacy_forward']):+.2f} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Reference rows come from the prior 60-day window ending at the forward-window boundary, so the forward window is cleanly held out.",
            "- If the corrected winner is positive here but loses the local ranking, it stays alive as a branch but not as the clear EUR rescue leader.",
            "- If the corrected winner goes negative or churn-dominant here, the rescue stays offline-only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        info = mt5.symbol_info(SYMBOL)
        if info is None:
            print(f"{SYMBOL} symbol info unavailable")
            return 1

        now = int(time.time())
        forward_end = now
        reference_end = now - (FORWARD_DAYS * 86400)

        reference_bars = fetch_m15_bars(SYMBOL, days=REFERENCE_DAYS, end_time=reference_end)
        forward_bars = fetch_m15_bars(SYMBOL, days=FORWARD_DAYS, end_time=forward_end)
        if not reference_bars or not forward_bars:
            print("Not enough EURUSD bars for reference/forward windows")
            return 1

        max_open_per_side = cfg_map[SYMBOL].max_open_per_side
        rows: list[dict[str, object]] = []
        forward_results: dict[str, dict[str, float | int]] = {}

        for candidate in CANDIDATES:
            kwargs = {
                "step_sell": candidate.step_sell,
                "step_buy": candidate.step_buy,
                "max_open_per_side": max_open_per_side,
                "close_alpha": CLOSE_ALPHA,
                "close_style": CLOSE_STYLE,
                "sell_gap": CLOSE_GAP,
                "buy_gap": CLOSE_GAP,
                "open_realism_mode": OPEN_REALISM_MODE,
                "close_realism_mode": CLOSE_REALISM_MODE,
            }
            reference_result = simulate(SYMBOL, reference_bars, info, **kwargs)
            forward_result = simulate(SYMBOL, forward_bars, info, **kwargs)
            forward_results[candidate.label] = forward_result

            for period_name, bar_count, result in (
                ("reference_60d", len(reference_bars), reference_result),
                ("forward_7d", len(forward_bars), forward_result),
            ):
                rows.append(
                    {
                        "symbol": SYMBOL,
                        "candidate": candidate.label,
                        "shape": candidate.shape,
                        "period": period_name,
                        "bars": bar_count,
                        "days": REFERENCE_DAYS if period_name == "reference_60d" else FORWARD_DAYS,
                        "combined_net_usd": result["combined_net_usd"],
                        "realized_net_usd": result["realized_net_usd"],
                        "floating_net_usd": result["floating_net_usd"],
                        "realized_closes": result["realized_closes"],
                        "same_bar_roundtrip_pct": result["same_bar_roundtrip_pct"],
                        "max_open_total": result["max_open_total"],
                        "reference_combined_usd": reference_result["combined_net_usd"],
                        "reference_scaled_to_forward_usd": round(
                            float(reference_result["combined_net_usd"]) * FORWARD_DAYS / REFERENCE_DAYS,
                            3,
                        ),
                    }
                )

        legacy_forward = forward_results["legacy_reference"]
        for row in rows:
            if row["period"] == "forward_7d":
                row["delta_vs_legacy_forward"] = round(
                    float(row["combined_net_usd"]) - float(legacy_forward["combined_net_usd"]),
                    3,
                )
            else:
                row["delta_vs_legacy_forward"] = ""

        with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        OUTPUT_MD.write_text(build_markdown([{k: str(v) for k, v in row.items()} for row in rows]), encoding="utf-8")
        print(f"Spread: {info.spread} points, pip: {pip_size_for(info)}")
        print(f"Wrote {OUTPUT_CSV}")
        print(f"Wrote {OUTPUT_MD}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
