"""
External supervisor for the canonical MT5 competition bot.

The live entrypoint remains `python mt5_bot.py`.
This launcher keeps a separate worker process alive so native/interpreter
crashes in the trading loop do not silently kill supervision.
"""

from __future__ import annotations

import ast
import json
import os
import py_compile
import subprocess
import sys
import time
import ctypes
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKER_FILE = BASE_DIR / "mt5_bot_v10.py"
WORKER_EXTERNAL_IMPORT_ROOTS = {"MetaTrader5"}
LAUNCHER_STATE_FILE = BASE_DIR / "canonical_launcher_state.json"
WORKER_STATE_FILE = BASE_DIR / "canonical_worker_state.json"
OUT_LOG = BASE_DIR / "mt5_canonical_supervisor_out.log"
ERR_LOG = BASE_DIR / "mt5_canonical_supervisor_err.log"
WORKER_OUT_LOG = BASE_DIR / "mt5_canonical_worker_out.log"
WORKER_ERR_LOG = BASE_DIR / "mt5_canonical_worker_err.log"
RESTART_DELAY_SECONDS = 5
CANONICAL_SUPERVISOR_ENV = "CANONICAL_MT5_SUPERVISOR"
CANONICAL_SUPERVISOR_DETACHED_ENV = "CANONICAL_MT5_SUPERVISOR_DETACHED"
SUPERVISOR_MUTEX_NAME = "Local\\TradingBotsCanonicalMT5Supervisor"
_supervisor_mutex_handle = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_getppid() -> int:
    try:
        return os.getppid()
    except OSError:
        return 0


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    try:
        with open_log_sink(OUT_LOG) as handle:
            handle.write(f"{line}\n")
    except OSError:
        pass


def open_log_sink(path: Path):
    try:
        return path.open("a", encoding="utf-8")
    except OSError:
        fallback = path.with_name(f"{path.stem}.{os.getpid()}.fallback{path.suffix}")
        try:
            return fallback.open("a", encoding="utf-8")
        except OSError:
            return open(os.devnull, "a", encoding="utf-8")


def windows_detach_creationflags() -> int:
    if os.name != "nt":
        return 0
    flags = 0
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return flags


def popen_with_windows_detach_fallback(args, **kwargs):
    """
    Try the detached Windows spawn path first, then fall back to a normal spawn
    if the host denies those creation flags.
    """
    creationflags = kwargs.pop("creationflags", 0)
    try:
        return subprocess.Popen(args, creationflags=creationflags, **kwargs)
    except PermissionError:
        if os.name != "nt" or not creationflags:
            raise
        return subprocess.Popen(args, creationflags=0, **kwargs)


def relaunch_supervisor_detached_if_needed() -> bool:
    """
    Detach the canonical supervisor from the launching shell on Windows.

    Recent outage forensics showed whole-pair DOWN events without worker stderr
    or a normal supervisor restart trail. That pattern is more consistent with
    external/session interruption than an in-loop trading crash. Relaunching the
    supervisor into its own detached process group reduces the chance that shell
    cleanup or terminal teardown kills both canonical processes at once.
    """
    if os.name != "nt":
        return False
    if os.environ.get(CANONICAL_SUPERVISOR_DETACHED_ENV) == "1":
        return False

    detached_env = os.environ.copy()
    detached_env[CANONICAL_SUPERVISOR_DETACHED_ENV] = "1"
    with open_log_sink(OUT_LOG) as stdout_handle, open_log_sink(ERR_LOG) as stderr_handle:
        popen_with_windows_detach_fallback(
            [sys.executable, str(BASE_DIR / "mt5_bot.py")],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            env=detached_env,
            creationflags=windows_detach_creationflags(),
            close_fds=True,
        )
    return True


