#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV_PATH = ROOT / "reports" / "fx_fixed_step_close_policy_ladder.csv"
DEFAULT_OUTPUT_PATH = ROOT / "reports" / "fx_close_policy_promotion_recommendation.md"
DEFAULT_SYMBOLS = ("EURUSD", "GBPUSD")
DEFAULT_PRACTICAL_MAX_ALPHA = 0.5


@dataclass(frozen=True)
class LadderRow:
    symbol: str
    policy: str
    close_alpha: float
    baseline_combined_usd: float
    variant_combined_usd: float
    delta_combined_usd: float


@dataclass(frozen=True)
class Candidate:
    policy: str
    combined_usd: float
    delta_vs_baseline_usd: float
    by_symbol: dict[str, float]


@dataclass(frozen=True)
class MixedPackage:
    combined_usd: float
    delta_vs_baseline_usd: float
    by_symbol_policy: dict[str, str]
    by_symbol_usd: dict[str, float]


def load_ladder_rows(path: Path) -> list[LadderRow]:
    rows: list[LadderRow] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            rows.append(
                LadderRow(
                    symbol=str(raw["symbol"]).upper(),
                    policy=str(raw["policy"]),
                    close_alpha=float(raw["close_alpha"]),
                    baseline_combined_usd=float(raw["baseline_combined_usd"]),
                    variant_combined_usd=float(raw["variant_combined_usd"]),
                    delta_combined_usd=float(raw["delta_combined_usd"]),
                )
            )
    return rows


def baseline_total(rows: list[LadderRow], symbols: list[str]) -> float:
    total = 0.0
    for symbol in symbols:
        symbol_rows = [row for row in rows if row.symbol == symbol]
        if not symbol_rows:
            raise ValueError(f"Missing ladder rows for symbol {symbol}")
        total += symbol_rows[0].baseline_combined_usd
    return total


def practical_rows(rows: list[LadderRow], symbols: list[str], practical_max_alpha: float) -> list[LadderRow]:
    wanted = set(symbols)
    filtered = [
        row
        for row in rows
        if row.symbol in wanted and row.close_alpha <= practical_max_alpha
    ]
    if not filtered:
        raise ValueError("No ladder rows matched the requested symbols and alpha filter")
    return filtered


def unified_candidates(rows: list[LadderRow], symbols: list[str], practical_max_alpha: float) -> list[Candidate]:
    usable = practical_rows(rows, symbols, practical_max_alpha)
    symbol_set = set(symbols)
    candidates: list[Candidate] = []
    for policy in sorted({row.policy for row in usable}):
        matching = [row for row in usable if row.policy == policy]
        if {row.symbol for row in matching} != symbol_set:
            continue
        by_symbol = {row.symbol: row.variant_combined_usd for row in matching}
        combined = sum(by_symbol.values())
        baseline = sum(row.baseline_combined_usd for row in matching)
        candidates.append(
            Candidate(
                policy=policy,
                combined_usd=combined,
                delta_vs_baseline_usd=combined - baseline,
                by_symbol=by_symbol,
            )
        )
    candidates.sort(key=lambda item: item.combined_usd, reverse=True)
    return candidates


def mixed_package(rows: list[LadderRow], symbols: list[str], practical_max_alpha: float) -> MixedPackage:
    usable = practical_rows(rows, symbols, practical_max_alpha)
    by_symbol_policy: dict[str, str] = {}
    by_symbol_usd: dict[str, float] = {}
    total = 0.0
    baseline = 0.0
    for symbol in symbols:
        symbol_rows = [row for row in usable if row.symbol == symbol]
        if not symbol_rows:
            raise ValueError(f"Missing practical ladder rows for symbol {symbol}")
        best = max(symbol_rows, key=lambda row: row.variant_combined_usd)
        by_symbol_policy[symbol] = best.policy
        by_symbol_usd[symbol] = best.variant_combined_usd
        total += best.variant_combined_usd
        baseline += best.baseline_combined_usd
    return MixedPackage(
        combined_usd=total,
        delta_vs_baseline_usd=total - baseline,
        by_symbol_policy=by_symbol_policy,
        by_symbol_usd=by_symbol_usd,
    )


