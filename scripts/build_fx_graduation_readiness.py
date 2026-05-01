#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MEMORY = ROOT / "memory"

LIVE_STATE_PATH = REPORTS / "penetration_lattice_live_source_state.json"
GBP_STATE_PATH = REPORTS / "shadow_gbpusd_tick_forward_state.json"
GBP_REPORT_PATH = REPORTS / "gbpusd_tick_forward_shadow.md"
EUR_REPORT_PATH = REPORTS / "eurusd_forward_shadow.md"
REALISM_REPORT_PATH = REPORTS / "fx_low_step_realism_audit.md"
FX_LIVE_ALPHA_AUDIT_JSON = REPORTS / "fx_live_alpha_recent_audit.json"
MIXED_CLOSE_POLICY_STATE_PATH = REPORTS / "penetration_lattice_shadow_fx_close_policy_mixed_state.json"
MIXED_SESSION_GATED_STATE_PATH = REPORTS / "penetration_lattice_shadow_fx_close_policy_mixed_session_gated_state.json"
SESSION_GATING_REPORT_PATH = REPORTS / "fx_session_gating_analysis.md"

JSON_PATH = REPORTS / "fx_graduation_readiness.json"
MD_PATH = REPORTS / "fx_graduation_readiness.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def extract_money(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except Exception:
        return None


def extract_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except Exception:
        return None


def pct_text(value: int | float | None, target: int | float | None) -> str:
    if value is None or target in {None, 0}:
        return "-"
    try:
        pct = (float(value) / float(target)) * 100.0
    except Exception:
        return "-"
    return f"{pct:.1f}%"


def load_session_gating_summary() -> dict[str, Any]:
    text = load_text(SESSION_GATING_REPORT_PATH)
    recovered = extract_money(text, r"Blocking off-session would have recovered \$([0-9.,]+) from")
    off_count = extract_int(text, r"Blocking off-session would have recovered \$[0-9.,]+ from ([0-9,]+) trades")
    good_total = extract_money(text, r"GOOD_SESSION: [0-9,]+ trades, WR=[0-9.]+%, avg=\$[+-]?[0-9.,]+, total=\$([+-]?[0-9.,]+)")
    off_total = extract_money(text, r"OFF_SESSION: [0-9,]+ trades, WR=[0-9.]+%, avg=\$[+-]?[0-9.,]+, total=\$([+-]?[0-9.,]+)")
    if recovered is None or off_count is None:
        return {}
    return {
        "recovered": recovered,
        "off_count": off_count,
        "good_total": good_total,
        "off_total": off_total,
    }


def fmt_money(value: Any, decimals: int = 2) -> str:
    try:
        return f"${float(value):+.{decimals}f}"
    except Exception:
        return "unknown"


def classify_runner_status(state: dict[str, Any]) -> str:
    runner = state.get("runner") if isinstance(state.get("runner"), dict) else {}
    heartbeat = str(runner.get("heartbeat_at") or "").strip()
    if heartbeat:
        return "running"
    return "unknown"


