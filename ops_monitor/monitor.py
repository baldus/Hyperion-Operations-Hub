from __future__ import annotations

import argparse
import fcntl
import logging
import os
import shlex
import signal
import sys
import termios
import threading
import time
import tty
from pathlib import Path
from typing import Optional

import psutil
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import controls
from .metrics import (
    AccessSnapshot,
    BackupStatus,
    NetworkStatus,
    check_port,
    format_uptime,
    mask_db_url,
    read_backup_status,
    read_log_lines,
    read_network_status,
    read_ops_events,
    read_process_metrics,
    read_recent_access,
    read_recent_errors,
    read_boot_status,
    read_sequence_repair_summary,
    summarize_connections,
)


console = Console()


class TerminalInput:
    def __init__(self) -> None:
        self._fd = sys.stdin.fileno()
        self._original_attrs = termios.tcgetattr(self._fd)
        self._original_flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
        self._buffer = ""

    def __enter__(self) -> "TerminalInput":
        tty.setcbreak(self._fd)
        fcntl.fcntl(self._fd, fcntl.F_SETFL, self._original_flags | os.O_NONBLOCK)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._original_attrs)
        fcntl.fcntl(self._fd, fcntl.F_SETFL, self._original_flags)

    def _parse_buffer(self) -> list[str]:
        keys: list[str] = []
        while self._buffer:
            if self._buffer[0] != "\x1b":
                ch = self._buffer[0]
                self._buffer = self._buffer[1:]
                if ch in ("\r", "\n"):
                    keys.append("enter")
                elif ch == "\t":
                    keys.append("tab")
                elif ch == "q":
                    keys.append("q")
                elif ch == "f":
                    keys.append("f")
                elif ch == "j":
                    keys.append("down")
                elif ch == "k":
                    keys.append("up")
                elif ch in {"r", "s", "u", "c", "v"}:
                    keys.append(ch)
                continue

            sequences = {
                "\x1b[A": "up",
                "\x1b[B": "down",
                "\x1b[C": "right",
                "\x1b[D": "left",
                "\x1b[5~": "pgup",
                "\x1b[6~": "pgdn",
                "\x1b[H": "home",
                "\x1b[F": "end",
                "\x1b[1~": "home",
                "\x1b[4~": "end",
                "\x1b[7~": "home",
                "\x1b[8~": "end",
                "\x1b[Z": "backtab",
            }

            matched = False
            for seq, name in sequences.items():
                if self._buffer.startswith(seq):
                    keys.append(name)
                    self._buffer = self._buffer[len(seq) :]
                    matched = True
                    break

            if matched:
                continue

            if self._buffer == "\x1b":
                keys.append("esc")
                self._buffer = ""
                break

            if self._buffer.startswith("\x1b") and len(self._buffer) < 3:
                break

            self._buffer = self._buffer[1:]
        return keys

    def read_keys(self) -> list[str]:
        keys: list[str] = []
        while True:
            try:
                chunk = os.read(self._fd, 64)
            except BlockingIOError:
                break
            if not chunk:
                break
            self._buffer += chunk.decode(errors="ignore")
        if self._buffer:
            keys.extend(self._parse_buffer())
        return keys


def configure_terminal_logger() -> logging.Logger:
    logger = logging.getLogger("ops_monitor")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_path = Path("/var/log/hyperion_terminal_display.log")
    try:
        handler = logging.FileHandler(log_path)
    except OSError:
        log_path = Path("support/hyperion_terminal_display.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.info("terminal display started")
    return logger


def build_header(service_name: str, status_text: str) -> Panel:
    header = Text(service_name, style="bold white")
    header.append(" \u2022 ")
    header.append(status_text, style="green" if "Running" in status_text else "red")
    return Panel(header, padding=(1, 1), box=box.ROUNDED)


def build_metrics_table(state: dict) -> Table:
    table = Table(box=box.SIMPLE, expand=True, show_header=False)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="white")

    network_status = state.get("network_status")
    if isinstance(network_status, NetworkStatus):
        if network_status.status.startswith("OFFLINE"):
            network_value = Text(f"!!! {network_status.raw}", style="bold red")
        elif network_status.status.startswith("ONLINE"):
            network_value = Text(network_status.raw, style="bold green")
        else:
            network_value = Text(network_status.raw, style="yellow")
    else:
        network_value = Text("UNKNOWN | network watchdog not running", style="yellow")

    table.add_row("Status", state.get("status", "Unknown"))
    table.add_row("Uptime", state.get("uptime", "00:00:00"))
    table.add_row("CPU", f"{state.get('cpu', 0):.1f}%")
    table.add_row("Memory", f"{state.get('memory', 0):.1f} MB")
    table.add_row("Threads", str(state.get("threads", 0)))
    table.add_row("Port", state.get("port_status", "n/a"))
    table.add_row("Clients", str(state.get("connections", 0)))
    table.add_row("Network", network_value)
    return table


