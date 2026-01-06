from __future__ import annotations

import argparse
import os
import queue
import shlex
import sys
import threading
import time
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
    check_port,
    format_uptime,
    read_process_metrics,
    summarize_connections,
    tail_log,
)


console = Console()


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

    table.add_row("Status", state.get("status", "Unknown"))
    table.add_row("Uptime", state.get("uptime", "00:00:00"))
    table.add_row("CPU", f"{state.get('cpu', 0):.1f}%")
    table.add_row("Memory", f"{state.get('memory', 0):.1f} MB")
    table.add_row("Threads", str(state.get("threads", 0)))
    table.add_row("Port", state.get("port_status", "n/a"))
    table.add_row("Clients", str(state.get("connections", 0)))
    return table


def build_log_panel(log_lines: list[str], path: Path) -> Panel:
    rendered = "\n".join(log_lines) if log_lines else "(no log entries yet)"
    return Panel(rendered, title=f"Log tail — {path}", box=box.ROUNDED, padding=(1, 1))


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


def render_layout(state: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=7),
    )
    layout["body"].split_row(
        Layout(name="metrics", ratio=1),
        Layout(name="logs", ratio=2),
    )

    layout["header"].update(build_header(state.get("service_name", "Operations"), state.get("status", "Unknown")))
    layout["metrics"].update(build_metrics_table(state))
    layout["logs"].update(build_log_panel(state.get("log_lines", []), state.get("log_path", Path("log"))))
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
    }

    with Live(render_layout(live_state), console=console, screen=True, refresh_per_second=4) as live:
        while not stop_event.is_set():
            metrics = read_process_metrics(tracked_pid)
            port_status = check_port(app_port)
            log_snapshot = tail_log(log_file, state=log_cursor)

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
            }

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
