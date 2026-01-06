from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import psutil


@dataclass
class ProcessMetrics:
    status: str
    uptime: float
    cpu_percent: float
    memory_mb: float
    thread_count: int
    connections: int


@dataclass
class PortStatus:
    port: int
    reachable: bool
    last_checked: float


@dataclass
class LogSnapshot:
    lines: list[str]
    path: Path


def read_process_metrics(pid: int) -> Optional[ProcessMetrics]:
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            status = proc.status()
            uptime = time.time() - proc.create_time()
            cpu_percent = proc.cpu_percent(interval=None)
            memory_mb = proc.memory_info().rss / (1024 * 1024)
            thread_count = proc.num_threads()
            connections = len(proc.connections())
        return ProcessMetrics(
            status=status,
            uptime=uptime,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            thread_count=thread_count,
            connections=connections,
        )
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def check_port(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> PortStatus:
    reachable = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            reachable = True
        except OSError:
            reachable = False
    return PortStatus(port=port, reachable=reachable, last_checked=time.time())


def tail_log(path: Path, max_lines: int = 18, state: dict[str, int] | None = None) -> LogSnapshot:
    """Read the last *max_lines* from *path* while maintaining cursor state."""

    cursor = state or {}
    position = cursor.get("position", 0)
    lines: list[str] = []

    if not path.exists():
        return LogSnapshot(lines=["Log file not found: " + str(path)], path=path)

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(0, 2)
            end_position = fh.tell()
            if end_position < position:
                position = 0
            seek_to = max(end_position - 8192, 0)
            fh.seek(seek_to)
            buffer = fh.read()
            lines = buffer.splitlines()[-max_lines:]
            cursor["position"] = end_position
    except OSError as exc:
        lines = [f"Unable to read log: {exc}"]

    return LogSnapshot(lines=lines, path=path)


def format_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def summarize_connections(connections: Iterable[psutil._common.sconn], port: int) -> int:
    count = 0
    for conn in connections:
        if conn.laddr and conn.laddr.port == port:
            count += 1
    return count
