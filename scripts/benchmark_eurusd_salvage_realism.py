#!/usr/bin/env python3
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_fx_low_step_realism import REALISM_MODES, simulate_asymmetric_realism
from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_lab_v2 import load_bars


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "eurusd_salvage_realism.csv"
OUTPUT_MD = ROOT / "reports" / "eurusd_salvage_realism.md"

SYMBOL = "EURUSD"
DAYS = 60
CLOSE_STYLE = "all_profitable"
CLOSE_GAP = 1
CLOSE_ALPHA = 1.0


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
    Candidate("asymmetric_wide_sell", step_sell=1.5, step_buy=1.0),
    Candidate("asymmetric_tight_buy", step_sell=1.0, step_buy=0.5),
    Candidate("legacy_reference", step_sell=1.0, step_buy=1.0),
    Candidate("wider_symmetric", step_sell=1.5, step_buy=1.5),
]


def build_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# EURUSD Salvage Realism Audit",
        "",
        "This audit reruns the corrected EURUSD salvage neighborhood under stricter modeled-live bar semantics.",
        "",
        "## Current Read",
        "",
    ]

    modeled_live_rows = [row for row in rows if row["realism_mode"] == "broker_touch_bar_close"]
    modeled_live_rows.sort(key=lambda row: float(row["combined_net_usd"]), reverse=True)
    top_modeled_live = modeled_live_rows[0]
    corrected = next(row for row in modeled_live_rows if row["candidate"] == "corrected_winner")
    legacy = next(row for row in modeled_live_rows if row["candidate"] == "legacy_reference")
    corrected_intrabar = next(
        row for row in rows if row["candidate"] == "corrected_winner" and row["realism_mode"] == "intrabar_intrabar"
    )

    lines.append(
        f"- Corrected offline leader `{corrected['shape']}` lands at `${float(corrected['combined_net_usd']):.2f}` under "
        f"`broker_touch + bar_close`, versus legacy `{legacy['shape']}` at `${float(legacy['combined_net_usd']):.2f}` "
        f"(delta `${float(corrected['delta_vs_legacy_mode']):.2f}`). Retention vs its own intrabar read is "
        f"`{float(corrected['retention_vs_intrabar_pct']):.1f}%` with `{corrected['realized_closes']}` closes and "
        f"`{corrected['same_bar_roundtrip_pct']}`% same-bar round-trips "
        f"(intrabar closes `{corrected_intrabar['realized_closes']}`)."
    )
    lines.append(
        f"- The best modeled-live row is `{top_modeled_live['shape']}` "
        f"(`{top_modeled_live['candidate']}`) at `${float(top_modeled_live['combined_net_usd']):.2f}` with "
        f"`{top_modeled_live['same_bar_roundtrip_pct']}`% same-bar round-trips."
    )

    if top_modeled_live["candidate"] == "corrected_winner":
        lines.append("- The corrected tight winner survives the first modeled-live gate and stays on top of its local neighborhood.")
    else:
        lines.append(
            f"- The corrected tight winner does not hold the modeled-live top spot; `{top_modeled_live['shape']}` leads the realistic neighborhood instead."
        )

    lines.extend(
        [
            "",
            "## Modeled-Live Ranking",
            "",
            "| Rank | Candidate | Shape | Combined | Realized | Floating | Closes | Same-Bar RT | Delta vs Legacy | Retention vs Intrabar |",
            "|------|-----------|-------|----------|----------|----------|--------|-------------|-----------------|-----------------------|",
        ]
    )
    for idx, row in enumerate(modeled_live_rows, start=1):
        lines.append(
            f"| {idx} | {row['candidate']} | {row['shape']} | ${float(row['combined_net_usd']):.2f} | "
            f"${float(row['realized_net_usd']):.2f} | ${float(row['floating_net_usd']):.2f} | "
            f"{row['realized_closes']} | {row['same_bar_roundtrip_pct']}% | "
            f"${float(row['delta_vs_legacy_mode']):.2f} | {row['retention_vs_intrabar_pct']}% |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is the first realism gate after correcting the salvage sweep asymmetry bug.",
            "- If the corrected offline winner collapses or trails the legacy 1.0/1.0 branch here, treat the rescue as churn-inflated until forward data says otherwise.",
            "- Only the best `broker_touch + bar_close` survivor should advance to held-out forward proof.",
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
        bars = load_bars(SYMBOL, DAYS)
        if not bars:
            print(f"No bars for {SYMBOL}")
            return 1
        max_open_per_side = cfg_map[SYMBOL].max_open_per_side

        rows: list[dict[str, object]] = []
        results: dict[tuple[str, str], dict[str, float | int]] = {}

        for candidate in CANDIDATES:
            for realism in REALISM_MODES:
                results[(candidate.label, realism.name)] = simulate_asymmetric_realism(
                    SYMBOL,
                    bars,
                    info,
                    step_sell=candidate.step_sell,
                    step_buy=candidate.step_buy,
                    max_open_per_side=max_open_per_side,
                    close_gap=CLOSE_GAP,
                    close_alpha=CLOSE_ALPHA,
                    close_style=CLOSE_STYLE,
                    open_realism_mode=realism.open_mode,
                    close_realism_mode=realism.close_mode,
                )

        for realism in REALISM_MODES:
            legacy_result = results[("legacy_reference", realism.name)]
            for candidate in CANDIDATES:
                result = results[(candidate.label, realism.name)]
                intrabar_result = results[(candidate.label, "intrabar_intrabar")]
                rows.append(
                    {
                        "symbol": SYMBOL,
                        "candidate": candidate.label,
                        "shape": candidate.shape,
                        "days": DAYS,
                        "close_style": CLOSE_STYLE,
                        "close_gap": CLOSE_GAP,
                        "close_alpha": CLOSE_ALPHA,
                        "realism_mode": realism.name,
                        "open_realism_mode": realism.open_mode,
                        "close_realism_mode": realism.close_mode,
                        "combined_net_usd": result["combined_net_usd"],
                        "realized_net_usd": result["realized_net_usd"],
                        "floating_net_usd": result["floating_net_usd"],
                        "realized_closes": result["realized_closes"],
                        "open_events": result["open_events"],
                        "close_events": result["close_events"],
                        "same_bar_roundtrips": result["same_bar_roundtrips"],
                        "same_bar_roundtrip_pct": result["same_bar_roundtrip_pct"],
                        "avg_realized_per_close_usd": result["avg_realized_per_close_usd"],
                        "max_open_total": result["max_open_total"],
                        "max_open_buy": result["max_open_buy"],
                        "max_open_sell": result["max_open_sell"],
                        "legacy_mode_combined_usd": legacy_result["combined_net_usd"],
                        "delta_vs_legacy_mode": round(
                            float(result["combined_net_usd"]) - float(legacy_result["combined_net_usd"]),
                            3,
                        ),
                        "intrabar_combined_usd": intrabar_result["combined_net_usd"],
                        "retention_vs_intrabar_pct": round(
                            (float(result["combined_net_usd"]) / float(intrabar_result["combined_net_usd"]) * 100.0)
                            if float(intrabar_result["combined_net_usd"]) != 0.0
                            else 0.0,
                            1,
                        ),
                    }
                )

        with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        OUTPUT_MD.write_text(build_markdown([{k: str(v) for k, v in row.items()} for row in rows]), encoding="utf-8")
        print(f"Wrote {OUTPUT_CSV}")
        print(f"Wrote {OUTPUT_MD}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
