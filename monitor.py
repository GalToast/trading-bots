import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency fallback
    psutil = None

from mt5_config import BOT_COMMENT_PREFIX, BOT_MAGIC, LOGIN, PASSWORD, SERVER

BRAIN_FILE = os.path.join(os.path.dirname(__file__), "brain.json")
RUNTIME_STATE_FILE = os.path.join(os.path.dirname(__file__), "runtime_state.json")
LAUNCHER_STATE_FILE = os.path.join(os.path.dirname(__file__), "canonical_launcher_state.json")
WORKER_STATE_FILE = os.path.join(os.path.dirname(__file__), "canonical_worker_state.json")
WORKER_REFUSAL_STATE_FILE = os.path.join(os.path.dirname(__file__), "canonical_worker_refusal_state.json")


def windows_no_window_creationflags():
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_bot_position(pos):
    comment = getattr(pos, "comment", "") or ""
    return getattr(pos, "magic", None) == BOT_MAGIC or comment.startswith(f"{BOT_COMMENT_PREFIX}-")


def get_bot_processes():
    if psutil is not None:
        rows = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
            except (psutil.Error, OSError):
                continue
            name = str(info.get("name") or "")
            lowered = name.lower()
            if "python" not in lowered and "terminal64" not in lowered:
                continue
            cmdline_parts = [str(part) for part in (info.get("cmdline") or []) if str(part)]
            rows.append(
                {
                    "ProcessId": int(info.get("pid", 0) or 0),
                    "Name": name,
                    "CommandLine": subprocess.list2cmdline(cmdline_parts) if cmdline_parts else "",
                }
            )
        return rows

    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python|pythonw|terminal64' } | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            creationflags=windows_no_window_creationflags(),
        )
        stdout = result.stdout.strip()
        if not stdout:
            return []
        data = json.loads(stdout)
        if isinstance(data, dict):
            data = [data]
        return data
    except Exception:
        return []