def get_process_command_line(pid: int) -> str:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\"; if ($p) {{ $p.CommandLine }}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def find_existing_launcher_pid(current_pid: int) -> int | None:
    if not LAUNCHER_STATE_FILE.exists():
        return None
    try:
        state = json.loads(LAUNCHER_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    launcher_pid = state.get("launcher_pid")
    if launcher_pid in (None, "", current_pid):
        return None

    try:
        launcher_pid = int(launcher_pid)
    except Exception:
        return None

    cmdline = get_process_command_line(launcher_pid)
    if "mt5_bot.py" in cmdline:
        return launcher_pid
    return None


def acquire_supervisor_mutex() -> bool:
    """Use an OS-level mutex so two launchers cannot race past state-file checks."""
    global _supervisor_mutex_handle

    if os.name != "nt":
        return True

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, SUPERVISOR_MUTEX_NAME)
    if not handle:
        return True

    last_error = kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False

    _supervisor_mutex_handle = handle
    return True


def release_supervisor_mutex() -> None:
    global _supervisor_mutex_handle

    if _supervisor_mutex_handle and os.name == "nt":
        try:
            ctypes.windll.kernel32.CloseHandle(_supervisor_mutex_handle)
        except Exception:
            pass
    _supervisor_mutex_handle = None