def build_live_row() -> dict[str, Any]:
    payload = load_json(LIVE_STATE_PATH)
    audit = load_json(FX_LIVE_ALPHA_AUDIT_JSON)
    session_gate = load_session_gating_summary()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    eur = symbols.get("EURUSD") if isinstance(symbols.get("EURUSD"), dict) else {}
    gbp = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}
    realized_total = to_float(eur.get("realized_net_usd")) + to_float(gbp.get("realized_net_usd"))
    closes_total = int(to_float(eur.get("realized_closes"))) + int(to_float(gbp.get("realized_closes")))
    open_total = len(eur.get("open_tickets") or []) + len(gbp.get("open_tickets") or [])
    runner_status = "running" if str(runner.get("heartbeat_at") or "").strip() else "unknown"
    alpha = to_float(metadata.get("raw_close_alpha"))
    cooldown = int(to_float(metadata.get("raw_rearm_cooldown_bars")))
    session_gate_enabled = bool(metadata.get("session_gate"))
    session_gated_now = bool(runner.get("session_gated"))
    gated_hour = runner.get("gated_hour")
    audit_summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    provisional = bool(audit_summary.get("revert_is_thin_sample"))
    prior_alpha = audit_summary.get("prior_window_alpha")
    prior_closes = int(to_float(audit_summary.get("prior_window_close_count")))
    prior_net = audit_summary.get("prior_window_close_net_usd")
    current_window_closes = int(to_float(audit_summary.get("current_window_close_count")))
    evidence = f"live config active; {closes_total} realized closes, ${realized_total:+.2f} realized, {open_total} open"
    next_gate = "monitor_live_only"
    recommendation = "Keep this as the current FX live reference. Do not conflate it with the unpromoted macro geometry winners."
    posture_suffix = ""
    if provisional:
        prior_alpha_text = f"{float(prior_alpha):.1f}" if prior_alpha is not None else "unknown"
        evidence += (
            f"; prior alpha={prior_alpha_text} window was only {prior_closes} closes / {fmt_money(prior_net)}, "
            f"current alpha={alpha:.1f} post-revert window has {current_window_closes} closes"
        )
        next_gate = str(audit_summary.get("next_gate") or "accumulate_post_revert_sample")
        recommendation = (
            "Keep this as the current live FX reference, but treat the alpha=0.5 revert as provisional "
            "until the post-revert window accumulates a real close sample."
        )
        posture_suffix = "; provisional_alpha_audit"
    if session_gate:
        evidence += (
            f"; session-gating audit says off-session FX cost about ${session_gate['recovered']:.2f} "
            f"across {session_gate['off_count']} trades"
        )
        recommendation += (
            " Also validate the session-gating fix path before arguing for new FX geometry or another 24h live hold."
        )
        posture_suffix += "; session_gate_recommended"
    if session_gate_enabled:
        posture_suffix += "; session_gate_enabled"
        if session_gated_now:
            hour_text = f"{int(gated_hour):02d}" if gated_hour is not None else "current"
            evidence += f"; lane is currently idling cleanly under session gate at {hour_text}:00 UTC"
            next_gate = "next_good_session_window"
            recommendation += (
                " Overnight bleed is now blocked; the next operator check is the first good-session window when the lane resumes live processing."
            )
            posture_suffix += f"; gated_now=yes; gated_hour={gated_hour}"
        else:
            evidence += "; session gate is enabled and the lane is in active good-session processing mode"
            posture_suffix += "; gated_now=no"
    return {
        "lane_name": "live_rearm_941777",
        "candidate": "live_rearm_941777 conservative package",
        "scope": "EURUSD + GBPUSD",
        "shape": f"raw_stateful_rearm alpha={alpha:.1f} cooldown={cooldown}",
        "evidence": evidence,
        "readiness": "live",
        "gate_status": "graduated_live",
        "progress_label": "graduated",
        "progress_value": closes_total,
        "progress_target": closes_total,
        "progress_pct": "100.0%",
        "lane_status": runner_status,
        "next_gate": next_gate,
        "recommendation": recommendation,
        "operator_posture": f"{runner_status}; {open_total} open; alpha={metadata.get('raw_close_alpha')}; cooldown={metadata.get('raw_rearm_cooldown_bars')}{posture_suffix}",
    }