def build_metrics_panel(state: dict, focused: bool) -> Panel:
    title_style = "bold yellow" if focused else None
    return Panel(
        build_metrics_table(state),
        title="System Health",
        title_align="left",
        title_style=title_style,
        box=box.ROUNDED,
        padding=(1, 1),
    )


def build_log_panel(
    log_lines: list[str],
    path: Path,
    *,
    follow: bool,
    scroll_index: int,
    window_size: int,
    focused: bool,
) -> Panel:
    total = len(log_lines)
    if total > window_size:
        start = max(total - window_size, 0) if follow else min(scroll_index, total - window_size)
    else:
        start = 0
    windowed = log_lines[start : start + window_size]
    rendered = "\n".join(windowed) if windowed else "(no log entries yet)"
    title = f"Log tail — {path}"
    if total > window_size:
        if follow:
            title = f"{title} (follow)"
        else:
            end_index = min(start + window_size, total)
            title = f"{title} (paused {start + 1}-{end_index}/{total})"
    title_style = "bold yellow" if focused else None
    return Panel(rendered, title=title, title_style=title_style, box=box.ROUNDED, padding=(1, 1))


def build_access_panel(access_snapshot: AccessSnapshot, focused: bool) -> Panel:
    users = access_snapshot.users or ["(no recent users)"]
    pages = access_snapshot.pages or ["(no recent page activity)"]
    rendered = "\n".join(
        [
            "[b]Connected users (last 5m)[/b]",
            *[f"- {user}" for user in users],
            "",
            "[b]Recent pages[/b]",
            *[f"- {page}" for page in pages],
        ]
    )
    title_style = "bold yellow" if focused else None
    return Panel(rendered, title=access_snapshot.status, title_style=title_style, box=box.ROUNDED, padding=(1, 1))


def build_controls_panel(verbose: bool) -> Panel:
    lines = [
        "[b]tab[/b] Next panel  [b]shift+tab[/b] Previous panel",
        "[b]↑/↓[/b] or [b]j/k[/b] Move focus  [b]enter[/b] Select  [b]f[/b] Follow log",
        "[b]pgup/pgdn[/b] Scroll log  [b]home/end[/b] Jump",
        "[b]r[/b] Restart application",
        "[b]s[/b] Graceful shutdown",
        "[b]k[/b] Force kill",
        "[b]u[/b] Reload configuration",
        "[b]c[/b] Clear logs",
        "[b]v[/b] Toggle verbose logging (current: {state})".format(
            state="on" if verbose else "off"
        ),
        "[b]q[/b] Quit monitor",
    ]
    return Panel("\n".join(lines), title="Controls", box=box.ROUNDED, padding=(1, 1))