def read_worker_state() -> dict[str, object]:
    if not WORKER_STATE_FILE.exists():
        return {}
    try:
        return json.loads(WORKER_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_worker_state_snapshot(
    pid: int | None,
    status: str,
    event: str,
    reason: str = "",
    detail: str = "",
    exit_code: object = None,
) -> None:
    payload = {
        "updated_at": utc_now(),
        "pid": pid,
        "status": status,
        "event": event,
        "reason": reason,
        "detail": detail,
        "exit_code": exit_code,
    }
    WORKER_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_state(**updates: object) -> None:
    state = {
        "updated_at": utc_now(),
        "launcher_pid": None,
        "launcher_parent_pid": None,
        "worker_pid": None,
        "status": "unknown",
        "restart_count": 0,
        "last_spawned_at": None,
        "last_exit_at": None,
        "last_exit_code": None,
        "last_error": "",
        "last_worker_status": "",
        "last_worker_event": "",
        "last_worker_reason": "",
        "last_worker_detail": "",
        "last_worker_updated_at": None,
    }
    if LAUNCHER_STATE_FILE.exists():
        try:
            state.update(json.loads(LAUNCHER_STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    state.update(updates)
    state["updated_at"] = utc_now()
    LAUNCHER_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def normalize_exit_code(exit_code: object) -> tuple[object, object]:
    try:
        signed = int(exit_code)
    except Exception:
        return exit_code, exit_code
    unsigned = signed & 0xFFFFFFFF
    return signed, unsigned


def sleep_with_state_heartbeat(seconds: int, **state_updates: object) -> None:
    """
    Keep launcher state fresh during retry sleeps so a disappeared supervisor can
    be distinguished from a healthy supervisor that is intentionally waiting.
    """
    deadline = time.time() + max(0, int(seconds))
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        write_state(**state_updates)
        time.sleep(min(1.0, remaining))


def resolve_worker_local_module_files(module_name: str) -> list[Path]:
    module_path = BASE_DIR.joinpath(*module_name.split("."))
    candidates = []

    file_candidate = module_path.with_suffix(".py")
    if file_candidate.exists():
        candidates.append(file_candidate)

    package_candidate = module_path / "__init__.py"
    if package_candidate.exists():
        candidates.append(package_candidate)

    return candidates


def get_worker_dependency_validation_targets() -> tuple[list[Path], list[str]]:
    source = WORKER_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WORKER_FILE))
    dependency_files: dict[Path, None] = {}
    missing_modules: dict[str, None] = {}

    def register_module(module_name: str) -> None:
        root = module_name.split(".", 1)[0]
        if root in sys.stdlib_module_names or root in WORKER_EXTERNAL_IMPORT_ROOTS:
            return

        candidates = resolve_worker_local_module_files(module_name)
        if candidates:
            for path in candidates:
                dependency_files[path] = None
            return

        missing_modules[module_name] = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                register_module(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            register_module(node.module)
            package_candidates = resolve_worker_local_module_files(node.module)
            if any(path.name == "__init__.py" for path in package_candidates):
                for alias in node.names:
                    register_module(f"{node.module}.{alias.name}")

    return sorted(dependency_files), sorted(missing_modules)


def validate_worker_file() -> tuple[bool, str]:
    """
    Refuse to spawn a syntactically broken worker or a worker whose extracted
    local modules are missing/broken.

    Live competition outages have repeatedly come from on-disk drift that left
    `mt5_bot_v10.py` uncompilable. More recent failures also came from the
    worker's repo-local imports drifting out of sync during incremental extractions
    (`bot/*.py`, `symbol_learner.py`). Catch both before launch so supervision
    stays alive, state stays honest, and the pair does not enter a pointless
    restart loop while positions remain open in MT5.
    """
    try:
        py_compile.compile(str(WORKER_FILE), doraise=True)
        dependency_files, missing_modules = get_worker_dependency_validation_targets()
        if missing_modules:
            missing_preview = ", ".join(missing_modules[:4])
            return False, f"missing worker dependency import(s): {missing_preview}"
        for dependency_file in dependency_files:
            py_compile.compile(str(dependency_file), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> None:
    if relaunch_supervisor_detached_if_needed():
        log("SUPERVISOR detached handoff spawned; parent launcher exiting")
        return

    restart_count = 0
    if not acquire_supervisor_mutex():
        log("SUPERVISOR exiting because canonical supervisor mutex is already held")
        return

    existing_launcher_pid = find_existing_launcher_pid(os.getpid())
    if existing_launcher_pid:
        log(f"SUPERVISOR exiting because launcher PID {existing_launcher_pid} is already active")
        release_supervisor_mutex()
        return

    try:
        log(
            "SUPERVISOR starting "
            f"pid={os.getpid()} ppid={safe_getppid()} "
            f"detached={os.environ.get(CANONICAL_SUPERVISOR_DETACHED_ENV) == '1'}"
        )
        write_state(
            launcher_pid=os.getpid(),
            launcher_parent_pid=safe_getppid(),
            worker_pid=None,
            status="starting",
            restart_count=restart_count,
            last_error="",
        )

        while True:
            worker_ok, worker_compile_error = validate_worker_file()
            if not worker_ok:
                detail = worker_compile_error.strip()
                write_worker_state_snapshot(
                    None,
                    "refused",
                    "syntax_check_failed",
                    "worker file failed prelaunch compile gate",
                    detail,
                    1,
                )
                log(
                    "SUPERVISOR refusing worker spawn because mt5_bot_v10.py "
                    f"failed compile gate; retrying in {RESTART_DELAY_SECONDS}s"
                )
                write_state(
                    launcher_pid=os.getpid(),
                    launcher_parent_pid=safe_getppid(),
                    worker_pid=None,
                    status="waiting_for_valid_worker",
                    restart_count=restart_count,
                    last_exit_at=utc_now(),
                    last_exit_code=1,
                    last_error="worker file failed prelaunch compile gate",
                    last_worker_status="refused",
                    last_worker_event="syntax_check_failed",
                    last_worker_reason="worker file failed prelaunch compile gate",
                    last_worker_detail=detail,
                    last_worker_updated_at=utc_now(),
                )
                sleep_with_state_heartbeat(
                    RESTART_DELAY_SECONDS,
                    launcher_pid=os.getpid(),
                    launcher_parent_pid=safe_getppid(),
                    worker_pid=None,
                    status="waiting_for_valid_worker",
                    restart_count=restart_count,
                    last_exit_at=utc_now(),
                    last_exit_code=1,
                    last_error="worker file failed prelaunch compile gate",
                    last_worker_status="refused",
                    last_worker_event="syntax_check_failed",
                    last_worker_reason="worker file failed prelaunch compile gate",
                    last_worker_detail=detail,
                    last_worker_updated_at=utc_now(),
                )
                continue

            with open_log_sink(WORKER_OUT_LOG) as stdout_handle, open_log_sink(WORKER_ERR_LOG) as stderr_handle:
                log("SUPERVISOR spawning mt5_bot_v10.py worker")
                worker_env = os.environ.copy()
                worker_env[CANONICAL_SUPERVISOR_ENV] = "1"
                worker = popen_with_windows_detach_fallback(
                    [sys.executable, str(WORKER_FILE)],
                    cwd=str(BASE_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    env=worker_env,
                    creationflags=windows_detach_creationflags(),
                    close_fds=True,
                )

                write_state(
                    launcher_pid=os.getpid(),
                    launcher_parent_pid=safe_getppid(),
                    worker_pid=worker.pid,
                    status="running",
                    restart_count=restart_count,
                    last_spawned_at=utc_now(),
                    last_error="",
                    last_worker_status="",
                    last_worker_event="",
                    last_worker_reason="",
                    last_worker_detail="",
                    last_worker_updated_at=None,
                )
                write_worker_state_snapshot(
                    worker.pid,
                    "starting",
                    "spawned",
                    "supervisor spawned worker",
                    "awaiting worker heartbeat",
                    None,
                )

                try:
                    while True:
                        exit_code = worker.poll()
                        if exit_code is not None:
                            break
                        write_state(
                            launcher_pid=os.getpid(),
                            launcher_parent_pid=safe_getppid(),
                            worker_pid=worker.pid,
                            status="running",
                            restart_count=restart_count,
                            last_spawned_at=utc_now(),
                            last_error="",
                            last_worker_status="",
                            last_worker_event="",
                            last_worker_reason="",
                            last_worker_detail="",
                            last_worker_updated_at=None,
                        )
                        time.sleep(1.0)
                except KeyboardInterrupt:
                    log("SUPERVISOR received keyboard interrupt, stopping worker")
                    worker.terminate()
                    try:
                        worker.wait(timeout=10)
                    except Exception:
                        worker.kill()
                    write_state(
                        launcher_pid=os.getpid(),
                        launcher_parent_pid=safe_getppid(),
                        worker_pid=None,
                        status="stopped",
                        last_exit_at=utc_now(),
                        last_exit_code="keyboard_interrupt",
                        last_error="supervisor keyboard interrupt",
                    )
                    raise

            worker_state = read_worker_state()
            if worker_state.get("pid") != worker.pid:
                worker_state = {
                    "pid": worker.pid,
                    "status": "unknown",
                    "event": "state_mismatch",
                    "reason": "worker state file belonged to a different pid",
                    "detail": f"expected_pid={worker.pid} observed_pid={worker_state.get('pid')}",
                    "updated_at": utc_now(),
                    "exit_code": exit_code,
                }
            worker_reason = str(worker_state.get("reason", "") or "")
            worker_event = str(worker_state.get("event", "") or "")
            worker_status = str(worker_state.get("status", "") or "")
            worker_detail = str(worker_state.get("detail", "") or "")
            worker_updated_at = worker_state.get("updated_at")
            restart_count += 1
            exit_signed, exit_unsigned = normalize_exit_code(exit_code)
            if worker_event or worker_reason:
                log(
                    f"SUPERVISOR noticed worker exit code signed={exit_signed} unsigned={exit_unsigned}; "
                    f"worker_status={worker_status or '?'} event={worker_event or '?'} reason={worker_reason or '?'}; "
                    f"restarting in {RESTART_DELAY_SECONDS}s"
                )
            else:
                log(
                    f"SUPERVISOR noticed worker exit code signed={exit_signed} "
                    f"unsigned={exit_unsigned}; restarting in {RESTART_DELAY_SECONDS}s"
                )
            write_state(
                launcher_pid=os.getpid(),
                launcher_parent_pid=safe_getppid(),
                worker_pid=None,
                status="restarting",
                restart_count=restart_count,
                last_exit_at=utc_now(),
                last_exit_code=exit_signed,
                last_error=worker_reason or ("" if exit_code == 0 else f"worker exited with code {exit_code}"),
                last_worker_status=worker_status,
                last_worker_event=worker_event,
                last_worker_reason=worker_reason,
                last_worker_detail=worker_detail,
                last_worker_updated_at=worker_updated_at,
            )
            sleep_with_state_heartbeat(
                RESTART_DELAY_SECONDS,
                launcher_pid=os.getpid(),
                launcher_parent_pid=safe_getppid(),
                worker_pid=None,
                status="restarting",
                restart_count=restart_count,
                last_exit_at=utc_now(),
                last_exit_code=exit_signed,
                last_error=worker_reason or ("" if exit_code == 0 else f"worker exited with code {exit_code}"),
                last_worker_status=worker_status,
                last_worker_event=worker_event,
                last_worker_reason=worker_reason,
                last_worker_detail=worker_detail,
                last_worker_updated_at=worker_updated_at,
            )
    finally:
        release_supervisor_mutex()


if __name__ == "__main__":
    main()
