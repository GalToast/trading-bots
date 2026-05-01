#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_MD = ROOT / "reports" / "kraken_maker_reality_cap_audit.md"
DEFAULT_REPORT_JSON = ROOT / "reports" / "kraken_maker_reality_cap_audit.json"


DEFAULT_LANES = {
    "fast_cooldown": ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl",
    "dds25_fixed": ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl",
    "dds50_fastbank": ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds50_fastbank_ab_events.jsonl",
    "microfill_entrygate010": ROOT / "reports" / "kraken_spot_maker_machinegun_microfill_exitgate025_spread25_entrygate010_ab_events.jsonl",
    "exitbreak_adverse_offset": ROOT / "reports" / "kraken_spot_maker_machinegun_microfill_exitgate025_spread25_entrygate010_exitbreak_adverse_offset_ab_events.jsonl",
}


def discover_kraken_maker_event_lanes() -> dict[str, Path]:
    lanes: dict[str, Path] = {}
    for path in sorted((ROOT / "reports").glob("kraken*events.jsonl")):
        lanes[path.stem.removesuffix("_events")] = path
    return lanes


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


@dataclass
class LaneAudit:
    lane: str
    events_path: Path
    closes: int
    booked_wins: int
    booked_losses: int
    realized_net_usd: float
    cap_breach_closes: int
    adjusted_wins: int
    adjusted_losses: int
    adjusted_net_usd: float
    cap_breach_adjusted_loss_usd: float
    missing_mae_closes: int
    maker_dependent_wins: int
    worst_min_net_pct: float | None
    worst_bid_taker_net_pct: float | None
    sample_breaches: list[dict[str, Any]]

    @property
    def booked_win_rate(self) -> float:
        return self.booked_wins / self.closes if self.closes else 0.0

    @property
    def adjusted_win_rate(self) -> float:
        total = self.adjusted_wins + self.adjusted_losses
        return self.adjusted_wins / total if total else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "events_path": str(self.events_path),
            "closes": self.closes,
            "booked_wins": self.booked_wins,
            "booked_losses": self.booked_losses,
            "booked_win_rate": round(self.booked_win_rate, 6),
            "realized_net_usd": round(self.realized_net_usd, 6),
            "cap_breach_closes": self.cap_breach_closes,
            "adjusted_wins": self.adjusted_wins,
            "adjusted_losses": self.adjusted_losses,
            "adjusted_win_rate": round(self.adjusted_win_rate, 6),
            "adjusted_net_usd": round(self.adjusted_net_usd, 6),
            "cap_breach_adjusted_loss_usd": round(self.cap_breach_adjusted_loss_usd, 6),
            "missing_mae_closes": self.missing_mae_closes,
            "maker_dependent_wins": self.maker_dependent_wins,
            "worst_min_net_pct": self.worst_min_net_pct,
            "worst_bid_taker_net_pct": self.worst_bid_taker_net_pct,
            "sample_breaches": self.sample_breaches,
        }


def close_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("action") == "close_maker_shadow"]


def cap_stop_net_usd(row: dict[str, Any], *, cap_pct: float, fallback_net: float) -> float:
    cost_usd = to_float(row.get("cost_usd"))
    if cost_usd > 0.0:
        return -abs(cap_pct) * 0.01 * cost_usd
    return min(fallback_net, 0.0)


