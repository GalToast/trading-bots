#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

import MetaTrader5 as mt5

from backtest_adaptive_deployment_study import (
    CloseSpec,
    DeploymentContract,
    TIMEFRAME_MAP,
    load_bars,
    load_shape,
    normalize_close_specs,
    resolve_base_contract,
    safe_float,
    score_sort_key,
    simulate_contract,
)


ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_STUDY_PATH = ROOT / "reports" / "adaptive_deployment_backtest_study.json"
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
OUTPUT_CSV = ROOT / "reports" / "money_velocity_frontier.csv"
OUTPUT_MD = ROOT / "reports" / "money_velocity_frontier.md"
OUTPUT_JSON = ROOT / "reports" / "money_velocity_frontier.json"

FORCED_EXPERIMENTAL_LABELS = {
    "book_flat_sweep",
    "book_flat_gap0",
    "harvest_inner_hold_frontier",
    "close_early",
    "close_early_funded_rescue",
    "close_early_shallow",
    "close_deep_shallow",
    "hybrid_early_hold_deep",
    "hybrid_early_hold_deep_funded_rescue",
    "outer_fast_shallow_funded_rescue",
    "range_sweep_trend_reclaim_funded_rescue",
    "sweep_fast_gap0",
    "sweep_fast_gap0_funded_rescue",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Focused money-velocity frontier search around the current adaptive deployment winners."
    )
    parser.add_argument("--symbols", nargs="*", default=["BTCUSD", "GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--all-shape-symbols", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP.keys()), default="M15")
    parser.add_argument("--study-json", default=str(DEPLOYMENT_STUDY_PATH))
    parser.add_argument("--shape-library", default=str(SHAPE_LIBRARY_PATH))
    parser.add_argument("--regime-json", default=str(REGIME_PATH))
    parser.add_argument("--top-profiles", type=int, default=4)
    parser.add_argument("--step-scales", nargs="*", type=float, default=[0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0])
    parser.add_argument("--cap-deltas", nargs="*", type=int, default=[0, 3, 6])
    parser.add_argument("--max-mae-abs-usd", type=float, default=None)
    parser.add_argument("--max-final-open", type=int, default=None)
    parser.add_argument("--max-max-open", type=int, default=None)
    parser.add_argument("--require-realized-cover", action="store_true")
    parser.add_argument("--starting-balance-usd", type=float, default=None)
    parser.add_argument("--hard-floor-usd", type=float, default=None)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_study_rows(path: Path) -> list[dict[str, Any]]:
    return list((load_json(path).get("rows") or []))


def safe_study_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row.get("gross_positive_booked_usd_per_hour", row.get("realized_usd_per_hour", 0.0)) or 0.0),
        float(row.get("realized_usd_per_hour", 0.0) or 0.0),
        float(row.get("unified_objective_score", 0.0) or 0.0),
        float(row.get("combined_net_usd", 0.0) or 0.0),
    )


def load_shape_symbols(path: Path) -> list[str]:
    payload = load_json(path)
    symbols = list(((payload.get("symbols") or {}) or {}).keys())
    return sorted(str(symbol).upper() for symbol in symbols)


def load_regime_symbols(path: Path) -> list[str]:
    payload = load_json(path)
    return sorted(
        str(row.get("symbol") or "").upper()
        for row in list(payload.get("symbols") or [])
        if str(row.get("symbol") or "").strip()
    )


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    if not bool(args.all_shape_symbols):
        return [str(symbol).upper() for symbol in args.symbols]
    shape_symbols = load_shape_symbols(Path(args.shape_library))
    regime_symbols = set(load_regime_symbols(Path(args.regime_json)))
    return [symbol for symbol in shape_symbols if symbol in regime_symbols]