def build_report(rows: list[LadderRow], symbols: list[str], practical_max_alpha: float) -> str:
    baseline = baseline_total(rows, symbols)
    unified = unified_candidates(rows, symbols, practical_max_alpha)
    if not unified:
        raise ValueError("No unified candidates found")
    best_unified = unified[0]
    best_mixed = mixed_package(rows, symbols, practical_max_alpha)
    mixed_edge = best_mixed.combined_usd - best_unified.combined_usd

    nzd_row = None
    for row in practical_rows(rows, ["NZDUSD"], practical_max_alpha):
        if row.policy == "allprof_gap1_alpha50":
            nzd_row = row
            break

    lines: list[str] = []
    lines.append("# FX Close Policy Promotion Recommendation")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("**Status:** SHADOW-RUNNABLE ONLY - DO NOT PROMOTE DIRECTLY")
    lines.append("**Scope:** practical close-policy comparison (`alpha <= 0.5`) for the current live-rearm symbol set")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- Current live-rearm symbols are `{ ' + '.join(symbols) }`.")
    lines.append(
        f"- Best practical unified one-policy candidate from `reports/fx_fixed_step_close_policy_ladder.csv` is "
        f"`{best_unified.policy}` at `${best_unified.combined_usd:.2f}`, which is `${best_unified.delta_vs_baseline_usd:+.2f}` "
        f"over the fixed-step ladder baseline `${baseline:.2f}` for this symbol set."
    )
    mixed_symbols = ", ".join(
        f"`{symbol}={best_mixed.by_symbol_policy[symbol]}`" for symbol in symbols
    )
    lines.append(
        f"- Best practical mixed per-symbol package is {mixed_symbols} at `${best_mixed.combined_usd:.2f}`, "
        f"which is `${best_mixed.delta_vs_baseline_usd:+.2f}` over baseline and `${mixed_edge:+.2f}` versus the best unified candidate."
    )
    lines.append(
        "- That mixed package now has a real shadow launch path via "
        "`--raw-symbol-overrides-path configs/fx_raw_symbol_overrides_close_policy_mixed.json`, "
        "but it still has no forward-proof read and should stay shadow-only."
    )
    lines.append("")
    lines.append("## Practical Unified Candidates")
    lines.append("")
    lines.append("| Policy | " + " | ".join(symbols) + " | Combined | Delta vs baseline |")
    lines.append("|---|---|" + "---|" * (len(symbols) - 1) + "---|---|")
    for candidate in unified:
        symbol_values = " | ".join(f"${candidate.by_symbol[symbol]:.2f}" for symbol in symbols)
        lines.append(
            f"| `{candidate.policy}` | {symbol_values} | `${candidate.combined_usd:.2f}` | `${candidate.delta_vs_baseline_usd:+.2f}` |"
        )
    lines.append("")
    lines.append("## Best Practical Mixed Package")
    lines.append("")
    lines.append("| Symbol | Best policy | Combined | Delta vs baseline |")
    lines.append("|---|---|---|---|")
    for symbol in symbols:
        symbol_rows = [row for row in rows if row.symbol == symbol and row.policy == best_mixed.by_symbol_policy[symbol]]
        best_row = max(symbol_rows, key=lambda row: row.variant_combined_usd)
        lines.append(
            f"| `{symbol}` | `{best_mixed.by_symbol_policy[symbol]}` | `${best_mixed.by_symbol_usd[symbol]:.2f}` | `${best_row.delta_combined_usd:+.2f}` |"
        )
    lines.append(
        f"| **Basket** | mixed per-symbol map | `${best_mixed.combined_usd:.2f}` | `${best_mixed.delta_vs_baseline_usd:+.2f}` |"
    )
    lines.append("")
    lines.append("## Launch Path")
    lines.append("")
    lines.append(
        "- `scripts/live_penetration_lattice_tick_shadow.py` now supports raw `close_style` plus `--raw-symbol-overrides-path` for raw FX lanes."
    )
    lines.append(
        "- Checked-in mixed-policy override file: `configs/fx_raw_symbol_overrides_close_policy_mixed.json`."
    )
    lines.append(
        "- Example shadow launch:"
    )
    lines.append("```bash")
    lines.append("python scripts/live_penetration_lattice_tick_shadow.py --symbols EURUSD GBPUSD --raw-rearm-variant rearm_lvl2_exc2 --raw-symbol-overrides-path configs/fx_raw_symbol_overrides_close_policy_mixed.json --state-path reports/penetration_lattice_shadow_fx_close_policy_mixed_state.json --event-path reports/penetration_lattice_shadow_fx_close_policy_mixed_events.jsonl --poll-seconds 5")
    lines.append("```")
    lines.append(
        "- Keep current live lanes unchanged until that shadow lane accumulates honest forward evidence."
    )
    lines.append("")
    lines.append("## Honest Recommendation")
    lines.append("")
    lines.append(
        "- Do not treat the mixed close-policy map as ready for live promotion. It is now runnable in shadow, but still unproven in forward execution."
    )
    lines.append(
        f"- If the goal is a **current-runner-compatible** next test, the honest unified candidate is `{best_unified.policy}`. "
        "That still belongs in shadow/forward validation first, not direct live promotion."
    )
    lines.append(
        "- If the goal is the **higher offline ceiling**, the plumbing now exists; the next gate is a supervised shadow run and then forward-proof review before touching `live_rearm_941777`."
    )
    lines.append(
        "- Keep the close-policy decision separate from the live `alpha=0.5` vs `alpha=1.0` argument. This report compares only the practical fixed-step close-policy map."
    )
    if nzd_row is not None:
        lines.append(
            f"- `NZDUSD` still aligns with `GBPUSD` on `{nzd_row.policy}` (`${nzd_row.variant_combined_usd:.2f}`), "
            "so if NZD returns to the rearm research basket, the mixed map stays `EUR=outer_gap2_alpha50`, `GBP/NZD=allprof_gap1_alpha50`."
        )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append("- `reports/fx_fixed_step_close_policy_ladder.csv`")
    lines.append("- `docs/penetration-lattice-program.md`")
    lines.append("- `reports/fx_live_alpha_recent_audit.md`")
    lines.append("- `configs/fx_raw_symbol_overrides_close_policy_mixed.json`")
    lines.append("- `scripts/live_penetration_lattice_tick_shadow.py`")
    lines.append("- `scripts/tick_penetration_lattice_core.py`")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare mixed per-symbol FX close-policy winners against the best unified live-compatible basket."
    )
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--output-path", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--practical-max-alpha", type=float, default=DEFAULT_PRACTICAL_MAX_ALPHA)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv_path)
    output_path = Path(args.output_path)
    symbols = [str(symbol).upper() for symbol in args.symbols]
    rows = load_ladder_rows(csv_path)
    report = build_report(rows, symbols, float(args.practical_max_alpha))
    output_path.write_text(report + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