def audit_lane(lane: str, path: Path, *, cap_pct: float) -> LaneAudit:
    closes = close_rows(load_jsonl(path))
    booked_wins = 0
    booked_losses = 0
    realized_net_usd = 0.0
    cap_breach_closes = 0
    adjusted_wins = 0
    adjusted_losses = 0
    adjusted_net_usd = 0.0
    cap_breach_adjusted_loss_usd = 0.0
    missing_mae_closes = 0
    maker_dependent_wins = 0
    worst_min_net_pct: float | None = None
    worst_bid_taker_net_pct: float | None = None
    sample_breaches: list[dict[str, Any]] = []

    for row in closes:
        net = to_float(row.get("net"))
        net_pct = to_float(row.get("net_pct"))
        realized_net_usd += net
        booked_win = net > 0.0
        if booked_win:
            booked_wins += 1
        else:
            booked_losses += 1

        min_net_raw = row.get("min_net_pct_on_cost")
        min_net_pct = None if min_net_raw is None else to_float(min_net_raw)
        if min_net_pct is None:
            missing_mae_closes += 1
        else:
            worst_min_net_pct = min(min_net_pct, worst_min_net_pct) if worst_min_net_pct is not None else min_net_pct
        cap_breach = min_net_pct is not None and min_net_pct <= -abs(cap_pct)
        if cap_breach:
            cap_breach_closes += 1
            stop_net = cap_stop_net_usd(row, cap_pct=cap_pct, fallback_net=net)
            cap_breach_adjusted_loss_usd += stop_net
            if len(sample_breaches) < 8:
                sample_breaches.append(
                    {
                        "ts_utc": row.get("ts_utc"),
                        "product_id": row.get("product_id"),
                        "cost_usd": round(to_float(row.get("cost_usd")), 6),
                        "net": round(net, 6),
                        "net_pct": round(net_pct, 4),
                        "adjusted_stop_net": round(stop_net, 6),
                        "min_net_pct_on_cost": round(min_net_pct, 4),
                        "reason": row.get("reason"),
                    }
                )

        bid_taker_raw = row.get("bid_taker_net_pct")
        bid_taker_net_pct = None if bid_taker_raw is None else to_float(bid_taker_raw)
        if bid_taker_net_pct is not None:
            worst_bid_taker_net_pct = (
                min(bid_taker_net_pct, worst_bid_taker_net_pct)
                if worst_bid_taker_net_pct is not None
                else bid_taker_net_pct
            )
            if booked_win and bid_taker_net_pct < 0.0:
                maker_dependent_wins += 1

        adjusted_win = booked_win and not cap_breach
        if adjusted_win:
            adjusted_wins += 1
            adjusted_net_usd += net
        elif cap_breach:
            adjusted_losses += 1
            adjusted_net_usd += cap_stop_net_usd(row, cap_pct=cap_pct, fallback_net=net)
        else:
            adjusted_losses += 1
            adjusted_net_usd += min(net, 0.0)

    return LaneAudit(
        lane=lane,
        events_path=path,
        closes=len(closes),
        booked_wins=booked_wins,
        booked_losses=booked_losses,
        realized_net_usd=realized_net_usd,
        cap_breach_closes=cap_breach_closes,
        adjusted_wins=adjusted_wins,
        adjusted_losses=adjusted_losses,
        adjusted_net_usd=adjusted_net_usd,
        cap_breach_adjusted_loss_usd=cap_breach_adjusted_loss_usd,
        missing_mae_closes=missing_mae_closes,
        maker_dependent_wins=maker_dependent_wins,
        worst_min_net_pct=round(worst_min_net_pct, 4) if worst_min_net_pct is not None else None,
        worst_bid_taker_net_pct=round(worst_bid_taker_net_pct, 4) if worst_bid_taker_net_pct is not None else None,
        sample_breaches=sample_breaches,
    )


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Kraken Maker Reality-Cap Audit",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Cap: `{payload['cap_pct']}%` adverse excursion",
        "- Rule: any close with `min_net_pct_on_cost <= -cap` is charged as a stop-sized adjusted loss on `cost_usd`.",
        "- Caveat: this only audits closes that carry `min_net_pct_on_cost`; older missing rows are counted separately.",
        "",
        "| lane | closes | booked W/L | booked WR | cap breaches | adjusted W/L | adjusted WR | booked net | adjusted net | cap-stop net | maker-dependent wins | worst MAE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lane in payload["lanes"]:
        lines.append(
            "| {lane} | {closes} | {bw}/{bl} | {bwr:.1%} | {breach} | {aw}/{al} | {awr:.1%} | ${net:.6f} | ${adj:.6f} | ${caploss:.6f} | {mdw} | {worst} |".format(
                lane=lane["lane"],
                closes=lane["closes"],
                bw=lane["booked_wins"],
                bl=lane["booked_losses"],
                bwr=lane["booked_win_rate"],
                breach=lane["cap_breach_closes"],
                aw=lane["adjusted_wins"],
                al=lane["adjusted_losses"],
                awr=lane["adjusted_win_rate"],
                net=lane["realized_net_usd"],
                adj=lane["adjusted_net_usd"],
                caploss=lane["cap_breach_adjusted_loss_usd"],
                mdw=lane["maker_dependent_wins"],
                worst=lane["worst_min_net_pct"],
            )
        )
    lines.extend(["", "## Breach Samples", ""])
    for lane in payload["lanes"]:
        if not lane["sample_breaches"]:
            continue
        lines.append(f"### {lane['lane']}")
        for row in lane["sample_breaches"]:
            lines.append(
                f"- `{row.get('product_id')}` `{row.get('ts_utc')}` net `{row.get('net_pct')}%`, MAE `{row.get('min_net_pct_on_cost')}%`, adjusted stop `{row.get('adjusted_stop_net')}`, reason `{row.get('reason')}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_payload(lanes: dict[str, Path], *, cap_pct: float) -> dict[str, Any]:
    audits = [audit_lane(lane, path, cap_pct=cap_pct) for lane, path in lanes.items()]
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_reality_cap_audit",
        "cap_pct": cap_pct,
        "lanes": [audit.to_json() for audit in audits],
        "summary": {
            "lanes": len(audits),
            "total_closes": sum(a.closes for a in audits),
            "total_booked_net_usd": round(sum(a.realized_net_usd for a in audits), 6),
            "total_adjusted_net_usd": round(sum(a.adjusted_net_usd for a in audits), 6),
            "total_cap_breaches": sum(a.cap_breach_closes for a in audits),
            "total_cap_breach_adjusted_loss_usd": round(sum(a.cap_breach_adjusted_loss_usd for a in audits), 6),
            "total_maker_dependent_wins": sum(a.maker_dependent_wins for a in audits),
        },
    }


def parse_lane_args(values: list[str]) -> dict[str, Path]:
    if not values:
        return DEFAULT_LANES
    lanes: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid lane spec {value!r}; expected name=path")
        name, path = value.split("=", 1)
        lanes[name.strip()] = Path(path.strip())
    return lanes


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Kraken maker close tapes under a hard MAE reality cap.")
    parser.add_argument("--cap-pct", type=float, default=3.0)
    parser.add_argument("--lane", action="append", default=[], help="Lane spec name=events_path. Defaults to key Kraken maker lanes.")
    parser.add_argument("--all-kraken-events", action="store_true", help="Audit every reports/kraken*events.jsonl close tape instead of the curated default lanes.")
    parser.add_argument("--output-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_REPORT_JSON)
    args = parser.parse_args()

    lanes = discover_kraken_maker_event_lanes() if args.all_kraken_events else parse_lane_args(args.lane)
    payload = build_payload(lanes, cap_pct=abs(float(args.cap_pct)))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