def load_brain_summary():
    if not os.path.exists(BRAIN_FILE):
        return {"global": {"total_trades": 0, "total_wins": 0, "total_losses": 0}, "symbols": 0}
    try:
        with open(BRAIN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        global_stats = data.get("global", {})
        return {
            "global": {
                "total_trades": global_stats.get("total_trades", 0),
                "total_wins": global_stats.get("total_wins", 0),
                "total_losses": global_stats.get("total_losses", 0),
            },
            "symbols": len(data.get("symbols", {})),
        }
    except Exception:
        return {"global": {"total_trades": "?", "total_wins": "?", "total_losses": "?"}, "symbols": "?"}


def load_runtime_state():
    if not os.path.exists(RUNTIME_STATE_FILE):
        return None
    try:
        with open(RUNTIME_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_launcher_state():
    if not os.path.exists(LAUNCHER_STATE_FILE):
        return None
    try:
        with open(LAUNCHER_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_worker_state():
    if not os.path.exists(WORKER_STATE_FILE):
        return None
    try:
        with open(WORKER_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_worker_refusal_state():
    if not os.path.exists(WORKER_REFUSAL_STATE_FILE):
        return None
    try:
        with open(WORKER_REFUSAL_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_runtime_age_seconds(runtime_state):
    if not runtime_state:
        return None
    updated_at = runtime_state.get("updated_at")
    if not updated_at:
        return None
    try:
        ts = datetime.fromisoformat(updated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


def process_ids_from_bot_processes(bot_processes):
    ids = set()
    for proc in bot_processes:
        try:
            ids.add(int(proc.get("ProcessId")))
        except Exception:
            continue
    return ids


def derive_live_status(bot_processes, launcher_state, runtime_age_seconds):
    process_ids = process_ids_from_bot_processes(bot_processes)
    launcher_processes = [
        p for p in bot_processes if "mt5_bot.py" in (p.get("CommandLine") or "")
    ]
    worker_processes = [
        p for p in bot_processes if "mt5_bot_v10.py" in (p.get("CommandLine") or "")
    ]
    launcher_pid = None
    worker_pid = None
    if launcher_state:
        try:
            launcher_pid = int(launcher_state.get("launcher_pid"))
        except Exception:
            launcher_pid = None
        try:
            worker_pid = int(launcher_state.get("worker_pid"))
        except Exception:
            worker_pid = None

    launcher_seen = launcher_pid in process_ids if launcher_pid is not None else False
    worker_seen = worker_pid in process_ids if worker_pid is not None else False
    heartbeat_fresh = runtime_age_seconds is not None and runtime_age_seconds <= 15
    any_launcher_process = len(launcher_processes) > 0
    any_worker_process = len(worker_processes) > 0

    if any_worker_process and not any_launcher_process and heartbeat_fresh:
        status = "WORKER_ONLY"
    elif process_ids and heartbeat_fresh:
        status = "LIVE"
    elif process_ids:
        status = "PROCESS_ONLY"
    elif heartbeat_fresh:
        status = "HEARTBEAT_ONLY"
    else:
        status = "DOWN"

    return {
        "status": status,
        "launcher_seen": launcher_seen,
        "worker_seen": worker_seen,
        "heartbeat_fresh": heartbeat_fresh,
        "any_launcher_process": any_launcher_process,
        "any_worker_process": any_worker_process,
    }


def build_symbol_summary(positions):
    summary = {}
    for pos in positions:
        bucket = summary.setdefault(
            pos.symbol,
            {"count": 0, "volume": 0.0, "pnl": 0.0},
        )
        bucket["count"] += 1
        bucket["volume"] += float(getattr(pos, "volume", 0.0) or 0.0)
        bucket["pnl"] += float(getattr(pos, "profit", 0.0) or 0.0)
    return sorted(summary.items(), key=lambda item: (item[1]["count"], abs(item[1]["pnl"])), reverse=True)


def build_managed_symbol_summary(runtime_state, positions):
    if not runtime_state or not runtime_state.get("positions"):
        return []

    live_by_ticket = {int(pos.ticket): pos for pos in positions}
    summary = {}
    for item in runtime_state["positions"]:
        ticket = int(item.get("ticket", 0) or 0)
        pos = live_by_ticket.get(ticket)
        if not pos:
            continue
        symbol = item.get("symbol") or pos.symbol
        bucket = summary.setdefault(
            symbol,
            {"count": 0, "volume": 0.0, "pnl": 0.0, "adopted_count": 0},
        )
        bucket["count"] += 1
        bucket["volume"] += float(getattr(pos, "volume", 0.0) or 0.0)
        bucket["pnl"] += float(getattr(pos, "profit", 0.0) or 0.0)
        if item.get("adopted"):
            bucket["adopted_count"] += 1

    return sorted(summary.items(), key=lambda item: (item[1]["count"], abs(item[1]["pnl"])), reverse=True)


def margin_status(info):
    equity = float(getattr(info, "equity", 0.0) or 0.0)
    margin_free = float(getattr(info, "margin_free", 0.0) or 0.0)
    if equity <= 0:
        return "UNKNOWN", 0.0
    ratio = margin_free / equity
    if ratio < 0.10:
        return "CRITICAL", ratio
    if ratio < 0.20:
        return "WARN", ratio
    return "OK", ratio


def print_status():
    processes = get_bot_processes()
    bot_processes = [
        p
        for p in processes
        if any(
            marker in (p.get("CommandLine") or "")
            for marker in ("mt5_bot.py", "mt5_bot_v10.py")
        )
    ]
    terminal_running = any((p.get("Name") or "").lower() == "terminal64.exe" for p in processes)

    info = mt5.account_info()
    positions = mt5.positions_get() or []
    bot_positions = [p for p in positions if is_bot_position(p)]
    total_pl = sum(p.profit for p in positions)
    bot_pl = sum(p.profit for p in bot_positions)
    brain = load_brain_summary()
    runtime_state = load_runtime_state()
    launcher_state = load_launcher_state()
    worker_state = load_worker_state()
    worker_state_pid_mismatch = False
    if worker_state and launcher_state:
        worker_state_pid = worker_state.get("pid")
        launcher_worker_pid = launcher_state.get("worker_pid")
        worker_state_pid_mismatch = (
            worker_state_pid not in (None, "")
            and launcher_worker_pid not in (None, "")
            and worker_state_pid != launcher_worker_pid
        )
    worker_refusal_state = load_worker_refusal_state()
    runtime_age_seconds = get_runtime_age_seconds(runtime_state)
    live_status = derive_live_status(bot_processes, launcher_state, runtime_age_seconds)

    print("=" * 70)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Canonical MT5 monitor")
    print(f"MT5 terminal running: {'yes' if terminal_running else 'no'}")
    print(f"Canonical bot process count: {len(bot_processes)}")
    for proc in bot_processes:
        print(f"  PID {proc.get('ProcessId')}: {proc.get('CommandLine')}")
    if launcher_state:
        print(
            f"Launcher state: {launcher_state.get('status', '?')} | "
            f"launcher_pid={launcher_state.get('launcher_pid', '?')} | "
            f"worker_pid={launcher_state.get('worker_pid', '?')} | "
            f"restarts={launcher_state.get('restart_count', '?')}"
        )
        print(
            f"Effective live status: {live_status['status']} | "
            f"launcher_seen={'yes' if live_status['launcher_seen'] else 'no'} | "
            f"worker_seen={'yes' if live_status['worker_seen'] else 'no'} | "
            f"heartbeat_fresh={'yes' if live_status['heartbeat_fresh'] else 'no'}"
        )
        if live_status["status"] == "WORKER_ONLY":
            print("  runtime warning: standalone worker detected without canonical supervisor; launch path likely drifted")
        if launcher_state.get("last_exit_code") not in (None, ""):
            print(
                f"  last exit: code={launcher_state.get('last_exit_code')} "
                f"at {launcher_state.get('last_exit_at')}"
            )
        if launcher_state.get("last_worker_status") or launcher_state.get("last_worker_event"):
            print(
                f"  worker exit detail: status={launcher_state.get('last_worker_status') or '?'} "
                f"event={launcher_state.get('last_worker_event') or '?'} "
                f"reason={launcher_state.get('last_worker_reason') or '?'}"
            )
            if launcher_state.get("last_worker_updated_at"):
                print(f"    worker state timestamp: {launcher_state.get('last_worker_updated_at')}")
            if launcher_state.get("last_worker_detail"):
                detail = str(launcher_state.get("last_worker_detail")).strip().splitlines()[0]
                print(f"    worker detail: {detail}")
        if launcher_state.get("last_error"):
            print(f"  launcher detail: {launcher_state.get('last_error')}")
    if worker_state:
        print(
            f"Worker state: status={worker_state.get('status') or '?'} "
            f"event={worker_state.get('event') or '?'} "
            f"reason={worker_state.get('reason') or '?'}"
        )
        if worker_state_pid_mismatch:
            print(
                f"  worker state warning: pid mismatch "
                f"(state={worker_state.get('pid')} launcher={launcher_state.get('worker_pid')})"
            )
        if worker_state.get("updated_at"):
            print(f"  worker state timestamp: {worker_state.get('updated_at')}")
        if worker_state.get("detail"):
            detail = str(worker_state.get("detail")).strip().splitlines()[0]
            print(f"  worker detail: {detail}")
    if worker_refusal_state:
        print(
            f"Latest standalone refusal: status={worker_refusal_state.get('status') or '?'} "
            f"event={worker_refusal_state.get('event') or '?'} "
            f"reason={worker_refusal_state.get('reason') or '?'}"
        )
        if worker_refusal_state.get("updated_at"):
            print(f"  refusal timestamp: {worker_refusal_state.get('updated_at')}")

    if not info:
        print("MT5 account: unavailable")
        return

    print(f"Account: login={info.login} server={info.server}")
    print(f"Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f} | Margin Free: ${info.margin_free:.2f}")
    margin_level, margin_ratio = margin_status(info)
    print(f"Free margin status: {margin_level} ({margin_ratio*100:.1f}% of equity)")
    print(f"All positions: {len(positions)} | All P/L: ${total_pl:+.2f}")
    print(f"Bot positions: {len(bot_positions)} | Bot P/L: ${bot_pl:+.2f}")

    if runtime_state:
        print(
            "Managed by runtime: "
            f"{runtime_state.get('managed_positions', '?')} total | "
            f"{runtime_state.get('adopted_positions', '?')} adopted | "
            f"{runtime_state.get('direct_positions', '?')} direct"
        )
        if runtime_age_seconds is not None:
            stale = runtime_age_seconds > 15
            stale_note = " | STALE" if stale else ""
            print(f"Runtime heartbeat age: {runtime_age_seconds:.1f}s{stale_note}")
            if live_status["status"] == "DOWN":
                print("  runtime warning: canonical bot is definitively down (no live process and stale heartbeat)")
            elif stale and not bot_processes:
                print("  runtime warning: no canonical bot process detected and runtime state is stale")
        posture = runtime_state.get("entry_posture")
        if posture:
            print(
                f"Entry posture: {posture} | "
                f"rearm={'yes' if runtime_state.get('rearm_active') else 'no'} | "
                f"managed_dd={float(runtime_state.get('managed_drawdown_pct', 0.0) or 0.0)*100:.2f}% | "
                f"top_symbol_dd={float(runtime_state.get('top_symbol_drawdown_pct', 0.0) or 0.0)*100:.2f}%"
            )
            reason = runtime_state.get("rearm_reason")
            if reason:
                print(f"  posture detail: {reason}")
            post_cleanup_hold_remaining = int(runtime_state.get("post_cleanup_hold_remaining_s", 0) or 0)
            if post_cleanup_hold_remaining > 0:
                trigger = runtime_state.get("post_cleanup_hold_trigger") or "unknown"
                pnl = float(runtime_state.get("post_cleanup_hold_last_pnl", 0.0) or 0.0)
                print(
                    f"  post-cleanup holdoff: {post_cleanup_hold_remaining}s "
                    f"trigger={trigger} pnl=${pnl:+.2f}"
                )
            last_sync_close_holdoff_event = runtime_state.get("last_sync_close_holdoff_event")
            if last_sync_close_holdoff_event:
                print(f"  last sync-close holdoff: {last_sync_close_holdoff_event}")

    all_symbol_summary = build_symbol_summary(positions)
    if all_symbol_summary:
        print("Top all-position exposure:")
        for symbol, data in all_symbol_summary[:5]:
            print(
                f"  {symbol}: {data['count']} pos | {data['volume']:.2f} lots | "
                f"P/L ${data['pnl']:+.2f}"
            )

    managed_symbol_summary = build_managed_symbol_summary(runtime_state, positions)
    if managed_symbol_summary:
        print("Top managed-position exposure:")
        for symbol, data in managed_symbol_summary[:5]:
            adopted_note = f" | adopted {data['adopted_count']}" if data.get("adopted_count") else ""
            print(
                f"  {symbol}: {data['count']} pos | {data['volume']:.2f} lots | "
                f"P/L ${data['pnl']:+.2f}{adopted_note}"
            )

    if bot_positions:
        for pos in bot_positions[:15]:
            side = "BUY" if pos.type == 0 else "SELL"
            print(
                f"  #{pos.ticket} {pos.symbol} {side} {pos.volume:.2f} "
                f"P/L ${pos.profit:+.2f} magic={pos.magic} comment={pos.comment}"
            )
        if len(bot_positions) > 15:
            print(f"  ... {len(bot_positions) - 15} more bot positions")

    global_stats = brain["global"]
    print(
        "Brain: "
        f"symbols={brain['symbols']} trades={global_stats['total_trades']} "
        f"wins={global_stats['total_wins']} losses={global_stats['total_losses']}"
    )


def main():
    loops = 1
    interval = 10
    if len(sys.argv) > 1:
        loops = max(1, int(sys.argv[1]))
    if len(sys.argv) > 2:
        interval = max(1, int(sys.argv[2]))

    if not mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
        print(f"Failed to connect to MT5: {mt5.last_error()}")
        return 1

    try:
        for idx in range(loops):
            print_status()
            if idx < loops - 1:
                time.sleep(interval)
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
