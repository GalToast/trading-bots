"""FX Session Gating Analysis — Quantify off-session bleed for EURUSD/GBPUSD.

Analyzes trade_behavior_log.jsonl for the FX live lane symbols (EURUSD, GBPUSD)
split by session (London/NY overlap vs off-session) to estimate recovered value
if we re-enable is_good_session() gating for FX lattices.

Session definitions (UTC):
- GOOD_SESSION: 07:00-21:00 (London, overlap, NY)
- OFF_SESSION:  21:00-07:00 (Asian hours, thin liquidity)
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_FILE = REPO / "trade_behavior_log.jsonl"

FX_SYMBOLS = {"EURUSD", "GBPUSD", "NZDUSD", "USDJPY"}


def classify_session(utc_hour):
    if 7 <= utc_hour < 21:
        return "GOOD_SESSION"
    else:
        return "OFF_SESSION"


def main():
    trades = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            symbol = str(record.get("symbol", "") or "").upper()
            if symbol not in FX_SYMBOLS:
                continue
            # Only look at closed trades (have realized_pnl)
            pnl = record.get("realized_pnl")
            if pnl is None:
                continue
            timestamp = str(record.get("entry_time_utc", "") or record.get("recorded_at_utc", "") or "")
            utc_hour = None
            if timestamp:
                try:
                    # Try various formats
                    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                        try:
                            dt = datetime.strptime(timestamp[:19], fmt)
                            utc_hour = dt.hour
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            if utc_hour is None:
                continue

            session = classify_session(utc_hour)
            signal_type = str(record.get("signal_type", "") or "").upper()
            regime = str(record.get("regime_at_entry", "") or "").upper()
            direction = str(record.get("direction", "") or "").upper()

            trades.append({
                "symbol": symbol,
                "pnl": float(pnl),
                "session": session,
                "utc_hour": utc_hour,
                "signal_type": signal_type,
                "regime": regime,
                "direction": direction,
                "exit_reason": str(record.get("exit_reason", "") or ""),
            })

    print(f"=== FX Session Gating Analysis ===")
    print(f"Log: {LOG_FILE}")
    print(f"FX closed trades found: {len(trades)}\n")

    # Overall split
    by_session = defaultdict(list)
    for t in trades:
        by_session[t["session"]].append(t)

    print("=== Overall Session Split (All FX) ===\n")
    print(f"{'Session':<18} {'Count':>6} {'Win':>5} {'Loss':>5} {'Total PnL':>11} {'Avg PnL':>9} {'WR':>7}")
    print("-" * 68)
    for session in ["GOOD_SESSION", "OFF_SESSION"]:
        session_trades = by_session.get(session, [])
        count = len(session_trades)
        wins = sum(1 for t in session_trades if t["pnl"] > 0)
        losses = sum(1 for t in session_trades if t["pnl"] < 0)
        total = sum(t["pnl"] for t in session_trades)
        avg = total / count if count > 0 else 0
        wr = wins / count * 100 if count > 0 else 0
        print(f"{session:<18} {count:>6} {wins:>5} {losses:>5} {total:>11.2f} {avg:>9.2f} {wr:>6.1f}%")
    print()

    # By symbol
    print("=== By Symbol + Session ===\n")
    by_symbol_session = defaultdict(lambda: defaultdict(list))
    for t in trades:
        by_symbol_session[t["symbol"]][t["session"]].append(t)

    print(f"{'Symbol':<10} {'Session':<18} {'Count':>6} {'Avg PnL':>9} {'WR':>7} {'Total':>9}")
    print("-" * 68)
    for symbol in sorted(FX_SYMBOLS & {t["symbol"] for t in trades}):
        for session in ["GOOD_SESSION", "OFF_SESSION"]:
            session_trades = by_symbol_session[symbol].get(session, [])
            count = len(session_trades)
            if count == 0:
                continue
            wins = sum(1 for t in session_trades if t["pnl"] > 0)
            total = sum(t["pnl"] for t in session_trades)
            avg = total / count
            wr = wins / count * 100
            print(f"{symbol:<10} {session:<18} {count:>6} {avg:>9.2f} {wr:>6.1f}% {total:>9.2f}")
    print()

    # Candle direction specifically
    print("=== candle_direction by Session ===\n")
    cd_trades = [t for t in trades if t["signal_type"] == "CANDLE_DIRECTION"]
    cd_good = [t for t in cd_trades if t["session"] == "GOOD_SESSION"]
    cd_off = [t for t in cd_trades if t["session"] == "OFF_SESSION"]

    for label, subset in [("Good session", cd_good), ("Off-session", cd_off)]:
        count = len(subset)
        if count == 0:
            print(f"  {label}: No candle_direction trades")
            continue
        wins = sum(1 for t in subset if t["pnl"] > 0)
        total = sum(t["pnl"] for t in subset)
        avg = total / count
        wr = wins / count * 100
        print(f"  {label}: {count} trades, {wins}W/{count-wins}L, WR={wr:.1f}%, avg=${avg:.2f}, total=${total:.2f}")
    print()

    # Estimated recovery from session gating
    off_trades = by_session.get("OFF_SESSION", [])
    if off_trades:
        off_total = sum(t["pnl"] for t in off_trades)
        off_count = len(off_trades)
        off_avg = off_total / off_count if off_count else 0

        # If we had blocked all off-session trades:
        recovered = -off_total  # positive number if off-session is losing
        print(f"=== Session Gating Recovery Estimate ===\n")
        print(f"Off-session trades: {off_count}")
        print(f"Off-session total PnL: ${off_total:.2f}")
        print(f"Off-session avg PnL: ${off_avg:.2f}")
        if off_total < 0:
            print(f"BLOCKING off-session would have recovered: ${recovered:.2f}")
        else:
            print(f"Off-session was PROFITABLE (${off_total:.2f}) — gating would cost money")
        print()

        # Per-symbol recovery
        print("Per-symbol off-session recovery:")
        for symbol in sorted(set(t["symbol"] for t in off_trades)):
            sym_off = [t for t in off_trades if t["symbol"] == symbol]
            sym_total = sum(t["pnl"] for t in sym_off)
            print(f"  {symbol}: {len(sym_off)} trades, ${sym_total:+.2f} ({'would save' if sym_total < 0 else 'would lose'} ${abs(sym_total):.2f})")
    else:
        print("No off-session FX trades found — session gating not needed for FX either.")

    # Write report
    report_path = REPO / "reports" / "fx_session_gating_analysis.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# FX Session Gating Analysis\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"Total FX closed trades analyzed: {len(trades)}\n\n")
        for session in ["GOOD_SESSION", "OFF_SESSION"]:
            session_trades = by_session.get(session, [])
            count = len(session_trades)
            wins = sum(1 for t in session_trades if t["pnl"] > 0)
            total = sum(t["pnl"] for t in session_trades)
            avg = total / count if count > 0 else 0
            wr = wins / count * 100 if count > 0 else 0
            f.write(f"- {session}: {count} trades, WR={wr:.1f}%, avg=${avg:.2f}, total=${total:.2f}\n")
        f.write("\n## candle_direction Analysis\n\n")
        for label, subset in [("Good session", cd_good), ("Off-session", cd_off)]:
            count = len(subset)
            if count == 0:
                f.write(f"- {label}: No candle_direction trades\n")
                continue
            wins = sum(1 for t in subset if t["pnl"] > 0)
            total = sum(t["pnl"] for t in subset)
            avg = total / count
            wr = wins / count * 100
            f.write(f"- {label}: {count} trades, WR={wr:.1f}%, avg=${avg:.2f}, total=${total:.2f}\n")
        f.write("\n## Verdict\n\n")
        if off_total < 0:
            f.write(f"Blocking off-session would have recovered ${recovered:.2f} from {off_count} trades.\n")
            f.write("Session gating IS recommended for FX lattices.\n")
        else:
            f.write(f"Off-session was profitable (${off_total:.2f}). Session gating NOT recommended for FX.\n")
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
