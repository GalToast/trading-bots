#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_OPPORTUNITY_PATH = REPORTS / "kraken_maker_opportunity_board.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_MICROFILL_PATH = REPORTS / "kraken_maker_microfill_calibration_summary.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_microcloud_eligibility.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_microcloud_eligibility.md"
FILL_LIKE_RESULTS = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}
DEFAULT_OFFSET_FRACS = (0.0, 0.25, 0.5, 0.75)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_int_list(value: str) -> list[int]:
    out: list[int] = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        count = int(float(raw))
        if count > 0 and count not in out:
            out.append(count)
    return out or [2, 3, 5]


def parse_float_list(value: str) -> list[float]:
    out: list[float] = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        amount = float(raw)
        if amount > 0.0 and amount not in out:
            out.append(amount)
    return out or [10.0, 25.0, 50.0]


def rows_by_product(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "").upper()
        if product_id:
            out[product_id] = row
    return out


def counter_rate(row: Any) -> tuple[float | None, int, int]:
    if not isinstance(row, dict):
        return None, 0, 0
    total = sum(int(value) for value in row.values() if isinstance(value, int))
    fills = sum(int(row.get(result, 0)) for result in FILL_LIKE_RESULTS)
    if total <= 0:
        return None, 0, 0
    return max(0.0, min(1.0, fills / total)), total, fills


def rate_row(
    summary: dict[str, Any],
    product_id: str,
    side: str,
    *,
    min_samples: int,
) -> dict[str, Any]:
    product_id = str(product_id).upper()
    side = str(side).lower()
    side_row = (summary.get("by_product_side") or {}).get(f"{product_id}|{side}")
    rate, samples, fills = counter_rate(side_row)
    source = "by_product_side"
    if samples < min_samples:
        product_row = (summary.get("by_product") or {}).get(product_id)
        product_rate, product_samples, product_fills = counter_rate(product_row)
        if product_samples > samples:
            rate, samples, fills = product_rate, product_samples, product_fills
            source = "by_product_fallback"
    return {
        "rate": round(rate, 6) if rate is not None else None,
        "samples": samples,
        "fills": fills,
        "source": source if samples > 0 else "missing",
        "sample_eligible": samples >= min_samples,
    }


def offset_rate_row(
    summary: dict[str, Any],
    product_id: str,
    side: str,
    offset_frac: float,
    *,
    min_samples: int,
) -> dict[str, Any]:
    key = f"{str(product_id).upper()}|{str(side).lower()}|{float(offset_frac):.4f}"
    row = (summary.get("by_product_side_offset") or {}).get(key)
    rate, samples, fills = counter_rate(row)
    return {
        "offset_frac": round(float(offset_frac), 6),
        "rate": round(rate, 6) if rate is not None else None,
        "samples": samples,
        "fills": fills,
        "sample_eligible": samples >= min_samples,
    }


def tickback_rate_row(
    summary: dict[str, Any],
    product_id: str,
    side: str,
    tick_back: int,
    *,
    min_samples: int,
) -> dict[str, Any]:
    key = f"{str(product_id).upper()}|{str(side).lower()}|{int(tick_back)}"
    row = (summary.get("by_product_side_tick_offset") or {}).get(key)
    rate, samples, fills = counter_rate(row)
    return {
        "tick_back": int(tick_back),
        "rate": round(rate, 6) if rate is not None else None,
        "samples": samples,
        "fills": fills,
        "sample_eligible": samples >= min_samples,
    }


def tickback_microfill_ok(
    rows: list[dict[str, Any]],
    *,
    min_rate: float,
    min_samples: int,
    required_tick_backs: tuple[int, ...] = (1, 2),
) -> bool:
    by_tick = {to_int(row.get("tick_back")): row for row in rows}
    return all(
        eligible_rate(by_tick.get(tick_back, {}), min_rate, min_samples=min_samples)
        for tick_back in required_tick_backs
    )


