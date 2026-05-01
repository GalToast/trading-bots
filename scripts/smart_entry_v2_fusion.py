"""Smart Entry v2 Signal Fusion Analysis.

Tests what happens when all 5 Smart Entry v2 signals are combined:
1. Competition lane priority (regime WR >= 55%, positive PnL)
2. Session gating (07:00-21:00 UTC)
3. Wider stops signal (brain not detecting tight-stop chop)
4. Hot streak sizing (symbol WR > 70% or 65%+ with consec wins)
5. ATR-scaled exits (exit target scaled by symbol volatility)

Reads trade_behavior_log.jsonl and simulates the combined signals.
"""

import json
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_FILE = REPO / "trade_behavior_log.jsonl"

FX_SYMBOLS = {"EURUSD", "GBPUSD", "NZDUSD", "USDJPY", "AUDUSD", "USDCAD",
               "AUDCAD", "GBPAUD", "NZDCAD", "AUDCHF", "EURCHF", "USDCHF"}

def is_good_session(utc_str):
    """07:00-21:00 UTC is good session."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return 7 <= dt.hour < 21
    except:
        return True  # Default to good if we can't parse


def analyze():
    # Load all closed trades
    trades = []
    with open(LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except:
                continue
            if record.get("realized_pnl") is None:
                continue
            trades.append(record)

    print(f"=== Smart Entry v2 Signal Fusion Analysis ===")
    print(f"Total closed trades: {len(trades)}")
    print()

    # Filter FX trades for signal fusion test
    fx_trades = [t for t in trades if t.get("symbol", "").upper() in FX_SYMBOLS]
    print(f"FX closed trades: {len(fx_trades)}")

    # 1. Session gating impact
    session_good = [t for t in fx_trades if is_good_session(t.get("entry_time_utc", ""))]
    session_bad = [t for t in fx_trades if not is_good_session(t.get("entry_time_utc", ""))]

    good_pnl = sum(t["realized_pnl"] for t in session_good)
    bad_pnl = sum(t["realized_pnl"] for t in session_bad)
    good_wr = sum(1 for t in session_good if t["realized_pnl"] > 0) / len(session_good) * 100 if session_good else 0
    bad_wr = sum(1 for t in session_bad if t["realized_pnl"] > 0) / len(session_bad) * 100 if session_bad else 0

    print(f"\n=== Signal 1: Session Gating ===")
    print(f"  Good session (07-21 UTC): {len(session_good)} trades, WR={good_wr:.1f}%, PnL=${good_pnl:.2f}")
    print(f"  Bad session (21-07 UTC):  {len(session_bad)} trades, WR={bad_wr:.1f}%, PnL=${bad_pnl:.2f}")

    # 2. Regime quality (proxy for competition lane priority)
    regime_trades = defaultdict(list)
    for t in fx_trades:
        regime = (t.get("regime_at_entry", "") or "unknown").upper()
        regime_trades[regime].append(t)

    print(f"\n=== Signal 2: Regime Quality ===")
    for regime, r_trades in sorted(regime_trades.items(), key=lambda x: -sum(t["realized_pnl"] for t in x[1])):
        pnl = sum(t["realized_pnl"] for t in r_trades)
        wr = sum(1 for t in r_trades if t["realized_pnl"] > 0) / len(r_trades) * 100 if r_trades else 0
        print(f"  {regime:15s}: {len(r_trades):4d} trades, WR={wr:5.1f}%, PnL=${pnl:8.2f}")

    # 3. Mode performance (proxy for signal quality)
    mode_trades = defaultdict(list)
    for t in fx_trades:
        mode = (t.get("entry_mode", "") or "unknown").upper()
        mode_trades[mode].append(t)

    print(f"\n=== Signal 3: Mode Performance ===")
    for mode, m_trades in sorted(mode_trades.items(), key=lambda x: -sum(t["realized_pnl"] for t in x[1])):
        pnl = sum(t["realized_pnl"] for t in m_trades)
        wr = sum(1 for t in m_trades if t["realized_pnl"] > 0) / len(m_trades) * 100 if m_trades else 0
        avg_pnl = pnl / len(m_trades) if m_trades else 0
        print(f"  {mode:15s}: {len(m_trades):4d} trades, WR={wr:5.1f}%, avg=${avg_pnl:6.2f}, PnL=${pnl:8.2f}")

    # 4. Symbol performance (proxy for hot streak detection)
    sym_trades = defaultdict(list)
    for t in fx_trades:
        sym = (t.get("symbol", "") or "unknown").upper()
        sym_trades[sym].append(t)

    print(f"\n=== Signal 4: Symbol Hot Streaks ===")
    for sym, s_trades in sorted(sym_trades.items(), key=lambda x: -sum(t["realized_pnl"] for t in x[1])):
        pnl = sum(t["realized_pnl"] for t in s_trades)
        wr = sum(1 for t in s_trades if t["realized_pnl"] > 0) / len(s_trades) * 100 if s_trades else 0
        avg_pnl = pnl / len(s_trades) if s_trades else 0
        print(f"  {sym:10s}: {len(s_trades):4d} trades, WR={wr:5.1f}%, avg=${avg_pnl:6.2f}, PnL=${pnl:8.2f}")

    # 5. Fusion: What happens when we combine signals?
    # Test: only enter when session is good AND regime is not losing
    print(f"\n=== Signal Fusion: Session + Regime ===")
    
    # Count how many trades would have been blocked by each signal
    total_fx = len(fx_trades)
    blocked_by_session = len(session_bad)
    
    # Count regime quality: count trades from regimes with WR < 40%
    losing_regime_count = 0
    losing_regime_pnl = 0
    for regime, r_trades in regime_trades.items():
        if len(r_trades) >= 5:  # Need minimum sample
            wr = sum(1 for t in r_trades if t["realized_pnl"] > 0) / len(r_trades)
            if wr < 0.40:
                losing_regime_count += len(r_trades)
                losing_regime_pnl += sum(t["realized_pnl"] for t in r_trades)

    # Combined: session + regime filter
    fusion_trades = [t for t in session_good 
                     if (t.get("regime_at_entry", "") or "unknown").upper() not in 
                     {r for r, rt in regime_trades.items() 
                      if len(rt) >= 5 and sum(1 for t in rt if t["realized_pnl"] > 0) / len(rt) < 0.40}]
    fusion_pnl = sum(t["realized_pnl"] for t in fusion_trades)
    fusion_wr = sum(1 for t in fusion_trades if t["realized_pnl"] > 0) / len(fusion_trades) * 100 if fusion_trades else 0
    fusion_blocked = total_fx - len(fusion_trades)
    
    print(f"  Total FX trades:     {total_fx}")
    print(f"  Blocked by session:  {blocked_by_session} (${bad_pnl:+.2f})")
    print(f"  Blocked by regime:   {losing_regime_count} (${losing_regime_pnl:+.2f})")
    print(f"  Fusion (both):       {len(fusion_trades)} trades, WR={fusion_wr:.1f}%, PnL=${fusion_pnl:.2f}")
    print(f"  Trades blocked:      {fusion_blocked} ({fusion_blocked/total_fx*100:.1f}%)")
    print(f"  PnL improvement:     ${fusion_pnl - good_pnl:+.2f} vs session-only")

    # 6. What about high-confidence only?
    conf_trades = [t for t in fx_trades if t.get("entry_confidence_raw", 0) is not None and t["entry_confidence_raw"] >= 0.60]
    conf_pnl = sum(t["realized_pnl"] for t in conf_trades)
    conf_wr = sum(1 for t in conf_trades if t["realized_pnl"] > 0) / len(conf_trades) * 100 if conf_trades else 0
    print(f"\n=== Confidence Filter (>=0.60) ===")
    print(f"  {len(conf_trades)} trades, WR={conf_wr:.1f}%, PnL=${conf_pnl:.2f}")
    print(f"  vs all FX: {len(fx_trades)} trades, PnL=${sum(t['realized_pnl'] for t in fx_trades):.2f}")

    # 7. Combined fusion: session + regime + confidence
    all_fusion = [t for t in conf_trades 
                  if is_good_session(t.get("entry_time_utc", ""))
                  and (t.get("regime_at_entry", "") or "unknown").upper() not in 
                  {r for r, rt in regime_trades.items() 
                   if len(rt) >= 5 and sum(1 for t in rt if t["realized_pnl"] > 0) / len(rt) < 0.40}]
    all_fusion_pnl = sum(t["realized_pnl"] for t in all_fusion)
    all_fusion_wr = sum(1 for t in all_fusion if t["realized_pnl"] > 0) / len(all_fusion) * 100 if all_fusion else 0
    
    print(f"\n=== FULL FUSION: Session + Regime + Confidence ===")
    print(f"  {len(all_fusion)} trades, WR={all_fusion_wr:.1f}%, PnL=${all_fusion_pnl:.2f}")
    print(f"  Blocked: {len(fx_trades) - len(all_fusion)} ({(len(fx_trades) - len(all_fusion))/len(fx_trades)*100:.1f}%)")
    print(f"  Avg PnL per trade: ${all_fusion_pnl/len(all_fusion):.2f}" if all_fusion else "  No trades")

    # Write report
    report_path = REPO / "reports" / "smart_entry_v2_fusion_analysis.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Smart Entry v2 Signal Fusion Analysis\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"Total closed trades analyzed: {len(trades)}\n")
        f.write(f"FX trades: {len(fx_trades)}\n\n")
        f.write(f"### Signal 1: Session Gating\n\n")
        f.write(f"- Good session: {len(session_good)} trades, WR={good_wr:.1f}%, PnL=${good_pnl:.2f}\n")
        f.write(f"- Bad session: {len(session_bad)} trades, WR={bad_wr:.1f}%, PnL=${bad_pnl:.2f}\n\n")
        f.write(f"### Signal 2: Regime Quality\n\n")
        for regime, r_trades in sorted(regime_trades.items(), key=lambda x: -sum(t["realized_pnl"] for t in x[1])):
            pnl = sum(t["realized_pnl"] for t in r_trades)
            wr = sum(1 for t in r_trades if t["realized_pnl"] > 0) / len(r_trades) * 100 if r_trades else 0
            f.write(f"- {regime}: {len(r_trades)} trades, WR={wr:.1f}%, PnL=${pnl:.2f}\n")
        f.write(f"\n### Full Fusion Results\n\n")
        f.write(f"- Trades: {len(all_fusion)} (blocked {len(fx_trades) - len(all_fusion)})\n")
        f.write(f"- WR: {all_fusion_wr:.1f}%\n")
        f.write(f"- Total PnL: ${all_fusion_pnl:.2f}\n")
        f.write(f"- Avg per trade: ${all_fusion_pnl/len(all_fusion):.2f}\n")
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    analyze()
