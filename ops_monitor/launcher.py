from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _resolve_python() -> str:
    return os.getenv("PYTHON", sys.executable or "python")


def _monitor_command(
    target_pid: int,
    app_port: int,
    log_file: Path,
    restart_cmd: Optional[str],
    service_name: str,
) -> list[str]:
    monitor_module = "ops_monitor.monitor"
    python_exe = _resolve_python()
    args = [
        python_exe,
        "-m",
        monitor_module,
        f"--target-pid={target_pid}",
        f"--app-port={app_port}",
        f"--service-name={service_name}",
        f"--log-file={log_file}",
    ]
    if restart_cmd:
        args.append(f"--restart-cmd={restart_cmd}")
    return args


def _safe_popen(args: list[str], env: dict[str, str], use_shell: bool = False) -> bool:
    try:
        subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=use_shell,
        )
        return True
    except OSError:
        return False


def _launch_in_terminal(command: list[str]) -> bool:
    launch_mode = os.getenv("OPS_MONITOR_LAUNCH_MODE", "window").lower()
    if launch_mode in {"background", "headless"}:
        return False

    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", str(Path(__file__).resolve().parent.parent))}

    if sys.platform.startswith("win"):
        command_line = subprocess.list2cmdline(command)
        return _safe_popen(
            ["cmd.exe", "/c", "start", "Hyperion Ops Monitor", command_line],
            env=env,
        )

    if sys.platform == "darwin":
        command_line = shlex.join(command)
        return _safe_popen(
            [
                "osascript",
                "-e",
                f'tell application "Terminal" to do script "{command_line}"',
            ],
            env=env,
        )

    if not os.getenv("DISPLAY"):
        return False

    terminal_overrides = [os.getenv("OPS_MONITOR_TERMINAL")] if os.getenv("OPS_MONITOR_TERMINAL") else []
    candidates = terminal_overrides + [
        "x-terminal-emulator",
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "xterm",
    ]

    for terminal in candidates:
        if not terminal:
            continue
        if shutil.which(terminal) is None:
            continue
        if terminal == "gnome-terminal":
            return _safe_popen([terminal, "--", *command], env=env)
        if terminal == "konsole":
            return _safe_popen([terminal, "-e", *command], env=env)
        if terminal == "xfce4-terminal":
            return _safe_popen([terminal, "--command", shlex.join(command)], env=env)
        if terminal in {"xterm", "x-terminal-emulator"}:
            return _safe_popen([terminal, "-e", *command], env=env)

    return False


def launch_monitor_process(
    target_pid: int,
    app_port: int,
    log_file: Path,
    restart_cmd: Optional[str] = None,
    service_name: str = "Hyperion Operations Hub",
) -> None:
    """Launch the operations monitor as a detached subprocess."""

    if os.getenv("ENABLE_OPS_MONITOR", "1") == "0":
        return

    args = _monitor_command(
        target_pid=target_pid,
        app_port=app_port,
        log_file=log_file,
        restart_cmd=restart_cmd,
        service_name=service_name,
    )

    log_file_path = Path(log_file)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if _launch_in_terminal(args):
            return
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", str(Path(__file__).resolve().parent.parent))},
        )
    except OSError:
        pass


def parse_restart_cmd(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    if raw.strip().startswith("["):
        try:
            import json

            parts = json.loads(raw)
            return " ".join(shlex.quote(part) for part in parts)
        except Exception:
            return raw
    return raw


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Launch the Hyperion operations monitor")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--app-port", type=int, default=8000)
    parser.add_argument("--log-file", type=Path, default=Path("support/operations.log"))
    parser.add_argument("--restart-cmd", type=str, default=None)
    parser.add_argument("--service-name", type=str, default="Hyperion Operations Hub")

    args = parser.parse_args()
    restart_cmd = parse_restart_cmd(args.restart_cmd)
    launch_monitor_process(
        target_pid=args.target_pid,
        app_port=args.app_port,
        log_file=args.log_file,
        restart_cmd=restart_cmd,
        service_name=args.service_name,
    )