def eligible_rate(row: dict[str, Any], threshold: float, *, min_samples: int) -> bool:
    rate = row.get("rate")
    return (
        row.get("samples", 0) >= min_samples
        and rate is not None
        and to_float(rate) >= threshold
    )


def slice_option(
    *,
    quote_usd: float,
    slice_count: int,
    maker_fee_bps: float,
    min_notional_usd: float,
) -> dict[str, Any]:
    slice_quote = quote_usd / max(1, slice_count)
    post_fee_notional = slice_quote * (1.0 - maker_fee_bps / 10000.0)
    min_valid = min_notional_usd <= 0.0 or post_fee_notional >= min_notional_usd
    return {
        "slice_count": slice_count,
        "slice_quote_usd": round(slice_quote, 6),
        "post_fee_order_notional_usd": round(post_fee_notional, 6),
        "min_notional_usd": round(min_notional_usd, 6),
        "min_notional_valid": min_valid,
        "min_notional_shortfall_usd": round(max(0.0, min_notional_usd - post_fee_notional), 6),
    }


def build_payload(
    *,
    opportunity_path: Path = DEFAULT_OPPORTUNITY_PATH,
    radar_path: Path = DEFAULT_RADAR_PATH,
    microfill_summary_path: Path = DEFAULT_MICROFILL_PATH,
    quote_usd: float = 25.0,
    quote_usd_scenarios: list[float] | None = None,
    maker_fee_bps: float = 25.0,
    slice_counts: list[int] | None = None,
    min_entry_rate: float = 0.10,
    min_exit_rate: float = 0.25,
    min_offset_rate: float = 0.10,
    min_offset_samples: int = 6,
) -> dict[str, Any]:
    opportunity = load_json(opportunity_path)
    radar = load_json(radar_path)
    microfill = load_json(microfill_summary_path)
    radar_by_product = rows_by_product(radar)
    counts = slice_counts or [2, 3, 5]
    rows = opportunity.get("rows") if isinstance(opportunity.get("rows"), list) else []
    scenario_quotes = quote_usd_scenarios or [10.0, 25.0, 50.0]

    product_rows: list[dict[str, Any]] = []
    for opp in rows:
        if not isinstance(opp, dict):
            continue
        product_id = str(opp.get("product_id") or "").upper()
        if not product_id:
            continue
        radar_row = radar_by_product.get(product_id, {})
        min_notional = to_float(radar_row.get("min_notional_usd") or radar_row.get("cost_min"))
        buy_rate = rate_row(microfill, product_id, "buy", min_samples=min_offset_samples)
        sell_rate = rate_row(microfill, product_id, "sell", min_samples=min_offset_samples)
        buy_offsets = [
            offset_rate_row(microfill, product_id, "buy", offset, min_samples=min_offset_samples)
            for offset in DEFAULT_OFFSET_FRACS
        ]
        sell_offsets = [
            offset_rate_row(microfill, product_id, "sell", offset, min_samples=min_offset_samples)
            for offset in DEFAULT_OFFSET_FRACS
        ]
        buy_tickbacks = [
            tickback_rate_row(microfill, product_id, "buy", tick_back, min_samples=min_offset_samples)
            for tick_back in (0, 1, 2)
        ]
        sell_tickbacks = [
            tickback_rate_row(microfill, product_id, "sell", tick_back, min_samples=min_offset_samples)
            for tick_back in (0, 1, 2)
        ]
        options = [
            slice_option(
                quote_usd=quote_usd,
                slice_count=count,
                maker_fee_bps=maker_fee_bps,
                min_notional_usd=min_notional,
            )
            for count in counts
        ]
        by_count = {option["slice_count"]: option for option in options}
        base_microfill_ok = eligible_rate(buy_rate, min_entry_rate, min_samples=min_offset_samples) and eligible_rate(
            sell_rate, min_exit_rate, min_samples=min_offset_samples
        )
        buy_l1_ok = eligible_rate(buy_offsets[0], min_offset_rate, min_samples=min_offset_samples)
        sell_l1_ok = eligible_rate(sell_offsets[0], min_offset_rate, min_samples=min_offset_samples)
        tickback_buy_available = tickback_microfill_ok(
            buy_tickbacks, min_rate=min_offset_rate, min_samples=min_offset_samples
        )
        tickback_sell_available = tickback_microfill_ok(
            sell_tickbacks, min_rate=min_offset_rate, min_samples=min_offset_samples
        )
        option_2 = by_count.get(2, {})
        option_5 = by_count.get(5, {})
        l1_candidate = bool(
            option_2.get("min_notional_valid")
            and base_microfill_ok
            and buy_l1_ok
            and sell_l1_ok
        )
        true_microcloud_launchable = bool(
            option_5.get("min_notional_valid")
            and base_microfill_ok
            and buy_l1_ok
            and sell_l1_ok
            and tickback_buy_available
            and tickback_sell_available
        )
        blockers: list[str] = []
        if not option_2.get("min_notional_valid"):
            blockers.append("two_slice_min_notional_fails")
        if not option_5.get("min_notional_valid"):
            blockers.append("five_slice_min_notional_fails")
        if not base_microfill_ok:
            blockers.append("base_buy_sell_microfill_gate_fails")
        if not buy_l1_ok or not sell_l1_ok:
            blockers.append("l1_offset_microfill_gate_fails")
        if not tickback_buy_available or not tickback_sell_available:
            blockers.append("tickback_microfill_calibration_missing")

        product_rows.append(
            {
                "product_id": product_id,
                "playbook": str(opp.get("playbook") or ""),
                "mer": round(to_float(opp.get("mer")), 6),
                "machinegun_score": round(to_float(opp.get("machinegun_score")), 6),
                "opportunity_spread_bps": round(to_float(opp.get("spread_bps")), 6),
                "radar_present": bool(radar_row),
                "live_spread_bps": round(to_float(radar_row.get("spread_bps")), 6) if radar_row else 0.0,
                "min_notional_usd": round(min_notional, 6),
                "slice_options": options,
                "buy_microfill": buy_rate,
                "sell_microfill": sell_rate,
                "buy_offset_microfill": buy_offsets,
                "sell_offset_microfill": sell_offsets,
                "buy_tickback_microfill": buy_tickbacks,
                "sell_tickback_microfill": sell_tickbacks,
                "base_microfill_ok": base_microfill_ok,
                "l1_microfill_ok": bool(buy_l1_ok and sell_l1_ok),
                "tickback_calibration_available": bool(tickback_buy_available and tickback_sell_available),
                "telemetry_only_l1_two_slice_candidate": l1_candidate,
                "true_5x_microcloud_launchable": true_microcloud_launchable,
                "blockers": blockers,
            }
        )

    product_rows.sort(
        key=lambda row: (
            row["telemetry_only_l1_two_slice_candidate"],
            row["true_5x_microcloud_launchable"],
            row["machinegun_score"],
            row["mer"],
        ),
        reverse=True,
    )
    slice_valid_counts = {
        str(count): sum(1 for row in product_rows if next((opt for opt in row["slice_options"] if opt["slice_count"] == count), {}).get("min_notional_valid"))
        for count in counts
    }
    quote_scenarios = []
    for scenario_quote in scenario_quotes:
        quote_scenarios.append(
            {
                "quote_usd": round(scenario_quote, 6),
                "slice_valid_counts": {
                    str(count): sum(
                        1
                        for row in product_rows
                        if slice_option(
                            quote_usd=scenario_quote,
                            slice_count=count,
                            maker_fee_bps=maker_fee_bps,
                            min_notional_usd=to_float(row.get("min_notional_usd")),
                        )["min_notional_valid"]
                    )
                    for count in counts
                },
            }
        )
    five_slice_min_blockers = [
        row["product_id"]
        for row in product_rows
        if "five_slice_min_notional_fails" in row["blockers"]
    ]
    telemetry_candidates = [row["product_id"] for row in product_rows if row["telemetry_only_l1_two_slice_candidate"]]
    true_launchable = [row["product_id"] for row in product_rows if row["true_5x_microcloud_launchable"]]
    global_blockers: list[str] = []
    runner_behavior_supported = True
    if not product_rows:
        global_blockers.append("no_opportunity_rows")
    if not true_launchable:
        global_blockers.append("no_true_5x_microcloud_launchable_products")
    if any("tickback_microfill_calibration_missing" in row["blockers"] for row in product_rows):
        global_blockers.append("tickback_microfill_calibration_missing")
    if five_slice_min_blockers:
        global_blockers.append("five_slice_min_notional_fails_for_some_products")

    verdict = "blocked_for_true_microcloud_launch"
    if telemetry_candidates:
        verdict = "telemetry_only_l1_candidates_found"
    if true_launchable and runner_behavior_supported:
        verdict = "eligible_for_isolated_true_microcloud_ab"

    return {
        "generated_at": utc_now_iso(),
        "parameters": {
            "opportunity_path": str(opportunity_path),
            "radar_path": str(radar_path),
            "microfill_summary_path": str(microfill_summary_path),
            "quote_usd": round(quote_usd, 6),
            "quote_usd_scenarios": [round(value, 6) for value in scenario_quotes],
            "maker_fee_bps": round(maker_fee_bps, 6),
            "slice_counts": counts,
            "min_entry_rate": round(min_entry_rate, 6),
            "min_exit_rate": round(min_exit_rate, 6),
            "min_offset_rate": round(min_offset_rate, 6),
            "min_offset_samples": min_offset_samples,
        },
        "summary": {
            "verdict": verdict,
            "products_scanned": len(product_rows),
            "slice_valid_counts": slice_valid_counts,
            "quote_scenarios": quote_scenarios,
            "base_microfill_eligible_count": sum(1 for row in product_rows if row["base_microfill_ok"]),
            "l1_microfill_eligible_count": sum(1 for row in product_rows if row["l1_microfill_ok"]),
            "telemetry_only_l1_two_slice_candidates": len(telemetry_candidates),
            "true_5x_microcloud_launchable_count": len(true_launchable),
            "five_slice_min_notional_blockers": len(five_slice_min_blockers),
            "global_blockers": global_blockers,
        },
        "launch_contract": {
            "true_microcloud_ab_allowed": bool(true_launchable and runner_behavior_supported),
            "runner_microcloud_behavior_supported": runner_behavior_supported,
            "candidate_command_emitted": False,
            "candidate_command_not_emitted_reason": (
                "audit does not emit launch commands; collect tick-back calibration and use fresh isolated state/event/lock paths "
                "before manually launching any shadow-only micro-cloud branch"
            ),
            "next_code_slice": (
                "run the public calibrator with --price-tick-backs 0,1,2 on the L1 candidates, then rerun this "
                "eligibility contract before any isolated --enable-micro-cloud shadow launch"
            ),
        },
        "top_telemetry_only_l1_candidates": telemetry_candidates[:25],
        "top_true_5x_microcloud_launchable": true_launchable[:25],
        "rows": product_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    launch = payload.get("launch_contract", {})
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# Kraken Maker Micro-Cloud Eligibility",
        "",
        f"Generated: `{payload.get('generated_at', '')}`",
        "",
        "## Verdict",
        "",
        f"- Verdict: `{summary.get('verdict', '')}`",
        f"- Products scanned: `{summary.get('products_scanned', 0)}`",
        f"- Telemetry-only 2-slice L1 candidates: `{summary.get('telemetry_only_l1_two_slice_candidates', 0)}`",
        f"- True 5x micro-cloud launchable products: `{summary.get('true_5x_microcloud_launchable_count', 0)}`",
        f"- Candidate command emitted: `{launch.get('candidate_command_emitted', False)}`",
        f"- Command hold reason: {launch.get('candidate_command_not_emitted_reason', '')}",
        "",
        "## Blockers",
        "",
    ]
    blockers = summary.get("global_blockers") if isinstance(summary.get("global_blockers"), list) else []
    if blockers:
        lines.extend(f"- `{blocker}`" for blocker in blockers)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Slice Legality",
            "",
            "| Slice Count | Products Clearing Min Notional |",
            "|---:|---:|",
        ]
    )
    for count, valid in (summary.get("slice_valid_counts") or {}).items():
        lines.append(f"| {count} | {valid} |")
    lines.extend(
        [
            "",
            "## Quote Scenarios",
            "",
            "| Total Quote USD | 2 Slices Valid | 3 Slices Valid | 5 Slices Valid |",
            "|---:|---:|---:|---:|",
        ]
    )
    for scenario in summary.get("quote_scenarios") or []:
        counts = scenario.get("slice_valid_counts") or {}
        lines.append(
            "| {quote:.2f} | {two} | {three} | {five} |".format(
                quote=to_float(scenario.get("quote_usd")),
                two=counts.get("2", 0),
                three=counts.get("3", 0),
                five=counts.get("5", 0),
            )
        )
    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| Product | 2-Slice L1 Candidate | 5x Launchable | Min Notional | Buy Rate | Sell Rate | Blockers |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows[:30]:
        buy = row.get("buy_microfill") or {}
        sell = row.get("sell_microfill") or {}
        lines.append(
            "| {product} | {l1} | {launchable} | {min_notional:.6f} | {buy_rate} | {sell_rate} | {blockers} |".format(
                product=row.get("product_id", ""),
                l1="yes" if row.get("telemetry_only_l1_two_slice_candidate") else "no",
                launchable="yes" if row.get("true_5x_microcloud_launchable") else "no",
                min_notional=to_float(row.get("min_notional_usd")),
                buy_rate="" if buy.get("rate") is None else f"{to_float(buy.get('rate')):.3f}",
                sell_rate="" if sell.get("rate") is None else f"{to_float(sell.get('rate')):.3f}",
                blockers=", ".join(row.get("blockers") or []),
            )
        )
    lines.extend(
        [
            "",
            "## Next Code Slice",
            "",
            str(launch.get("next_code_slice") or ""),
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(payload: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Kraken maker micro-cloud eligibility contract.")
    parser.add_argument("--opportunity-path", default=str(DEFAULT_OPPORTUNITY_PATH))
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--microfill-summary-path", default=str(DEFAULT_MICROFILL_PATH))
    parser.add_argument("--quote-usd", type=float, default=25.0)
    parser.add_argument("--quote-usd-scenarios", default="10,25,50")
    parser.add_argument("--maker-fee-bps", type=float, default=25.0)
    parser.add_argument("--slice-counts", default="2,3,5")
    parser.add_argument("--min-entry-rate", type=float, default=0.10)
    parser.add_argument("--min-exit-rate", type=float, default=0.25)
    parser.add_argument("--min-offset-rate", type=float, default=0.10)
    parser.add_argument("--min-offset-samples", type=int, default=6)
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-out", default=str(DEFAULT_MD_PATH))
    args = parser.parse_args()

    payload = build_payload(
        opportunity_path=Path(args.opportunity_path),
        radar_path=Path(args.radar_path),
        microfill_summary_path=Path(args.microfill_summary_path),
        quote_usd=args.quote_usd,
        quote_usd_scenarios=parse_float_list(args.quote_usd_scenarios),
        maker_fee_bps=args.maker_fee_bps,
        slice_counts=parse_int_list(args.slice_counts),
        min_entry_rate=args.min_entry_rate,
        min_exit_rate=args.min_exit_rate,
        min_offset_rate=args.min_offset_rate,
        min_offset_samples=args.min_offset_samples,
    )
    write_reports(payload, Path(args.json_out), Path(args.md_out))
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
