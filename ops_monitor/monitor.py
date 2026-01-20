from __future__ import annotations

import argparse
import os
import queue
import shlex
import sys
import threading
import time
import subprocess
from datetime import datetime
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
    ConnectivityStatus,
    OpsEventEntry,
    check_port,
    format_uptime,
    mask_db_url,
    ping_host,
    read_backup_status,
    read_ops_events,
    read_process_metrics,
    read_recent_access,
    read_recent_errors,
    read_boot_status,
    read_sequence_repair_summary,
    summarize_connections,
    tail_log,
)


console = Console()


class NetworkWatchdog:
    def __init__(
        self,
        *,
        host: str,
        interval: float,
        timeout: int,
        restart_cooldown: float,
    ) -> None:
        self._host = host
        self._interval = interval
        self._timeout = timeout
        self._restart_cooldown = restart_cooldown
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._events: list[OpsEventEntry] = []
        self._last_restart: datetime | None = None
        self._status = ConnectivityStatus(
            online=True,
            last_seen=None,
            last_checked=None,
            last_failure=None,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1)

    def snapshot(self) -> tuple[ConnectivityStatus, list[OpsEventEntry]]:
        with self._lock:
            status = ConnectivityStatus(
                online=self._status.online,
                last_seen=self._status.last_seen,
                last_checked=self._status.last_checked,
                last_failure=self._status.last_failure,
            )
            events = list(self._events)
        return status, events

    def _run(self) -> None:
        was_online = True
        while not self._stop_event.is_set():
            now = datetime.utcnow()
            online = ping_host(self._host, timeout=self._timeout)
            restart_needed = False

            with self._lock:
                self._status.last_checked = now
                if online:
                    self._status.online = True
                    self._status.last_seen = now
                    self._status.last_failure = None
                else:
                    self._status.online = False
                    if was_online:
                        self._status.last_failure = now
                        self._record_event(now, "WARNING: INTERNET DISCONNECTED")
                        restart_needed = self._restart_due(now)

            if not online and restart_needed:
                self._restart_network_manager()

            was_online = online
            self._stop_event.wait(self._interval)

    def _record_event(self, timestamp: datetime, message: str) -> None:
        self._events.append(
            OpsEventEntry(
                created_at=timestamp,
                level="WARNING",
                message=message,
                source="network_watchdog",
                context=None,
            )
        )
        if len(self._events) > 25:
            self._events = self._events[-25:]

    def _restart_due(self, now: datetime) -> bool:
        if self._restart_cooldown <= 0:
            return True
        if not self._last_restart:
            self._last_restart = now
            return True
        elapsed = (now - self._last_restart).total_seconds()
        if elapsed >= self._restart_cooldown:
            self._last_restart = now
            return True
        return False

    def _restart_network_manager(self) -> None:
        try:
            subprocess.run(
                ["systemctl", "restart", "NetworkManager"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return


def _input_listener(command_queue: "queue.Queue[str]", stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            ch = console.input("[bold cyan]ops> ").strip().lower()
            if ch:
                command_queue.put(ch[0])
        except (EOFError, KeyboardInterrupt):
            stop_event.set()
            break


def build_header(service_name: str, status_text: str) -> Panel:
    header = Text(service_name, style="bold white")
    header.append(" \u2022 ")
    header.append(status_text, style="green" if "Running" in status_text else "red")
    return Panel(header, padding=(1, 1), box=box.ROUNDED)


def build_metrics_table(state: dict) -> Table:
    table = Table(box=box.SIMPLE, expand=True, show_header=False)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="white")

    internet_status = state.get("internet_status")
    if isinstance(internet_status, ConnectivityStatus):
        if internet_status.online:
            internet_value = Text("ONLINE", style="bold green")
        else:
            internet_value = Text("WARNING: INTERNET DISCONNECTED", style="bold red")
        last_seen = (
            internet_status.last_seen.strftime("%Y-%m-%d %H:%M:%S UTC")
            if internet_status.last_seen
            else "never"
        )
    else:
        internet_value = Text("unknown", style="yellow")
        last_seen = "n/a"

    table.add_row("Status", state.get("status", "Unknown"))
    table.add_row("Uptime", state.get("uptime", "00:00:00"))
    table.add_row("CPU", f"{state.get('cpu', 0):.1f}%")
    table.add_row("Memory", f"{state.get('memory', 0):.1f} MB")
    table.add_row("Threads", str(state.get("threads", 0)))
    table.add_row("Port", state.get("port_status", "n/a"))
    table.add_row("Clients", str(state.get("connections", 0)))
    table.add_row("Internet", internet_value)
    table.add_row("Last seen", last_seen)
    return table


def build_log_panel(log_lines: list[str], path: Path) -> Panel:
    rendered = "\n".join(log_lines) if log_lines else "(no log entries yet)"
    return Panel(rendered, title=f"Log tail — {path}", box=box.ROUNDED, padding=(1, 1))


def build_access_panel(access_snapshot: AccessSnapshot) -> Panel:
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
    return Panel(rendered, title=access_snapshot.status, box=box.ROUNDED, padding=(1, 1))


def build_controls_panel(verbose: bool) -> Panel:
    lines = [
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


def build_backup_panel(status: BackupStatus) -> Panel:
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
    return Panel("\n".join(lines), title="Backup Status", box=box.ROUNDED, padding=(1, 1))


def build_events_panel(events: list) -> Panel:
    if not events:
        return Panel("(no recent warnings/errors)", title="Events", box=box.ROUNDED, padding=(1, 1))
    lines = []
    for event in events:
        timestamp = event.created_at.strftime("%H:%M:%S") if event.created_at else "--:--:--"
        level = event.level
        message = event.message
        lines.append(f"[{level}] {timestamp} {message}")
    return Panel("\n".join(lines), title="Recent Events", box=box.ROUNDED, padding=(1, 1))


def build_health_panel(state: dict) -> Panel:
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
    return Panel("\n".join(lines), title="Health", box=box.ROUNDED, padding=(1, 1))


def build_errors_panel(error_snapshot) -> Panel:
    lines = error_snapshot.entries or ["(no recent exceptions)"]
    return Panel("\n".join(lines), title=error_snapshot.status, box=box.ROUNDED, padding=(1, 1))


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
    layout["metrics"].update(build_metrics_table(state))
    layout["backup"].update(build_backup_panel(state.get("backup_status")))
    layout["health"].update(build_health_panel(state))
    layout["access"].update(build_access_panel(state.get("access_snapshot", AccessSnapshot([], [], "Access"))))
    layout["logs"].update(build_log_panel(state.get("log_lines", []), state.get("log_path", Path("log"))))
    layout["events"].update(build_events_panel(state.get("events", [])))
    layout["errors"].update(build_errors_panel(state.get("error_snapshot")))
    layout["footer"].update(build_controls_panel(state.get("verbose", False)))
    return layout


def monitor_loop(
    target_pid: int,
    restart_cmd: Optional[str],
    log_file: Path,
    app_port: int,
    service_name: str,
) -> None:
    command_queue: "queue.Queue[str]" = queue.Queue()
    stop_event = threading.Event()

    input_thread = threading.Thread(target=_input_listener, args=(command_queue, stop_event), daemon=True)
    input_thread.start()

    log_cursor: dict[str, int] = {}
    status_message = "System Online"
    verbose = False
    tracked_pid = target_pid
    db_url = os.getenv("OPS_MONITOR_DB_URL") or os.getenv("DB_URL")
    db_url_masked = mask_db_url(db_url)
    gunicorn_bind = f"{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '8000')}"
    gunicorn_workers = os.getenv("GUNICORN_WORKERS", "2")
    gunicorn_timeout = os.getenv("GUNICORN_TIMEOUT", "600")
    internet_host = os.getenv("OPS_MONITOR_CONNECTIVITY_HOST", "1.1.1.1")
    internet_interval = float(os.getenv("OPS_MONITOR_CONNECTIVITY_INTERVAL", "10"))
    internet_timeout = int(os.getenv("OPS_MONITOR_CONNECTIVITY_TIMEOUT", "1"))
    internet_restart_cooldown = float(os.getenv("OPS_MONITOR_CONNECTIVITY_RESTART_COOLDOWN", "60"))

    watchdog = NetworkWatchdog(
        host=internet_host,
        interval=internet_interval,
        timeout=internet_timeout,
        restart_cooldown=internet_restart_cooldown,
    )
    watchdog.start()

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
        "internet_status": ConnectivityStatus(
            online=True,
            last_seen=None,
            last_checked=None,
            last_failure=None,
        ),
    }

    with Live(render_layout(live_state), console=console, screen=True, refresh_per_second=4) as live:
        while not stop_event.is_set():
            metrics = read_process_metrics(tracked_pid)
            port_status = check_port(app_port)
            log_snapshot = tail_log(log_file, state=log_cursor)
            access_snapshot = read_recent_access(db_url)
            backup_status = read_backup_status(db_url)
            events = read_ops_events(db_url)
            error_snapshot = read_recent_errors(db_url)
            sequence_summary = read_sequence_repair_summary(db_url)
            boot_status = read_boot_status(db_url)
            internet_status, network_events = watchdog.snapshot()

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

            live_state = {
                "service_name": service_name,
                "status": f"{status} — {status_message}",
                "uptime": uptime,
                "cpu": cpu,
                "memory": memory,
                "threads": threads,
                "port_status": "online" if port_status.reachable else "offline",
                "connections": connections,
                "log_lines": log_snapshot.lines,
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
                "internet_status": internet_status,
            }

            if network_events:
                events = [*network_events, *events]

            live.update(render_layout(live_state))

            try:
                command = command_queue.get_nowait()
            except queue.Empty:
                command = None

            if command:
                if command == "q":
                    stop_event.set()
                    status_message = "Exiting monitor"
                elif command == "r":
                    status_message, new_pid = controls.restart_process(tracked_pid, restart_cmd)
                    if new_pid:
                        tracked_pid = new_pid
                elif command == "s":
                    status_message = controls.graceful_shutdown(tracked_pid)
                elif command == "k":
                    status_message = controls.force_kill(tracked_pid)
                elif command == "u":
                    status_message = controls.reload_config(tracked_pid)
                elif command == "c":
                    status_message = controls.clear_logs(log_file)
                elif command == "v":
                    verbose = not verbose
                    status_message = controls.toggle_verbose(tracked_pid, verbose)

            if metrics is None and not restart_cmd:
                stop_event.set()

            time.sleep(0.5)

    watchdog.stop()
    stop_event.set()
    input_thread.join(timeout=1)


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
