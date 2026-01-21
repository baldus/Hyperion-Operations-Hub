from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from terminal_monitor.panels.backups import read_backup_status
from terminal_monitor.panels.host_info import get_host_info
from terminal_monitor.panels.logs import LogPanel, LogSource
from terminal_monitor.panels.network import read_network_status
from terminal_monitor.panels.services import get_service_statuses, resolve_service_list
from terminal_monitor.panels.system_health import get_system_health
from terminal_monitor.util.doctor import run_doctor
from terminal_monitor.util.logging import log_startup_details, setup_logging

LOG = logging.getLogger("terminal_monitor")


class InfoPanel(Static):
    can_focus = True

    def __init__(self, title: str, *, panel_id: str) -> None:
        super().__init__("", id=panel_id)
        self.border_title = title

    def update_lines(self, lines: list[str], *, style: str | None = None) -> None:
        if style:
            text = Text("\n".join(lines), style=style)
            self.update(text)
        else:
            self.update("\n".join(lines))


class SearchScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def __init__(self, initial: str | None = None) -> None:
        super().__init__()
        self._initial = initial or ""

    def compose(self) -> ComposeResult:
        yield Static("Search logs", id="search-title")
        yield Input(value=self._initial, placeholder="Enter search term", id="search-input")
        yield Horizontal(
            Button("Apply", id="search-apply", variant="primary"),
            Button("Clear", id="search-clear"),
            Button("Cancel", id="search-cancel"),
            id="search-actions",
        )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search-apply":
            value = self.query_one(Input).value.strip()
            self.dismiss(value or None)
        elif event.button.id == "search-clear":
            self.dismiss("")
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class TerminalMonitorApp(App):
    CSS = """
    Screen {
        layout: vertical;
        background: black;
        color: white;
    }

    #header {
        height: 3;
        content-align: center middle;
        background: #1f2937;
        color: white;
        text-style: bold;
    }

    #main {
        height: 1fr;
    }

    #left, #right {
        width: 1fr;
    }

    InfoPanel, LogPanel {
        border: round #334155;
        padding: 1;
        height: 1fr;
    }

    InfoPanel:focus, LogPanel:focus {
        border: heavy #22d3ee;
    }

    #status-bar {
        height: 1;
        background: #111827;
        color: #9ca3af;
        content-align: center middle;
    }

    #logs {
        height: 2fr;
    }

    #search-title {
        content-align: center middle;
        text-style: bold;
        padding: 1 0;
    }

    #search-actions {
        height: auto;
        content-align: center middle;
        padding: 1 0;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("tab", "focus_next", "Focus next"),
        ("enter", "activate", "Activate"),
        ("f", "toggle_follow", "Follow"),
        ("/", "search_logs", "Search"),
        ("pageup", "page_up", "Scroll up"),
        ("pagedown", "page_down", "Scroll down"),
        ("j", "focus_down", "Down"),
        ("k", "focus_up", "Up"),
        ("up", "focus_up", "Up"),
        ("down", "focus_down", "Down"),
        ("left", "focus_left", "Left"),
        ("right", "focus_right", "Right"),
    ]

    def __init__(self, *, refresh_ms: int, log_sources: list[LogSource], db_url: str | None) -> None:
        super().__init__()
        self.refresh_ms = refresh_ms
        self.log_sources = log_sources
        self.db_url = db_url
        self._panel_positions: dict[str, tuple[int, int]] = {}
        self._panels: list[Static] = []
        self._log_panel: LogPanel | None = None

    def compose(self) -> ComposeResult:
        yield Static("Hyperion Ops Console — Terminal Monitor", id="header")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield InfoPanel("Network Status", panel_id="network")
                yield InfoPanel("Services", panel_id="services")
                yield InfoPanel("Host Info", panel_id="host-info")
            with Vertical(id="right"):
                yield InfoPanel("System Health", panel_id="system-health")
                yield InfoPanel("Backups", panel_id="backups")
                yield LogPanel(self.log_sources, id="logs")
        yield Static(
            "q quit · tab focus · arrows/j/k move · enter activate · f follow · / search · pgup/pgdn scroll",
            id="status-bar",
        )

    def on_mount(self) -> None:
        self._log_panel = self.query_one(LogPanel)
        self._panel_positions = {
            "network": (0, 0),
            "services": (1, 0),
            "host-info": (2, 0),
            "system-health": (0, 1),
            "backups": (1, 1),
            "logs": (2, 1),
        }
        self._panels = [
            self.query_one("#network", InfoPanel),
            self.query_one("#services", InfoPanel),
            self.query_one("#host-info", InfoPanel),
            self.query_one("#system-health", InfoPanel),
            self.query_one("#backups", InfoPanel),
            self._log_panel,
        ]
        self.set_focus(self.query_one("#network", InfoPanel))
        self.set_interval(self.refresh_ms / 1000.0, self.refresh_panels)
        self.set_interval(1, self.update_clock)
        self.refresh_panels()
        self.update_clock()

    def update_clock(self) -> None:
        header = self.query_one("#header", Static)
        header.update(f"Hyperion Ops Console — Terminal Monitor · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def refresh_panels(self) -> None:
        self._refresh_network()
        self._refresh_services()
        self._refresh_host_info()
        self._refresh_system_health()
        self._refresh_backups()
        if self._log_panel:
            try:
                self._log_panel.update_lines()
            except Exception:
                LOG.exception("log panel update failed")

    def _refresh_network(self) -> None:
        panel = self.query_one("#network", InfoPanel)
        try:
            status = read_network_status()
            if status.status == "OFFLINE":
                panel.update_lines([f"!! {status.raw} !!"], style="bold red")
            elif status.status == "UNKNOWN":
                panel.update_lines([status.raw], style="yellow")
            else:
                panel.update_lines([status.raw])
        except Exception:
            LOG.exception("network panel update failed")
            panel.update_lines(["UNKNOWN | network status error"], style="yellow")

    def _refresh_services(self) -> None:
        panel = self.query_one("#services", InfoPanel)
        try:
            statuses = get_service_statuses(resolve_service_list())
            lines = [f"{entry.name}: {entry.status}" for entry in statuses]
            panel.update_lines(lines)
        except Exception:
            LOG.exception("services panel update failed")
            panel.update_lines(["service status unavailable"], style="yellow")

    def _refresh_host_info(self) -> None:
        panel = self.query_one("#host-info", InfoPanel)
        try:
            info = get_host_info()
            panel.update_lines(
                [
                    f"Host: {info.hostname}",
                    f"Primary IP: {info.primary_ip}",
                    f"Kernel: {info.kernel}",
                    f"OS: {info.os}",
                    f"User: {info.user}",
                ]
            )
        except Exception:
            LOG.exception("host info update failed")
            panel.update_lines(["host info unavailable"], style="yellow")

    def _refresh_system_health(self) -> None:
        panel = self.query_one("#system-health", InfoPanel)
        try:
            health = get_system_health()
            panel.update_lines(
                [
                    f"Load avg: {health.load_avg}",
                    f"Mem: {health.mem_usage}",
                    f"Disk /: {health.disk_root}",
                    f"Disk /var: {health.disk_var}",
                    f"Uptime: {health.uptime}",
                    f"CPU temp: {health.cpu_temp}",
                ]
            )
        except Exception:
            LOG.exception("system health update failed")
            panel.update_lines(["system health unavailable"], style="yellow")

    def _refresh_backups(self) -> None:
        panel = self.query_one("#backups", InfoPanel)
        try:
            status = read_backup_status(self.db_url)
            lines = ["Last run: " + _format_dt(status.last_run_at)]
            if status.last_run_status:
                lines.append(f"Status: {status.last_run_status}")
            if status.last_run_filename:
                lines.append(f"File: {status.last_run_filename}")
            if status.last_success_at:
                lines.append("Last success: " + _format_dt(status.last_success_at))
            if status.next_run_at:
                lines.append("Next run: " + _format_dt(status.next_run_at))
            if status.restore_last_at:
                lines.append("Restore: " + _format_dt(status.restore_last_at))
            if status.restore_last_status:
                lines.append(f"Restore status: {status.restore_last_status}")
            if not any(line.strip() for line in lines):
                lines = ["Unknown"]
            panel.update_lines(lines)
        except Exception:
            LOG.exception("backup panel update failed")
            panel.update_lines(["backup status unknown"], style="yellow")

    def action_toggle_follow(self) -> None:
        if self._log_panel and self._log_panel.has_focus:
            self._log_panel.toggle_follow()

    async def action_search_logs(self) -> None:
        if not self._log_panel:
            return

        def handle_result(result: str | None) -> None:
            if result is None:
                return
            if result == "":
                self._log_panel.clear_search()
            else:
                self._log_panel.apply_search(result)

        await self.push_screen(SearchScreen(self._log_panel.search_term), handle_result)

    def action_page_up(self) -> None:
        if self._log_panel and self._log_panel.has_focus:
            self._log_panel.page_up()

    def action_page_down(self) -> None:
        if self._log_panel and self._log_panel.has_focus:
            self._log_panel.page_down()

    def action_activate(self) -> None:
        if self._log_panel and self._log_panel.has_focus:
            self._log_panel.cycle_source()

    def action_focus_up(self) -> None:
        self._move_focus(-1, 0)

    def action_focus_down(self) -> None:
        self._move_focus(1, 0)

    def action_focus_left(self) -> None:
        self._move_focus(0, -1)

    def action_focus_right(self) -> None:
        self._move_focus(0, 1)

    def _move_focus(self, row_delta: int, col_delta: int) -> None:
        current = self.focused
        if not current or current.id not in self._panel_positions:
            self.focus_next()
            return
        row, col = self._panel_positions[current.id]
        target = (row + row_delta, col + col_delta)
        for panel_id, position in self._panel_positions.items():
            if position == target:
                self.set_focus(self.query_one(f"#{panel_id}"))
                return


def _format_dt(value: datetime | None) -> str:
    if not value:
        return "unknown"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def run_headless(log_sources: list[LogSource], db_url: str | None) -> None:
    LOG.info("headless monitor loop starting (interval=10s)")
    while True:
        try:
            network = read_network_status()
            health = get_system_health()
            services = get_service_statuses(resolve_service_list())
            backup = read_backup_status(db_url)
            host = get_host_info()
            LOG.info("network status: %s", network.raw)
            LOG.info("system health: load=%s mem=%s disk_root=%s disk_var=%s uptime=%s temp=%s",
                     health.load_avg, health.mem_usage, health.disk_root, health.disk_var, health.uptime, health.cpu_temp)
            LOG.info(
                "services: %s",
                ", ".join(f"{svc.name}={svc.status}" for svc in services) or "none",
            )
            LOG.info(
                "backups: last_run=%s status=%s next_run=%s",
                _format_dt(backup.last_run_at),
                backup.last_run_status or "unknown",
                _format_dt(backup.next_run_at),
            )
            LOG.info(
                "host: %s ip=%s kernel=%s user=%s",
                host.hostname,
                host.primary_ip,
                host.kernel,
                host.user,
            )
        except Exception:
            LOG.exception("headless status loop failed")
        time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hyperion terminal monitor")
    parser.add_argument("--headless", action="store_true", help="Run without TTY (log only)")
    parser.add_argument("--doctor", action="store_true", help="Print environment diagnostics and exit")
    parser.add_argument("--log-file", type=Path, help="Override log file path")
    parser.add_argument("--refresh-ms", type=int, default=1000, help="Refresh interval for UI")
    args = parser.parse_args()

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    headless = args.headless or not is_tty

    log_path = setup_logging(args.log_file)
    log_startup_details(LOG, log_path=log_path, headless=headless)
    if not is_tty and not args.headless:
        LOG.warning("No TTY detected; switching to headless mode")

    if args.doctor:
        print(run_doctor(log_path))
        return 0

    db_url = os.getenv("TERMINAL_MONITOR_DB_URL") or os.getenv("OPS_MONITOR_DB_URL") or os.getenv("DB_URL")
    log_sources = _build_log_sources(log_path)

    if headless:
        run_headless(log_sources, db_url)
        return 0

    try:
        app = TerminalMonitorApp(refresh_ms=args.refresh_ms, log_sources=log_sources, db_url=db_url)
        app.run()
    except Exception:
        LOG.error("terminal monitor crashed:\n%s", traceback.format_exc())
        if is_tty:
            _render_error_screen(traceback.format_exc())
        return 1
    return 0


def _build_log_sources(primary_log: Path) -> list[LogSource]:
    sources = [LogSource("terminal monitor", primary_log)]
    for source in [
        LogSource("internet watchdog", Path("/var/log/internet_watchdog.log")),
        LogSource("backup log", Path("/var/lib/hyperion/backup.log")),
        LogSource("app backup", Path("/var/lib/hyperion/backups/backup.log")),
    ]:
        if source.path != primary_log:
            sources.append(source)
    return sources


def _render_error_screen(trace_text: str) -> None:
    message = (
        "Terminal monitor crashed.\n\n"
        "See /var/log/hyperion/terminal_monitor.log for details.\n\n"
        f"{trace_text}\n\n"
        "Press any key to exit."
    )
    try:
        import termios
        import tty

        print(message)
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        print(message)
        input("Press Enter to exit.")


if __name__ == "__main__":
    sys.exit(main())