def build_backup_panel(status: BackupStatus, focused: bool) -> Panel:
    restore_state = "n/a"
    if status.restore_last_status:
        restore_state = "running" if status.restore_last_status == "started" else status.restore_last_status
    lines = [
        f"[b]Frequency[/b]: {status.frequency_hours}h ({status.frequency_source})",
        f"[b]Last run[/b]: {status.last_run_at.strftime('%Y-%m-%d %H:%M UTC') if status.last_run_at else 'n/a'}",
        f"[b]Last status[/b]: {status.last_run_status or 'n/a'}",
        f"[b]Last message[/b]: {status.last_run_message or 'n/a'}",
        f"[b]Last file[/b]: {status.last_run_filename or 'n/a'}",
        f"[b]Last path[/b]: {status.last_run_filepath or 'n/a'}",
        f"[b]Last success[/b]: {status.last_success_at.strftime('%Y-%m-%d %H:%M UTC') if status.last_success_at else 'n/a'}",
        f"[b]Next run[/b]: {status.next_run_at.strftime('%Y-%m-%d %H:%M UTC') if status.next_run_at else 'n/a'}",
        f"[b]Restore state[/b]: {restore_state}",
        f"[b]Restore time[/b]: {status.restore_last_at.strftime('%Y-%m-%d %H:%M UTC') if status.restore_last_at else 'n/a'}",
        f"[b]Restore file[/b]: {status.restore_last_filename or 'n/a'}",
        f"[b]Restore by[/b]: {status.restore_last_username or 'n/a'}",
        f"[b]Restore message[/b]: {status.restore_last_message or 'n/a'}",
    ]
    title_style = "bold yellow" if focused else None
    return Panel("\n".join(lines), title="Backup Status", title_style=title_style, box=box.ROUNDED, padding=(1, 1))


def build_events_panel(events: list, focused: bool) -> Panel:
    if not events:
        title_style = "bold yellow" if focused else None
        return Panel("(no recent warnings/errors)", title="Events", title_style=title_style, box=box.ROUNDED, padding=(1, 1))
    lines = []
    for event in events:
        timestamp = event.created_at.strftime("%H:%M:%S") if event.created_at else "--:--:--"
        level = event.level
        message = event.message
        lines.append(f"[{level}] {timestamp} {message}")
    title_style = "bold yellow" if focused else None
    return Panel("\n".join(lines), title="Recent Events", title_style=title_style, box=box.ROUNDED, padding=(1, 1))


def build_health_panel(state: dict, focused: bool) -> Panel:
    lines = [
        f"[b]DB_URL[/b]: {state.get('db_url_masked', 'n/a')}",
        f"[b]Gunicorn bind[/b]: {state.get('gunicorn_bind', 'n/a')}",
        f"[b]Workers[/b]: {state.get('gunicorn_workers', 'n/a')}",
        f"[b]Timeout[/b]: {state.get('gunicorn_timeout', 'n/a')}",
        f"[b]Boot[/b]: {state.get('boot_status', 'n/a')}",
    ]

    summary = state.get("sequence_summary")
    if summary:
        lines.append(
            "[b]Sequence repair[/b]: "
            f"{summary.get('repaired', 0)} repaired, "
            f"{summary.get('skipped', 0)} skipped, "
            f"{summary.get('failed', 0)} failed"
        )
    title_style = "bold yellow" if focused else None
    return Panel("\n".join(lines), title="Health", title_style=title_style, box=box.ROUNDED, padding=(1, 1))


def build_errors_panel(error_snapshot, focused: bool) -> Panel:
    lines = error_snapshot.entries or ["(no recent exceptions)"]
    title_style = "bold yellow" if focused else None
    return Panel("\n".join(lines), title=error_snapshot.status, title_style=title_style, box=box.ROUNDED, padding=(1, 1))


def render_layout(state: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=7),
    )
    layout["body"].split_row(Layout(name="left", ratio=1), Layout(name="right", ratio=2))
    layout["left"].split_column(
        Layout(name="metrics"),
        Layout(name="backup"),
        Layout(name="health"),
        Layout(name="access", size=12),
    )
    layout["right"].split_column(Layout(name="logs"), Layout(name="events"), Layout(name="errors", size=8))

    layout["header"].update(build_header(state.get("service_name", "Operations"), state.get("status", "Unknown")))
    focused_panel = state.get("focused_panel")
    layout["metrics"].update(build_metrics_panel(state, focused_panel == "metrics"))
    layout["backup"].update(build_backup_panel(state.get("backup_status"), focused_panel == "backup"))
    layout["health"].update(build_health_panel(state, focused_panel == "health"))
    layout["access"].update(
        build_access_panel(state.get("access_snapshot", AccessSnapshot([], [], "Access")), focused_panel == "access")
    )
    layout["logs"].update(
        build_log_panel(
            state.get("log_lines", []),
            state.get("log_path", Path("log")),
            follow=state.get("log_follow", True),
            scroll_index=state.get("log_scroll", 0),
            window_size=state.get("log_window", 18),
            focused=focused_panel == "logs",
        )
    )
    layout["events"].update(build_events_panel(state.get("events", []), focused_panel == "events"))
    layout["errors"].update(build_errors_panel(state.get("error_snapshot"), focused_panel == "errors"))
    layout["footer"].update(build_controls_panel(state.get("verbose", False)))
    return layout