def select_candidate_close_specs(
    *,
    symbol: str,
    study_rows: list[dict[str, Any]],
    top_n_profiles: int = 4,
) -> list[CloseSpec]:
    available_specs = normalize_close_specs(load_shape(symbol))
    specs_by_label = {spec.label: spec for spec in available_specs}
    symbol_rows = [row for row in study_rows if str(row.get("symbol") or "").upper() == symbol.upper()]
    symbol_rows.sort(key=safe_study_sort_key, reverse=True)
    profile_labels: list[str] = []
    seen: set[str] = set()
    for row in symbol_rows:
        label = str(row.get("close_profile") or "")
        if label in seen or label not in specs_by_label:
            continue
        seen.add(label)
        profile_labels.append(label)
        if len(profile_labels) >= top_n_profiles:
            break
    for label in sorted(FORCED_EXPERIMENTAL_LABELS):
        if label in specs_by_label and label not in seen:
            seen.add(label)
            profile_labels.append(label)
    if "shape_contract" in specs_by_label and "shape_contract" not in seen:
        profile_labels.append("shape_contract")
    return [specs_by_label[label] for label in profile_labels if label in specs_by_label]


def build_local_frontier_contracts(
    *,
    symbol: str,
    timeframe: str,
    study_rows: list[dict[str, Any]],
    top_profiles: int,
    step_scales: list[float],
    cap_deltas: list[int],
) -> list[dict[str, Any]]:
    base = resolve_base_contract(symbol, timeframe)
    candidate_specs = select_candidate_close_specs(symbol=symbol, study_rows=study_rows, top_n_profiles=top_profiles)
    variants: list[dict[str, Any]] = []
    for step_scale in step_scales:
        for cap_delta in cap_deltas:
            max_open = max(4, base.max_open_per_side + cap_delta)
            for close_spec in candidate_specs:
                variants.append(
                    {
                        **base.__dict__,
                        "step_buy_px": round(base.step_buy_px * step_scale, 6),
                        "step_sell_px": round(base.step_sell_px * step_scale, 6),
                        "max_open_per_side": max_open,
                        "close_style": close_spec.style,
                        "close_alpha": close_spec.alpha,
                        "sell_gap": close_spec.sell_gap,
                        "buy_gap": close_spec.buy_gap,
                        "variant_label": f"{close_spec.label}_frontier_step{step_scale:.2f}_cap+{cap_delta}",
                        "step_scale": step_scale,
                        "cap_delta": cap_delta,
                        "close_profile": close_spec.label,
                    }
                )
    return variants


def build_summary(rows: list[dict[str, Any]], incumbents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    best_by_symbol: list[dict[str, Any]] = []
    leadership: list[str] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    for symbol in sorted(by_symbol):
        symbol_rows = by_symbol[symbol]
        best = max(symbol_rows, key=score_sort_key)
        incumbent = incumbents.get(symbol, {})
        incumbent_rate = safe_float(incumbent.get("realized_usd_per_hour"), 0.0) or 0.0
        delta = round(float(best["realized_usd_per_hour"]) - incumbent_rate, 3)
        leadership.append(
            f"{symbol} frontier best is `{best['variant_label']}` at `${best['realized_usd_per_hour']}/h` "
            f"vs incumbent `{incumbent.get('variant_label', 'unknown')}` `${incumbent_rate}/h` "
            f"(delta `${delta}/h`)."
        )
        best_by_symbol.append(best)
    overall_best = max(rows, key=score_sort_key) if rows else {}
    if overall_best:
        leadership.append(
            f"Highest money-velocity frontier row is `{overall_best['symbol']}:{overall_best['variant_label']}` "
            f"at `${overall_best['realized_usd_per_hour']}/h`."
        )
    return {
        "leadership": leadership,
        "best_by_symbol": best_by_symbol,
        "overall_best": overall_best,
    }


def constraint_filter_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "max_mae_abs_usd": safe_float(getattr(args, "max_mae_abs_usd", None)),
        "max_final_open": None if getattr(args, "max_final_open", None) is None else int(args.max_final_open),
        "max_max_open": None if getattr(args, "max_max_open", None) is None else int(args.max_max_open),
        "require_realized_cover": bool(getattr(args, "require_realized_cover", False)),
        "starting_balance_usd": safe_float(getattr(args, "starting_balance_usd", None)),
        "hard_floor_usd": safe_float(getattr(args, "hard_floor_usd", None)),
    }


