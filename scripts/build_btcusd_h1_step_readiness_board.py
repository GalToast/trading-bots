#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_btcusd_h1_step_forward_review as forward_review


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
ROBUSTNESS_CSV = REPORTS / "live_btcusd_h1_step_robustness.csv"
FORWARD_REVIEW_CSV = REPORTS / "btcusd_h1_step_forward_review.csv"
JSON_PATH = REPORTS / "btcusd_h1_step_readiness_board.json"
MD_PATH = REPORTS / "btcusd_h1_step_readiness_board.md"

STATIC_LANE_META = {
    "shadow_btcusd_h1_step30": {"label": "shadow_step30", "step": 30.0, "role": "shadow_candidate"},
    "shadow_btcusd_h1_step50": {"label": "shadow_step50", "step": 50.0, "role": "shadow_candidate"},
}
STATE_PATHS = {
    "live_btcusd_exc2_tight_941779": REPORTS / "penetration_lattice_shadow_btcusd_exc2_tight_state.json",
    "shadow_btcusd_h1_step30": REPORTS / "penetration_lattice_shadow_btcusd_h1_step30_state.json",
    "shadow_btcusd_h1_step50": REPORTS / "penetration_lattice_shadow_btcusd_h1_step50_state.json",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_since(value: Any, *, now_dt: datetime) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return round(max(0.0, (now_dt - dt).total_seconds()) / 3600.0, 2)


def lane_meta(live_step: float | None = None) -> dict[str, dict[str, Any]]:
    active_live_step = forward_review.load_live_step() if live_step is None else float(live_step)
    return {
        "live_btcusd_exc2_tight_941779": {
            "label": f"live_step{forward_review.format_step(active_live_step)}",
            "step": active_live_step,
            "role": "live_baseline",
        },
        **STATIC_LANE_META,
    }


def quoted_label(label: str) -> str:
    return f"`{label}`" if label else "`live_reference`"


def load_forward_map(path: Path = FORWARD_REVIEW_CSV) -> dict[str, dict[str, Any]]:
    meta = lane_meta()
    rows: dict[str, dict[str, Any]] = {}
    for raw in load_csv_rows(path):
        lane_name = str(raw.get("lane_name") or "")
        if lane_name not in meta:
            continue
        rows[lane_name] = {
            "lane_name": lane_name,
            "label": str(raw.get("label") or lane_name),
            "step": to_float(raw.get("step")),
            "role": str(raw.get("role") or ""),
            "forward_status": str(raw.get("forward_status") or ""),
            "baseline_source": str(raw.get("baseline_source") or ""),
            "baseline_at": str(raw.get("baseline_at") or ""),
            "baseline_realized_usd": to_float(raw.get("baseline_realized_usd")),
            "realized_net_usd": to_float(raw.get("realized_net_usd")),
            "realized_delta_usd": to_float(raw.get("realized_delta_usd")),
            "baseline_closes": to_int(raw.get("baseline_closes")),
            "closes": to_int(raw.get("closes")),
            "new_closes": to_int(raw.get("new_closes")),
            "open_count": to_int(raw.get("open_count")),
            "floating_usd": to_float(raw.get("floating_usd")),
            "net_usd": to_float(raw.get("net_usd")),
            "updated_at": str(raw.get("updated_at") or ""),
            "forward_note": str(raw.get("forward_note") or ""),
        }
    return rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def classify_first_trade_state(*, open_count: int, realized_closes: int, anchor_resets: int) -> str:
    if open_count > 0 or realized_closes > 0:
        return "active_or_seeded"
    if anchor_resets > 0:
        return "flat_reanchoring"
    return "flat_waiting"


def first_trade_note(*, role: str, first_trade_state: str) -> str:
    if role == "live_baseline":
        return "live reference row; first-trade latency diagnostics do not apply"
    if first_trade_state == "active_or_seeded":
        return "candidate has started producing real lane activity"
    if first_trade_state == "flat_reanchoring":
        return "still flat; while the lane is flat, anchor resets move first-entry thresholds with the market, so forward proof remains low-signal until the first open"
    return "still flat and waiting for the first threshold breach"


def load_state_context() -> dict[str, dict[str, Any]]:
    now_dt = utc_now()
    rows: dict[str, dict[str, Any]] = {}
    for lane_name, path in STATE_PATHS.items():
        payload = load_json(path)
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        runner = payload.get("runner") if isinstance(payload, dict) else {}
        symbols = payload.get("symbols") if isinstance(payload, dict) else {}
        btc_row = symbols.get("BTCUSD") if isinstance(symbols, dict) else {}
        started_at = str((runner.get("started_at") if isinstance(runner, dict) else "") or "")
        runtime_age_hours = hours_since(started_at, now_dt=now_dt)
        open_count = len(list(btc_row.get("open_tickets") or [])) if isinstance(btc_row, dict) else 0
        realized_closes = to_int(btc_row.get("realized_closes")) if isinstance(btc_row, dict) else 0
        anchor_resets = to_int(btc_row.get("anchor_resets")) if isinstance(btc_row, dict) else 0
        rows[lane_name] = {
            "runtime_age_hours": runtime_age_hours,
            "anchor_resets": anchor_resets,
            "open_count_state": open_count,
            "realized_closes_state": realized_closes,
            "state_step": to_float(
                (metadata.get("step") if isinstance(metadata, dict) else None)
                or (btc_row.get("base_step_px") if isinstance(btc_row, dict) else None)
            ),
            "first_trade_state": classify_first_trade_state(
                open_count=open_count,
                realized_closes=realized_closes,
                anchor_resets=anchor_resets,
            ),
        }
    return rows


def load_robustness_summary(path: Path = ROBUSTNESS_CSV) -> dict[float, dict[str, Any]]:
    grouped: dict[float, list[dict[str, Any]]] = {}
    all_rows: list[dict[str, Any]] = []
    for raw in load_csv_rows(path):
        step = round(to_float(raw.get("step")), 6)
        row = {
            "step": step,
            "days": to_int(raw.get("days")),
            "marked_net_usd": to_float(raw.get("marked_net_usd")),
            "realized_net_usd": to_float(raw.get("realized_net_usd")),
            "marked_floating_usd": to_float(raw.get("marked_floating_usd")),
            "realized_closes": to_int(raw.get("realized_closes")),
            "open_count": to_int(raw.get("open_count")),
        }
        grouped.setdefault(step, []).append(row)
        all_rows.append(row)
    window_wins: dict[float, int] = {}
    by_days: dict[int, list[dict[str, Any]]] = {}
    for row in all_rows:
        by_days.setdefault(int(row["days"]), []).append(row)
    for day_rows in by_days.values():
        winner = max(day_rows, key=lambda row: (float(row["marked_net_usd"]), float(row["realized_net_usd"])))
        winner_step = round(float(winner["step"]), 6)
        window_wins[winner_step] = window_wins.get(winner_step, 0) + 1
    summary: dict[float, dict[str, Any]] = {}
    for step, rows in grouped.items():
        summary[step] = {
            "step": step,
            "window_wins": int(window_wins.get(step, 0)),
            "avg_marked_net_usd": round(sum(to_float(row["marked_net_usd"]) for row in rows) / max(len(rows), 1), 3),
            "avg_realized_net_usd": round(sum(to_float(row["realized_net_usd"]) for row in rows) / max(len(rows), 1), 3),
            "best_marked_net_usd": round(max(to_float(row["marked_net_usd"]) for row in rows), 3),
            "worst_marked_net_usd": round(min(to_float(row["marked_net_usd"]) for row in rows), 3),
            "window_count": len(rows),
        }
    ordered = sorted(
        summary.values(),
        key=lambda row: (
            int(row["window_wins"]),
            float(row["avg_marked_net_usd"]),
            float(row["avg_realized_net_usd"]),
            -float(row["step"]),
        ),
        reverse=True,
    )
    for rank, row in enumerate(ordered, start=1):
        summary[round(float(row["step"]), 6)]["replay_rank"] = rank
    return summary


def classify_candidate(
    *,
    role: str,
    replay_rank: int | None,
    forward_status: str,
    new_closes: int,
    realized_delta_usd: float,
    live_label: str,
) -> tuple[str, str]:
    if role == "live_baseline":
        return "live_reference", f"keep {quoted_label(live_label)} as the broker reference until a shadow candidate clears the forward bar"
    if new_closes < 5:
        if replay_rank == 1:
            return "top_replay_wait_forward", "strongest replay candidate, but forward evidence is still too thin"
        return "fallback_wait_forward", "candidate is replay-supported, but forward evidence is still too thin"
    if realized_delta_usd > 0 and forward_status.startswith("holding_up"):
        if replay_rank == 1 and new_closes >= 20:
            return "promotion_ready_review", "candidate is replay-led and now has a positive mature forward run"
        if replay_rank == 1 and new_closes >= 10:
            return "micro_probe_candidate", "candidate is replay-led with an early positive forward run, but not yet mature enough for a full promotion verdict"
        return "forward_positive_watch", "candidate is positive forward, but the proof window is still short"
    if realized_delta_usd < 0:
        return "forward_negative_hold", "candidate is underperforming its supervised baseline"
    return "forward_flat_hold", "candidate has enough closes for a verdict window, but has not separated from baseline"


def build_payload(
    forward_map: dict[str, dict[str, Any]] | None = None,
    robustness_summary: dict[float, dict[str, Any]] | None = None,
    state_context: dict[str, dict[str, Any]] | None = None,
    live_step: float | None = None,
) -> dict[str, Any]:
    meta = lane_meta(live_step)
    forward_map = forward_map if forward_map is not None else load_forward_map()
    robustness_summary = robustness_summary if robustness_summary is not None else load_robustness_summary()
    state_context = state_context if state_context is not None else load_state_context()
    now_dt = utc_now()
    rows: list[dict[str, Any]] = []
    for lane_name, lane_meta_row in meta.items():
        forward_row = dict(forward_map.get(lane_name) or {})
        step = round(float(lane_meta_row["step"]), 6)
        replay_row = dict(robustness_summary.get(step) or {})
        state_row = dict(state_context.get(lane_name) or {})
        readiness_state, recommendation = classify_candidate(
            role=str(lane_meta_row["role"]),
            replay_rank=(int(replay_row["replay_rank"]) if replay_row.get("replay_rank") else None),
            forward_status=str(forward_row.get("forward_status") or ""),
            new_closes=to_int(forward_row.get("new_closes")),
            realized_delta_usd=to_float(forward_row.get("realized_delta_usd")),
            live_label=str(meta["live_btcusd_exc2_tight_941779"]["label"]),
        )
        rows.append(
            {
                "lane_name": lane_name,
                "label": str(lane_meta_row["label"]),
                "step": float(lane_meta_row["step"]),
                "role": str(lane_meta_row["role"]),
                "replay_rank": to_int(replay_row.get("replay_rank")) or None,
                "replay_window_wins": to_int(replay_row.get("window_wins")),
                "replay_avg_marked_net_usd": to_float(replay_row.get("avg_marked_net_usd")),
                "replay_avg_realized_net_usd": to_float(replay_row.get("avg_realized_net_usd")),
                "replay_best_marked_net_usd": to_float(replay_row.get("best_marked_net_usd")),
                "replay_worst_marked_net_usd": to_float(replay_row.get("worst_marked_net_usd")),
                "forward_status": str(forward_row.get("forward_status") or ""),
                "baseline_source": str(forward_row.get("baseline_source") or ""),
                "baseline_at": str(forward_row.get("baseline_at") or ""),
                "baseline_age_hours": hours_since(forward_row.get("baseline_at"), now_dt=now_dt),
                "new_closes": to_int(forward_row.get("new_closes")),
                "forward_delta_usd": to_float(forward_row.get("realized_delta_usd")),
                "forward_realized_usd": to_float(forward_row.get("realized_net_usd")),
                "forward_open_count": to_int(forward_row.get("open_count")),
                "forward_floating_usd": to_float(forward_row.get("floating_usd")),
                "forward_net_usd": to_float(forward_row.get("net_usd")),
                "updated_at": str(forward_row.get("updated_at") or ""),
                "runtime_age_hours": state_row.get("runtime_age_hours"),
                "anchor_resets": to_int(state_row.get("anchor_resets")),
                "state_step": to_float(state_row.get("state_step")),
                "first_trade_state": str(state_row.get("first_trade_state") or ""),
                "readiness_state": readiness_state,
                "recommendation": recommendation,
                "first_trade_note": first_trade_note(
                    role=str(lane_meta_row["role"]),
                    first_trade_state=str(state_row.get("first_trade_state") or ""),
                ),
                "note": str(forward_row.get("forward_note") or ""),
            }
        )
    rows.sort(
        key=lambda row: (
            row["role"] != "live_baseline",
            row["replay_rank"] if row["replay_rank"] is not None else 999,
            -float(row["forward_delta_usd"]),
            float(row["step"]),
        )
    )
    candidates = [row for row in rows if row["role"] != "live_baseline"]
    watch_lead = min(
        candidates,
        key=lambda row: (
            row["replay_rank"] if row["replay_rank"] is not None else 999,
            -float(row["forward_delta_usd"]),
            float(row["step"]),
        ),
        default=None,
    )
    promotion_ready = [
        row
        for row in candidates
        if row["readiness_state"] in {"micro_probe_candidate", "promotion_ready_review"}
    ]
    leadership_read = ["No BTC H1 step candidate clears a live-promotion bar today."]
    if watch_lead is not None:
        leadership_read.append(
            f"Step {int(watch_lead['step'])} is still the top watch candidate: replay rank #{watch_lead['replay_rank'] or '-'} with {watch_lead['replay_window_wins']} multi-window wins, but only {watch_lead['new_closes']} new forward closes so far."
        )
    fallback = next((row for row in candidates if int(row["step"]) == 50), None)
    if fallback is not None:
        leadership_read.append(
            f"Step 50 remains the fallback comparison lane, with replay rank #{fallback['replay_rank'] or '-'} and current forward state `{fallback['forward_status'] or '-'}`."
        )
    if promotion_ready:
        leadership_read[0] = "At least one BTC H1 step candidate now clears the early forward bar for a live-promotion review."
    else:
        live_ref = meta["live_btcusd_exc2_tight_941779"]
        leadership_read.append(
            f"Keep {quoted_label(str(live_ref['label']))} as the broker reference while the shadow candidates accumulate enough closes for a real forward verdict."
        )
    return {
        "generated_at": utc_now_iso(),
        "promotion_bar": "Require replay support plus at least 5 new forward closes for any verdict; treat 10+ as early probe evidence and 20+ as mature promotion review.",
        "watch_lead": watch_lead,
        "rows": rows,
        "leadership_read": leadership_read,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path = JSON_PATH, md_path: Path = MD_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# BTCUSD H1 Step Readiness Board",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Promotion bar: {payload['promotion_bar']}",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload.get("leadership_read") or []:
        lines.append(f"- {line}")
    if payload.get("watch_lead"):
        watch_lead = payload["watch_lead"]
        lines.extend(
            [
                "",
                "## Watch Lead",
                "",
                f"- Step: `{int(watch_lead['step'])}`",
                f"- Lane: `{watch_lead['lane_name']}`",
                f"- Readiness: `{watch_lead['readiness_state']}`",
                f"- Recommendation: {watch_lead['recommendation']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Lane | Label | Step | Role | Replay Rank | Replay Wins | Avg Marked $ | Worst Marked $ | Forward Status | New Closes | Delta $ | Open | Floating $ | Net $ | Baseline Age Hrs | Runtime Age Hrs | Anchor Resets | First-Trade State | Readiness | Recommendation |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in payload.get("rows") or []:
        baseline_age = "-" if row["baseline_age_hours"] is None else f"{float(row['baseline_age_hours']):.2f}"
        runtime_age = "-" if row["runtime_age_hours"] is None else f"{float(row['runtime_age_hours']):.2f}"
        replay_rank = "-" if row["replay_rank"] is None else str(int(row["replay_rank"]))
        lines.append(
            f"| {row['lane_name']} | {row['label']} | {int(row['step'])} | {row['role']} | {replay_rank} | {int(row['replay_window_wins'])} | "
            f"{float(row['replay_avg_marked_net_usd']):.3f} | {float(row['replay_worst_marked_net_usd']):.3f} | {row['forward_status'] or '-'} | "
            f"{int(row['new_closes'])} | {float(row['forward_delta_usd']):.4f} | {int(row['forward_open_count'])} | "
            f"{float(row['forward_floating_usd']):.4f} | {float(row['forward_net_usd']):.4f} | {baseline_age} | {runtime_age} | {int(row['anchor_resets'])} | "
            f"{row['first_trade_state'] or '-'} | {row['readiness_state']} | {row['recommendation']} |"
        )
        if row.get("first_trade_note"):
            lines.append(f"|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | note |  | {row['first_trade_note']} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload(live_step=forward_review.load_live_step())
    write_reports(payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