def build_gbp_row() -> dict[str, Any]:
    state = load_json(GBP_STATE_PATH)
    report_text = load_text(GBP_REPORT_PATH)

    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    gbp = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}
    durable = state.get("durable_proof") if isinstance(state.get("durable_proof"), dict) else {}
    open_count = len(gbp.get("open_tickets") or [])
    current_closes = int(to_float(gbp.get("realized_closes")))
    current_realized = to_float(gbp.get("realized_net_usd"))
    floating = to_float(gbp.get("floating_net_usd"))
    marked_net = extract_money(report_text, r"\| Marked Net \(USD\) \| \$([+-]?[0-9.,]+) \|")
    durable_closes = int(to_float(durable.get("durable_realized_closes")))
    durable_realized = to_float(durable.get("durable_realized_net_usd"))
    progress_target = 20
    lane_status = classify_runner_status(state)

    if durable_closes > 0:
        evidence = (
            f"tick-forward lane still open with {open_count} SELL, current snapshot ${current_realized:+.2f}/{current_closes}c, "
            f"durable proof ledger records {durable_closes} tick-native closes for ${durable_realized:+.2f}"
        )
        if durable_realized > 0:
            readiness = "shadow_proof_positive"
            gate_status = "counting_clean_closes"
            next_gate = "accumulate_20_plus_clean_closes"
            if durable_closes >= progress_target:
                recommendation = "Forward proof is now past the minimum close-count gate. Keep shadowing and judge promotion on whether the positive net survives more live time, not on replay alone."
            else:
                recommendation = "Keep shadowing. This is the only proof-positive macro FX survivor, but the sample is still far too small for promotion."
        else:
            readiness = "shadow_net_negative"
            gate_status = "demoted_from_promotion_queue"
            next_gate = "closure_diagnosis_only"
            if durable_closes >= progress_target:
                recommendation = (
                    "Decision #6 (2026-04-16): demoted from promotion queue. 7,313 closes at -$1,933 net (-$0.26/close). "
                    "This lane is disabled in the registry and removed from the FX watchdog group. "
                    "Keep only as a closure-diagnosis reference paired against shadow_gbpusd_tick_forward_no_escape."
                )
            else:
                recommendation = (
                    "Do not call this proof-positive. The lane has durable closes but the forward net is still negative."
                )
        progress_label = f"{durable_closes}/{progress_target} durable closes"
    else:
        marked_text = f", marked ${marked_net:+.2f}" if marked_net is not None else ""
        evidence = f"tick-forward lane open with {open_count} SELL, current snapshot ${current_realized:+.2f}/{current_closes}c{marked_text}"
        readiness = "shadow_collecting"
        gate_status = "waiting_first_clean_closes"
        progress_label = f"{durable_closes}/{progress_target} durable closes"
        next_gate = "first_clean_forward_closes"
        recommendation = "Keep shadowing until the lane produces durable closes; no promotion claim yet."

    marked_for_posture = marked_net if marked_net is not None else floating
    return {
        "lane_name": "shadow_gbpusd_tick_forward",
        "candidate": "GBPUSD macro geometry winner",
        "scope": "GBPUSD",
        "shape": "step 0.5/1.0 gap 1/3 allprof-alpha50 style",
        "evidence": evidence,
        "readiness": readiness,
        "gate_status": gate_status,
        "progress_label": progress_label,
        "progress_value": durable_closes,
        "progress_target": progress_target,
        "progress_pct": pct_text(durable_closes, progress_target),
        "lane_status": lane_status,
        "next_gate": next_gate,
        "recommendation": recommendation,
        "operator_posture": f"{lane_status}; {open_count} open SELL; marked ${marked_for_posture:+.2f}; snapshot={current_closes}c durable={durable_closes}c",
    }


def build_eur_row() -> dict[str, Any]:
    text = load_text(EUR_REPORT_PATH)
    net = extract_money(text, r"\| Combined Net \| \$([+-]?[0-9.,]+) \|")
    closes = extract_int(text, r"\| Closes \| ([0-9,]+) \|")
    return {
        "lane_name": "",
        "candidate": "EURUSD macro geometry winner",
        "scope": "EURUSD",
        "shape": "step 1.0/1.0 gap 3/3 outer",
        "evidence": f"7d forward-shadow failed at ${net:+.2f} over {closes} closes" if net is not None and closes is not None else "forward-shadow failed",
        "readiness": "rejected_current_regime",
        "gate_status": "rejected_forward",
        "progress_label": "failed",
        "progress_value": closes or 0,
        "progress_target": closes or 0,
        "progress_pct": "-",
        "lane_status": "not_running",
        "next_gate": "none",
        "recommendation": "Do not promote. Treat the 60d replay lead as regime-dependent until a materially different EURUSD path re-qualifies.",
        "operator_posture": "not in promotion queue on the current regime",
    }