def row_meets_constraints(row: dict[str, Any], args: argparse.Namespace) -> bool:
    max_mae_abs_usd = safe_float(getattr(args, "max_mae_abs_usd", None))
    if max_mae_abs_usd is not None and abs(float(row.get("max_adverse_excursion_usd", 0.0) or 0.0)) > max_mae_abs_usd:
        return False
    max_final_open = getattr(args, "max_final_open", None)
    if max_final_open is not None and int(row.get("final_open_count", 0) or 0) > int(max_final_open):
        return False
    max_max_open = getattr(args, "max_max_open", None)
    if max_max_open is not None and int(row.get("max_open_total", 0) or 0) > int(max_max_open):
        return False
    if bool(getattr(args, "require_realized_cover", False)):
        if float(row.get("min_realized_cover_gap_usd", 0.0) or 0.0) < 0.0:
            return False
    starting_balance_usd = safe_float(getattr(args, "starting_balance_usd", None))
    hard_floor_usd = safe_float(getattr(args, "hard_floor_usd", None))
    if starting_balance_usd is not None and hard_floor_usd is not None:
        min_equity = float(starting_balance_usd) + float(row.get("min_combined_equity_delta_usd", 0.0) or 0.0)
        if min_equity < float(hard_floor_usd):
            return False
    return True


def filter_frontier_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    return [row for row in rows if row_meets_constraints(row, args)]


