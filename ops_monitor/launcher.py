from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _resolve_python() -> str:
    return os.getenv("PYTHON", sys.executable or "python")


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

    log_file_path = Path(log_file)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
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