def build_nzd_row() -> dict[str, Any]:
    text = load_text(REALISM_REPORT_PATH)
    line_match = re.search(
        r"`NZDUSD` modeled-live winner `sell=0\.25/buy=0\.5` -> `\$([+-]?[0-9.]+)` .* delta `\$([+-]?[0-9.]+)`",
        text,
    )
    if line_match:
        winner_net = float(line_match.group(1))
        delta = float(line_match.group(2))
        evidence = f"first realism gate failed: modeled-live net ${winner_net:+.2f}, delta ${delta:+.2f} vs prior reference"
    else:
        evidence = "first realism gate failed under broker_touch + bar_close"
    return {
        "lane_name": "",
        "candidate": "NZDUSD low-step retune",
        "scope": "NZDUSD",
        "shape": "step 0.25/0.5 candidate from asymmetric sweep",
        "evidence": evidence,
        "readiness": "rejected_realism",
        "gate_status": "rejected_realism",
        "progress_label": "failed",
        "progress_value": 0,
        "progress_target": 0,
        "progress_pct": "-",
        "lane_status": "not_running",
        "next_gate": "none",
        "recommendation": "Keep NZDUSD out of the near-promotion queue until a different shape survives realism first.",
        "operator_posture": "blocked before forward proof",
    }


def build_close_policy_row() -> dict[str, Any]:
    state = load_json(MIXED_CLOSE_POLICY_STATE_PATH)
    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    eur = symbols.get("EURUSD") if isinstance(symbols.get("EURUSD"), dict) else {}
    gbp = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}
    runner_status = classify_runner_status(state)
    if eur or gbp:
        closes_total = int(to_float(eur.get("realized_closes"))) + int(to_float(gbp.get("realized_closes")))
        realized_total = to_float(eur.get("realized_net_usd")) + to_float(gbp.get("realized_net_usd"))
        open_total = len(eur.get("open_tickets") or []) + len(gbp.get("open_tickets") or [])
        progress_target = 20
        if closes_total >= progress_target and realized_total <= 0:
            readiness = "shadow_net_negative"
            gate_status = "net_negative_forward_sample"
            next_gate = "prefer_session_gated_variant_or_new_shape"
            recommendation = (
                "Do not promote the ungated mixed lane. It already has a large forward sample and a negative realized "
                "ledger, so treat it as a failed-forward baseline and compare against the session-gated variant instead."
            )
        else:
            readiness = "shadow_collecting"
            gate_status = "counting_clean_closes" if closes_total > 0 else "waiting_first_clean_closes"
            next_gate = "accumulate_20_plus_clean_closes" if closes_total > 0 else "first_clean_forward_closes"
            recommendation = "Keep the mixed close-policy lane in shadow until it builds a real forward sample. Do not promote from offline evidence alone."
        return {
            "lane_name": "shadow_fx_close_policy_mixed",
            "candidate": "symbol-specific close-policy map",
            "scope": "EURUSD + GBPUSD",
            "shape": "EUR outer_gap2_alpha50, GBP allprof_gap1_alpha50",
            "evidence": f"mixed-policy shadow lane active; {closes_total} closes, ${realized_total:+.2f} realized, {open_total} open",
            "readiness": readiness,
            "gate_status": gate_status,
            "progress_label": f"{closes_total}/{progress_target} forward closes",
            "progress_value": closes_total,
            "progress_target": progress_target,
            "progress_pct": pct_text(closes_total, progress_target),
            "lane_status": runner_status,
            "next_gate": next_gate,
            "recommendation": recommendation,
            "operator_posture": f"{runner_status}; {open_total} open; closes={closes_total}; mixed EUR/GBP close-policy proof",
        }
    return {
        "lane_name": "shadow_fx_close_policy_mixed",
        "candidate": "symbol-specific close-policy map",
        "scope": "EURUSD + GBPUSD",
        "shape": "EUR outer_gap2_alpha50, GBP allprof_gap1_alpha50",
        "evidence": "validated offline and spread-aware; shadow launch path now exists via the mixed close-policy override file",
        "readiness": "shadow_launch_ready",
        "gate_status": "ready_for_shadow_launch",
        "progress_label": "launch_ready",
        "progress_value": 0,
        "progress_target": 1,
        "progress_pct": "0.0%",
        "lane_status": "not_running",
        "next_gate": "launch_supervised_shadow_lane",
        "recommendation": "Launch the mixed close-policy lane in shadow and require forward evidence before any live argument.",
        "operator_posture": "shadow launch path ready; waiting supervised proof",
    }