def build_markdown(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    incumbents: dict[str, dict[str, Any]],
    *,
    timeframe: str,
    days: int,
    constraint_filter: dict[str, Any] | None = None,
    raw_row_count: int | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Money Velocity Frontier")
    lines.append("")
    lines.append(
        f"This focused study reopens geometry around the current adaptive deployment winners on `{timeframe}` bars over "
        f"`{days}` days and forces the strongest new close-order styles into the tournament."
    )
    if constraint_filter and any(value is not None for value in constraint_filter.values()):
        lines.append("")
        lines.append(
            "Constraint filter: "
            f"`max_mae_abs_usd={constraint_filter.get('max_mae_abs_usd')}` "
            f"`max_final_open={constraint_filter.get('max_final_open')}` "
            f"`max_max_open={constraint_filter.get('max_max_open')}` "
            f"`require_realized_cover={constraint_filter.get('require_realized_cover')}` "
            f"`starting_balance_usd={constraint_filter.get('starting_balance_usd')}` "
            f"`hard_floor_usd={constraint_filter.get('hard_floor_usd')}`."
        )
        if raw_row_count is not None:
            lines.append(f"Rows kept after filter: `{len(rows)}` of `{raw_row_count}`.")
    lines.append("")
    lines.append("## Leadership Read")
    lines.append("")
    for line in summary["leadership"]:
        lines.append(f"- {line}")
    lines.append("")
    lines.append("## Best Contracts")
    lines.append("")
    for item in summary["best_by_symbol"]:
        incumbent = incumbents.get(str(item["symbol"]), {})
        delta = round(float(item["realized_usd_per_hour"]) - float(incumbent.get("realized_usd_per_hour", 0.0) or 0.0), 3)
        lines.append(
            f"- `{item['symbol']}`: `{item['variant_label']}` -> `{item['close_style']}` alpha `{item['close_alpha']}` "
            f"gaps `{item['sell_gap']}/{item['buy_gap']}`, step `{item['step_scale']}`, cap `{item['max_open_per_side']}`. "
            f"Read: `${item['realized_usd_per_hour']}/h`, `${item['avg_close_usd']}` per close, "
            f"`{item['closes_per_hour']}` closes/h, MAE `${item['max_adverse_excursion_usd']}`, "
            f"cover floor `${item.get('min_realized_cover_gap_usd', 0.0)}`, "
            f"equity floor delta `${item.get('min_combined_equity_delta_usd', 0.0)}`, "
            f"delta vs incumbent `${delta}/h`."
        )
    lines.append("")
    lines.append("## Why")
    lines.append("")
    lines.append("- This study targets local frontier improvement, not global doctrine replacement.")
    lines.append("- It keeps the same replay engine as the canonical deployment study so deltas are directly comparable.")
    lines.append("- The candidate menu combines the strongest incumbent close families with forced experimental close-order styles.")
    return "\n".join(lines) + "\n"


def progress_output_path(output_json: str | Path) -> Path:
    path = Path(output_json)
    suffix = "".join(path.suffixes)
    if suffix:
        stem = path.name[: -len(suffix)]
        return path.with_name(f"{stem}.progress{suffix}")
    return path.with_name(f"{path.name}.progress")


def write_outputs(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    incumbents: dict[str, dict[str, Any]],
    *,
    args: argparse.Namespace,
    raw_row_count: int | None = None,
) -> None:
    out_csv = Path(args.output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "generated_at": utc_now_iso(),
        "timeframe": args.timeframe,
        "days": args.days,
        "symbols": args.symbols,
        "constraint_filter": constraint_filter_payload(args),
        "summary": summary,
        "incumbents": incumbents,
        "rows": rows,
    }
    Path(args.output_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    Path(args.output_md).write_text(
        build_markdown(
            rows,
            summary,
            incumbents,
            timeframe=args.timeframe,
            days=args.days,
            constraint_filter=constraint_filter_payload(args),
            raw_row_count=raw_row_count,
        ),
        encoding="utf-8",
    )


def write_checkpoint(
    rows: list[dict[str, Any]],
    incumbents: dict[str, dict[str, Any]],
    *,
    args: argparse.Namespace,
    completed_symbols: list[str],
    current_symbol: str | None,
    completed_contracts: int,
    total_contracts: int,
    elapsed_seconds: float,
    final: bool,
) -> None:
    if not rows:
        return
    rows_sorted = sorted(rows, key=score_sort_key, reverse=True)
    filtered_rows = filter_frontier_rows(rows_sorted, args)
    summary = build_summary(filtered_rows, incumbents)
    checkpoint_payload = {
        "generated_at": utc_now_iso(),
        "timeframe": args.timeframe,
        "days": args.days,
        "symbols": args.symbols,
        "constraint_filter": constraint_filter_payload(args),
        "completed_symbols": completed_symbols,
        "current_symbol": current_symbol,
        "completed_contracts": completed_contracts,
        "total_contracts": total_contracts,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "final": final,
        "summary": summary,
        "incumbents": incumbents,
        "rows": filtered_rows,
        "raw_row_count": len(rows_sorted),
    }
    target_json = Path(args.output_json) if final else progress_output_path(args.output_json)
    target_md = Path(args.output_md) if final else progress_output_path(args.output_md)
    target_csv = Path(args.output_csv) if final else progress_output_path(args.output_csv)
    target_json.write_text(json.dumps(checkpoint_payload, indent=2), encoding="utf-8")
    target_md.write_text(
        build_markdown(
            filtered_rows,
            summary,
            incumbents,
            timeframe=args.timeframe,
            days=args.days,
            constraint_filter=constraint_filter_payload(args),
            raw_row_count=len(rows_sorted),
        ),
        encoding="utf-8",
    )
    fieldnames = list(filtered_rows[0].keys()) if filtered_rows else []
    with target_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(filtered_rows)


def emit_progress(
    *,
    symbol: str,
    symbol_index: int,
    symbol_total: int,
    contract_index: int,
    contract_total: int,
    row: dict[str, Any],
    completed_contracts: int,
    total_contracts: int,
    elapsed_seconds: float,
) -> None:
    rate = completed_contracts / max(elapsed_seconds, 0.01)
    remaining = max(total_contracts - completed_contracts, 0)
    eta_seconds = remaining / max(rate, 1e-9)
    eta_minutes = eta_seconds / 60.0
    print(
        (
            f"[frontier] symbol {symbol_index}/{symbol_total} {symbol} "
            f"contract {contract_index}/{contract_total} "
            f"overall {completed_contracts}/{total_contracts} "
            f"best={row['variant_label']} rate=${row['realized_usd_per_hour']}/h "
            f"elapsed={elapsed_seconds/60.0:.1f}m eta={eta_minutes:.1f}m"
        ),
        flush=True,
    )


def main() -> int:
    args = parse_args()
    study_rows = load_study_rows(Path(args.study_json))
    symbols = resolve_symbols(args)
    incumbent_rows = {
        str(row.get("symbol") or "").upper(): row
        for row in list((((load_json(Path(args.study_json)).get("summary") or {}).get("best_by_symbol")) or []))
    }
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        rows: list[dict[str, Any]] = []
        symbol_contracts: dict[str, list[dict[str, Any]]] = {}
        total_contracts = 0
        for symbol in symbols:
            contracts = build_local_frontier_contracts(
                symbol=symbol,
                timeframe=args.timeframe,
                study_rows=study_rows,
                top_profiles=max(1, int(args.top_profiles)),
                step_scales=[float(v) for v in args.step_scales],
                cap_deltas=[int(v) for v in args.cap_deltas],
            )
            symbol_contracts[symbol] = contracts
            total_contracts += len(contracts)
        completed_contracts = 0
        completed_symbols: list[str] = []
        last_checkpoint = 0
        started = time.monotonic()
        print(
            f"[frontier] starting symbols={symbols} total_contracts={total_contracts} "
            f"progress_every={args.progress_every} checkpoint_every={args.checkpoint_every}",
            flush=True,
        )
        for symbol_index, symbol in enumerate(symbols, start=1):
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.timeframe, args.days)
            if not bars:
                continue
            contracts = symbol_contracts[symbol]
            print(
                f"[frontier] loaded {symbol} bars={len(bars)} contracts={len(contracts)}",
                flush=True,
            )
            for contract_index, contract_payload in enumerate(contracts, start=1):
                contract = DeploymentContract(**contract_payload)
                row = simulate_contract(contract, bars, info)
                rows.append(row)
                completed_contracts += 1
                elapsed = time.monotonic() - started
                progress_every = max(1, int(args.progress_every))
                checkpoint_every = max(1, int(args.checkpoint_every))
                if (
                    contract_index == 1
                    or contract_index == len(contracts)
                    or completed_contracts % progress_every == 0
                ):
                    emit_progress(
                        symbol=symbol,
                        symbol_index=symbol_index,
                        symbol_total=len(symbols),
                        contract_index=contract_index,
                        contract_total=len(contracts),
                        row=row,
                        completed_contracts=completed_contracts,
                        total_contracts=total_contracts,
                        elapsed_seconds=elapsed,
                    )
                if completed_contracts - last_checkpoint >= checkpoint_every:
                    write_checkpoint(
                        rows,
                        incumbent_rows,
                        args=args,
                        completed_symbols=completed_symbols,
                        current_symbol=symbol,
                        completed_contracts=completed_contracts,
                        total_contracts=total_contracts,
                        elapsed_seconds=elapsed,
                        final=False,
                    )
                    last_checkpoint = completed_contracts
                    print(
                        f"[frontier] checkpoint wrote after {completed_contracts}/{total_contracts} contracts",
                        flush=True,
                    )
            completed_symbols.append(symbol)
            write_checkpoint(
                rows,
                incumbent_rows,
                args=args,
                completed_symbols=completed_symbols,
                current_symbol=None,
                completed_contracts=completed_contracts,
                total_contracts=total_contracts,
                elapsed_seconds=time.monotonic() - started,
                final=False,
            )
            print(f"[frontier] symbol complete {symbol} ({symbol_index}/{len(symbols)})", flush=True)
        if not rows:
            print("No frontier rows generated.")
            return 1
        rows.sort(key=score_sort_key, reverse=True)
        filtered_rows = filter_frontier_rows(rows, args)
        if rows and not filtered_rows:
            print("[frontier] no rows satisfied the requested survivability constraints", flush=True)
        summary = build_summary(filtered_rows, incumbent_rows)
        write_outputs(filtered_rows, summary, incumbent_rows, args=args, raw_row_count=len(rows))
        print(f"Wrote {args.output_csv}")
        print(f"Wrote {args.output_md}")
        print(f"Wrote {args.output_json}")
        return 0
    except KeyboardInterrupt:
        write_checkpoint(
            rows,
            incumbent_rows,
            args=args,
            completed_symbols=completed_symbols,
            current_symbol=None,
            completed_contracts=completed_contracts,
            total_contracts=total_contracts,
            elapsed_seconds=time.monotonic() - started,
            final=False,
        )
        print("[frontier] interrupted; wrote progress checkpoint", flush=True)
        return 130
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
