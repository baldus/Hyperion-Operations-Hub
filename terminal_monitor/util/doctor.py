from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from terminal_monitor.panels.network import read_network_status


def run_doctor(log_path: Path) -> str:
    lines = []
    lines.append("Hyperion Terminal Monitor Doctor")
    lines.append("=")
    lines.append(f"python: {sys.version.replace(chr(10), ' ')}")
    lines.append(f"platform: {platform.platform()}")
    lines.append(f"term: {os.environ.get('TERM', '')}")
    lines.append(f"isatty stdin: {sys.stdin.isatty()}")
    lines.append(f"isatty stdout: {sys.stdout.isatty()}")
    lines.append(f"cwd: {Path.cwd()}")
    lines.append(f"log_path: {log_path}")
    log_writable = _can_write(log_path)
    lines.append(f"log_writable: {log_writable}")
    lines.append(f"network_status_path: /var/lib/hyperion/network_status.txt")
    lines.append(f"network_status: {read_network_status().raw}")
    lines.append("textual: " + _import_status("textual"))
    lines.append("rich: " + _import_status("rich"))
    return "\n".join(lines)


def _can_write(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            return True
    except OSError:
        return False


def _import_status(module: str) -> str:
    try:
        __import__(module)
        return "available"
    except Exception as exc:
        return f"unavailable ({exc.__class__.__name__})"