def build_close_policy_session_gated_row() -> dict[str, Any]:
    state = load_json(MIXED_SESSION_GATED_STATE_PATH)
    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    eur = symbols.get("EURUSD") if isinstance(symbols.get("EURUSD"), dict) else {}
    gbp = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}
    runner = state.get("runner") if isinstance(state.get("runner"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    runner_status = classify_runner_status(state)
    closes_total = int(to_float(eur.get("realized_closes"))) + int(to_float(gbp.get("realized_closes")))
    realized_total = to_float(eur.get("realized_net_usd")) + to_float(gbp.get("realized_net_usd"))
    open_total = len(eur.get("open_tickets") or []) + len(gbp.get("open_tickets") or [])
    progress_target = 20
    session_gate_enabled = bool(metadata.get("session_gate"))
    session_gated_now = bool(runner.get("session_gated"))
    gated_hour = runner.get("gated_hour")

    if eur or gbp or runner_status == "running":
        if session_gated_now:
            hour_text = f"{int(gated_hour):02d}" if gated_hour is not None else "current"
            evidence = (
                f"session-gated mixed-policy lane armed; off-session hour {hour_text}:00 UTC is being skipped cleanly, "
                f"{closes_total} closes, ${realized_total:+.2f} realized, {open_total} open"
            )
            gate_status = "waiting_good_session_window"
            progress_label = "armed for next good session"
            next_gate = "first_good_session_ticks"
            recommendation = (
                "Keep the gated proof lane running through off-session. Use the first good-session window to verify "
                "that session gating prevents overnight bleed without breaking the mixed close-policy lane."
            )
            posture = (
                f"{runner_status}; session_gate=on; gated_now=yes; gated_hour={gated_hour}; "
                f"{open_total} open; closes={closes_total}"
            )
        else:
            evidence = (
                f"session-gated mixed-policy lane active in good-session processing mode; {closes_total} closes, "
                f"${realized_total:+.2f} realized, {open_total} open"
            )
            gate_status = "counting_clean_closes" if closes_total > 0 else "waiting_first_clean_closes"
            progress_label = f"{closes_total}/{progress_target} forward closes"
            next_gate = "accumulate_20_plus_clean_closes" if closes_total > 0 else "first_clean_forward_closes"
            recommendation = (
                "Keep the gated proof lane running and compare it against the non-gated mixed lane before carrying "
                "session gating into the live FX reference."
            )
            posture = (
                f"{runner_status}; session_gate={'on' if session_gate_enabled else 'off'}; gated_now=no; "
                f"{open_total} open; closes={closes_total}"
            )
        return {
            "lane_name": "shadow_fx_close_policy_mixed_session_gated",
            "candidate": "symbol-specific close-policy map + session gate",
            "scope": "EURUSD + GBPUSD",
            "shape": "EUR outer_gap2_alpha50, GBP allprof_gap1_alpha50, session-gated",
            "evidence": evidence,
            "readiness": "shadow_collecting",
            "gate_status": gate_status,
            "progress_label": progress_label,
            "progress_value": closes_total,
            "progress_target": progress_target,
            "progress_pct": pct_text(closes_total, progress_target),
            "lane_status": runner_status,
            "next_gate": next_gate,
            "recommendation": recommendation,
            "operator_posture": posture,
        }
    return {
        "lane_name": "shadow_fx_close_policy_mixed_session_gated",
        "candidate": "symbol-specific close-policy map + session gate",
        "scope": "EURUSD + GBPUSD",
        "shape": "EUR outer_gap2_alpha50, GBP allprof_gap1_alpha50, session-gated",
        "evidence": "session-gated proof lane is registered but not running yet",
        "readiness": "shadow_launch_ready",
        "gate_status": "ready_for_shadow_launch",
        "progress_label": "launch_ready",
        "progress_value": 0,
        "progress_target": 1,
        "progress_pct": "0.0%",
        "lane_status": "not_running",
        "next_gate": "launch_supervised_shadow_lane",
        "recommendation": "Launch the session-gated proof lane and verify that it idles cleanly off-session before reading any live value into the gate.",
        "operator_posture": "shadow launch path ready; session-gated proof not started",
    }


def build_payload() -> dict[str, Any]:
    gbp_row = build_gbp_row()
    close_policy_row = build_close_policy_row()
    close_policy_session_row = build_close_policy_session_gated_row()
    session_gate = load_session_gating_summary()
    rows = [
        build_live_row(),
        gbp_row,
        build_eur_row(),
        build_nzd_row(),
        close_policy_row,
        close_policy_session_row,
    ]
    current_read = [
        "The conservative EURUSD + GBPUSD rearm package is the only FX truth that has genuinely graduated to live.",
        "EURUSD 1.0/1.0 gap 3/3 is forward-failed and NZDUSD low-step retunes are realism-failed.",
    ]
    if gbp_row["readiness"] == "shadow_proof_positive":
        current_read.insert(
            1,
            "GBPUSD 0.5/1.0 gap 1/3 is the only macro FX survivor still alive in the queue, but it is shadow-only and still needs a larger positive forward sample before promotion.",
        )
    elif gbp_row["readiness"] == "shadow_net_negative":
        current_read.insert(
            1,
            "GBPUSD 0.5/1.0 gap 1/3 is no longer an honest proof-positive survivor: the durable forward ledger is materially sampled and net-negative, so it belongs in closure-diagnosis / demotion discussion rather than promotion language.",
        )
    else:
        current_read.insert(
            1,
            "GBPUSD 0.5/1.0 gap 1/3 remains the supervised macro FX proof lane, but it still needs its first durable positive proof before promotion language is honest.",
        )
    if close_policy_row["readiness"] == "shadow_net_negative":
        current_read.append(
            "The ungated mixed EUR/GBP close-policy lane is also net-negative on a large forward sample, so it should be treated as a failed-forward baseline while the session-gated variant becomes the cleaner execution-fix harness."
        )
    else:
        current_read.append(
            "The mixed EUR/GBP close-policy package is no longer blocked by missing plumbing; it should be judged on forward proof, not offline ladders alone."
        )
    if session_gate:
        good_total = session_gate.get("good_total")
        off_total = session_gate.get("off_total")
        good_text = f"${good_total:+.2f}" if isinstance(good_total, (int, float)) else "near-flat"
        off_text = f"${off_total:+.2f}" if isinstance(off_total, (int, float)) else f"-${session_gate['recovered']:.2f}"
        current_read.append(
            "FX session gating is now the clearest execution fix to validate next: "
            f"the current audit shows good-session FX was {good_text} while off-session FX was {off_text}, "
            f"so blocking overnight rearm is a higher-confidence lever than inventing another geometry retune."
        )
        current_read.append(
            "The session-gated mixed close-policy proof lane is now the clean shadow harness for that claim: "
            "it should idle through off-session, then begin comparable forward proof once the good-session window opens."
        )
    live_state = load_json(LIVE_STATE_PATH)
    live_runner = live_state.get("runner") if isinstance(live_state.get("runner"), dict) else {}
    live_meta = live_state.get("metadata") if isinstance(live_state.get("metadata"), dict) else {}
    if bool(live_meta.get("session_gate")):
        if bool(live_runner.get("session_gated")):
            gated_hour = live_runner.get("gated_hour")
            hour_text = f"{int(gated_hour):02d}" if gated_hour is not None else "current"
            current_read.append(
                f"The live FX reference lane is already restarted with session gating and is idling cleanly at {hour_text}:00 UTC; "
                "the next live check is the first good-session window, not more overnight churn."
            )
        else:
            current_read.append(
                "The live FX reference lane now has session gating enabled and is processing inside the allowed session window."
            )
    lead_priority = {
        "shadow_proof_positive": 1,
        "shadow_collecting": 2,
        "shadow_launch_ready": 3,
    }
    lead = min(
        (row for row in rows if row["readiness"] in lead_priority),
        key=lambda row: lead_priority.get(str(row.get("readiness") or ""), 99),
        default=rows[0],
    )
    return {
        "generated_at": utc_now_iso(),
        "summary": {
            "live_rows": sum(1 for row in rows if row["readiness"] == "live"),
            "shadow_candidate_rows": sum(1 for row in rows if row["readiness"].startswith("shadow_")),
            "rejected_rows": sum(1 for row in rows if row["readiness"].startswith("rejected_")),
            "blocked_rows": sum(1 for row in rows if row["readiness"] == "offline_validated_hold"),
        },
        "current_read": current_read,
        "watch_lead": {
            "candidate": lead["candidate"],
            "readiness": lead["readiness"],
            "progress_label": lead.get("progress_label", ""),
            "progress_pct": lead.get("progress_pct", ""),
            "operator_posture": lead.get("operator_posture", ""),
            "recommendation": lead["recommendation"],
        },
        "rows": rows,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# FX Graduation Readiness",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        "- Promotion bar: keep the current live conservative package as the broker reference; no macro FX retune is promotable until it clears realism plus forward proof.",
        (
            "- Board counts: "
            f"`live={payload.get('summary', {}).get('live_rows', 0)}` "
            f"`shadow={payload.get('summary', {}).get('shadow_candidate_rows', 0)}` "
            f"`rejected={payload.get('summary', {}).get('rejected_rows', 0)}` "
            f"`blocked={payload.get('summary', {}).get('blocked_rows', 0)}`"
        ),
        "",
        "## Current Read",
        "",
    ]
    for line in payload.get("current_read", []):
        lines.append(f"- {line}")
    watch = payload.get("watch_lead") if isinstance(payload.get("watch_lead"), dict) else {}
    lines.extend(
        [
            "",
            "## Watch Lead",
            "",
            f"- Candidate: `{watch.get('candidate', '')}`",
            f"- Readiness: `{watch.get('readiness', '')}`",
            f"- Progress: `{watch.get('progress_label', '')}` (`{watch.get('progress_pct', '')}`)",
            f"- Posture: {watch.get('operator_posture', '')}",
            f"- Recommendation: {watch.get('recommendation', '')}",
            "",
            "## Rows",
            "",
            "| Candidate | Scope | Shape | Evidence | Readiness | Gate Status | Progress | Lane | Next Gate | Posture | Recommendation |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload.get("rows", []):
        lines.append(
            f"| {row['candidate']} | {row['scope']} | {row['shape']} | {row['evidence']} | "
            f"{row['readiness']} | {row['gate_status']} | {row['progress_label']} ({row['progress_pct']}) | "
            f"{row['lane_status']} | {row['next_gate']} | {row['operator_posture']} | {row['recommendation']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(
        json.dumps(
            {
                "json_path": str(JSON_PATH),
                "md_path": str(MD_PATH),
                "rows": len(payload.get("rows", [])),
                "watch_lead": payload.get("watch_lead", {}),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
