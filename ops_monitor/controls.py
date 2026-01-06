from __future__ import annotations

import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import psutil


class ControlError(RuntimeError):
    pass


def _resolve_process(pid: int) -> psutil.Process:
    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess as exc:  # pragma: no cover - defensive
        raise ControlError(f"Target process {pid} is not running") from exc


def graceful_shutdown(pid: int) -> str:
    proc = _resolve_process(pid)
    proc.terminate()
    return "Sent SIGTERM; waiting for graceful shutdown."


def force_kill(pid: int) -> str:
    proc = _resolve_process(pid)
    proc.kill()
    return "Force-killed application process."


def reload_config(pid: int) -> str:
    proc = _resolve_process(pid)
    if sys.platform.startswith("win"):
        proc.terminate()
        return "Reload requested on Windows; sent terminate signal."
    if hasattr(signal, "SIGHUP"):
        proc.send_signal(signal.SIGHUP)
        return "Sent SIGHUP to reload configuration."
    proc.terminate()
    return "Reload requested; graceful terminate issued."


def toggle_verbose(pid: int, verbose: bool) -> str:
    proc = _resolve_process(pid)
    if hasattr(signal, "SIGUSR1"):
        proc.send_signal(signal.SIGUSR1)
        return "Toggled verbose mode via SIGUSR1." if not verbose else "Requested verbose disable via SIGUSR1."
    proc.terminate()
    return "SIGUSR1 unavailable; terminated process to refresh logging."


def clear_logs(log_file: Path) -> str:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8"):
        pass
    return f"Cleared log file at {log_file}"


def restart_process(pid: int, restart_cmd: Optional[str]) -> tuple[str, Optional[int]]:
    proc = _resolve_process(pid)
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except psutil.TimeoutExpired:
        proc.kill()

    if not restart_cmd:
        return "Process stopped; no restart command configured.", None

    command = shlex.split(restart_cmd)
    restarted = subprocess.Popen(command, start_new_session=True)
    time.sleep(0.5)
    return f"Restarted process via: {restart_cmd}", restarted.pid