def monitor_loop(
    target_pid: int,
    restart_cmd: Optional[str],
    log_file: Path,
    app_port: int,
    service_name: str,
) -> None:
    stop_event = threading.Event()
    logger = configure_terminal_logger()
    debug_input = os.getenv("OPS_MONITOR_DEBUG", "0") == "1"
    refresh_interval = float(os.getenv("OPS_MONITOR_REFRESH_INTERVAL", "0.5"))
    log_max_lines = int(os.getenv("OPS_MONITOR_LOG_MAX_LINES", "200"))
    log_window = int(os.getenv("OPS_MONITOR_LOG_WINDOW", "18"))
    log_follow = True
    log_scroll = 0
    focused_panels = ["metrics", "logs", "events", "errors", "backup", "health", "access"]
    focus_index = 0
    resize_pending = False

    def handle_resize(signum, frame):
        nonlocal resize_pending
        resize_pending = True

    signal.signal(signal.SIGWINCH, handle_resize)
    status_message = "System Online"
    verbose = False
    tracked_pid = target_pid
    db_url = os.getenv("OPS_MONITOR_DB_URL") or os.getenv("DB_URL")
    db_url_masked = mask_db_url(db_url)
    gunicorn_bind = f"{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '8000')}"
    gunicorn_workers = os.getenv("GUNICORN_WORKERS", "2")
    gunicorn_timeout = os.getenv("GUNICORN_TIMEOUT", "600")
    live_state = {
        "service_name": service_name,
        "status": "Starting",
        "uptime": "00:00:00",
        "cpu": 0,
        "memory": 0,
        "threads": 0,
        "port_status": "initializing",
        "connections": 0,
        "log_lines": [],
        "log_path": log_file,
        "verbose": verbose,
        "access_snapshot": AccessSnapshot([], [], "Access"),
        "backup_status": BackupStatus(
            frequency_hours=4,
            frequency_source="default",
            last_run_at=None,
            last_run_status=None,
            last_run_message=None,
            last_run_filename=None,
            last_run_filepath=None,
            last_success_at=None,
            next_run_at=None,
            restore_last_at=None,
            restore_last_status=None,
            restore_last_filename=None,
            restore_last_message=None,
            restore_last_username=None,
        ),
        "events": [],
        "error_snapshot": read_recent_errors(db_url),
        "db_url_masked": db_url_masked,
        "gunicorn_bind": gunicorn_bind,
        "gunicorn_workers": gunicorn_workers,
        "gunicorn_timeout": gunicorn_timeout,
        "boot_status": "Unknown",
        "sequence_summary": None,
        "network_status": read_network_status(),
        "log_follow": log_follow,
        "log_scroll": log_scroll,
        "log_window": log_window,
        "focused_panel": focused_panels[focus_index],
    }

    with TerminalInput() as terminal_input, Live(
        render_layout(live_state),
        console=console,
        screen=True,
        refresh_per_second=4,
        auto_refresh=False,
    ) as live:
        last_render = 0.0
        while not stop_event.is_set():
            metrics = read_process_metrics(tracked_pid)
            port_status = check_port(app_port)
            log_snapshot = read_log_lines(log_file, max_lines=log_max_lines)
            access_snapshot = read_recent_access(db_url)
            backup_status = read_backup_status(db_url)
            events = read_ops_events(db_url)
            error_snapshot = read_recent_errors(db_url)
            sequence_summary = read_sequence_repair_summary(db_url)
            boot_status = read_boot_status(db_url)
            network_status = read_network_status()
            log_lines = log_snapshot.lines
            total_lines = len(log_lines)
            max_scroll = max(total_lines - log_window, 0)
            if log_follow:
                log_scroll = max_scroll
            else:
                log_scroll = max(0, min(log_scroll, max_scroll))

            if metrics is None:
                status = "Stopped"
                uptime = "00:00:00"
                cpu = memory = threads = connections = 0
                if restart_cmd:
                    status_message = "App offline; press r to restart."
                else:
                    status_message = "App offline"
            else:
                status = "Running"
                uptime = format_uptime(metrics.uptime)
                cpu = metrics.cpu_percent
                memory = metrics.memory_mb
                threads = metrics.thread_count
                try:
                    connections = summarize_connections(psutil.net_connections(), app_port)
                except Exception:
                    connections = metrics.connections
            for key in terminal_input.read_keys():
                if debug_input:
                    logger.info("key pressed: %s", key)
                if key in {"q", "esc"}:
                    stop_event.set()
                    status_message = "Exiting monitor"
                elif key in {"tab", "right", "down"}:
                    focus_index = (focus_index + 1) % len(focused_panels)
                elif key in {"backtab", "left", "up"}:
                    focus_index = (focus_index - 1) % len(focused_panels)
                elif key == "enter" and focused_panels[focus_index] == "logs":
                    log_follow = not log_follow
                elif key == "f" and focused_panels[focus_index] == "logs":
                    log_follow = not log_follow
                elif key in {"pgup", "pgdn", "home", "end"} and focused_panels[focus_index] == "logs":
                    log_follow = False
                    if key == "pgup":
                        log_scroll = max(log_scroll - log_window, 0)
                    elif key == "pgdn":
                        log_scroll = min(log_scroll + log_window, max_scroll)
                    elif key == "home":
                        log_scroll = 0
                    elif key == "end":
                        log_scroll = max_scroll
                elif key == "r":
                    status_message, new_pid = controls.restart_process(tracked_pid, restart_cmd)
                    if new_pid:
                        tracked_pid = new_pid
                elif key == "s":
                    status_message = controls.graceful_shutdown(tracked_pid)
                elif key == "k":
                    status_message = controls.force_kill(tracked_pid)
                elif key == "u":
                    status_message = controls.reload_config(tracked_pid)
                elif key == "c":
                    status_message = controls.clear_logs(log_file)
                elif key == "v":
                    verbose = not verbose
                    status_message = controls.toggle_verbose(tracked_pid, verbose)

            live_state = {
                "service_name": service_name,
                "status": f"{status} — {status_message}",
                "uptime": uptime,
                "cpu": cpu,
                "memory": memory,
                "threads": threads,
                "port_status": "online" if port_status.reachable else "offline",
                "connections": connections,
                "log_lines": log_lines,
                "log_path": log_snapshot.path,
                "verbose": verbose,
                "access_snapshot": access_snapshot,
                "backup_status": backup_status,
                "events": events,
                "error_snapshot": error_snapshot,
                "db_url_masked": db_url_masked,
                "gunicorn_bind": gunicorn_bind,
                "gunicorn_workers": gunicorn_workers,
                "gunicorn_timeout": gunicorn_timeout,
                "boot_status": boot_status or status_message,
                "sequence_summary": sequence_summary,
                "network_status": network_status,
                "log_follow": log_follow,
                "log_scroll": log_scroll,
                "log_window": log_window,
                "focused_panel": focused_panels[focus_index],
            }

            if metrics is None and not restart_cmd:
                stop_event.set()
            now = time.monotonic()
            if now - last_render >= refresh_interval or resize_pending:
                live.update(render_layout(live_state))
                live.refresh()
                last_render = now
                resize_pending = False

            time.sleep(0.05)

    stop_event.set()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operations console for Hyperion Operations Hub")
    parser.add_argument("--target-pid", type=int, required=True, help="PID of the application to monitor")
    parser.add_argument("--app-port", type=int, default=int(os.getenv("PORT", 8000)), help="Application port to probe")
    parser.add_argument("--restart-cmd", type=str, default=None, help="Command used to restart the app")
    parser.add_argument("--log-file", type=Path, default=Path("support/operations.log"), help="Log file to tail")
    parser.add_argument("--service-name", type=str, default="Hyperion Operations Hub")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    restart_cmd = args.restart_cmd
    if restart_cmd:
        restart_cmd = shlex.split(restart_cmd)
        restart_cmd = " ".join(shlex.quote(part) for part in restart_cmd)

    monitor_loop(
        target_pid=args.target_pid,
        restart_cmd=restart_cmd,
        log_file=args.log_file,
        app_port=args.app_port,
        service_name=args.service_name,
    )


if __name__ == "__main__":
    main()
