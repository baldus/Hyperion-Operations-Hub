from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rich.text import Text
from textual.widget import Widget
from textual.widgets import RichLog

from terminal_monitor.util.fs import safe_read_lines, tail_new_lines


@dataclass
class LogSource:
    label: str
    path: Path


DEFAULT_LOG_SOURCES = [
    LogSource("terminal monitor", Path("/var/log/hyperion/terminal_monitor.log")),
    LogSource("internet watchdog", Path("/var/log/internet_watchdog.log")),
    LogSource("backup log", Path("/var/lib/hyperion/backup.log")),
    LogSource("app backup", Path("/var/lib/hyperion/backups/backup.log")),
]


class LogPanel(Widget):
    can_focus = True

    def __init__(
        self,
        log_sources: Iterable[LogSource],
        *,
        max_lines: int = 500,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.log_sources = list(log_sources)
        self.max_lines = max_lines
        self.current_index = 0
        self.follow = True
        self.search_term: str | None = None
        self._last_position = 0
        self._lines: list[str] = []
        self._full_refresh = True
        self._log: RichLog | None = None

    def compose(self):
        yield RichLog(highlight=False, markup=False, wrap=True, id="log-view")

    def on_mount(self) -> None:
        self._log = self.query_one(RichLog)
        self._log.border_title = self.title
        self._log.border_subtitle = "follow: on"
        self._log.auto_scroll = True
        self._load_initial()

    @property
    def title(self) -> str:
        source = self.current_source
        return f"Live Logs · {source.label}"

    @property
    def current_source(self) -> LogSource:
        if not self.log_sources:
            return LogSource("(none)", Path("/var/log/hyperion/terminal_monitor.log"))
        return self.log_sources[self.current_index]

    @property
    def current_path(self) -> Path:
        return self.current_source.path

    def _load_initial(self) -> None:
        self._lines = safe_read_lines(self.current_path, max_lines=self.max_lines)
        try:
            self._last_position = self.current_path.stat().st_size
        except OSError:
            self._last_position = 0
        self._full_refresh = True
        self.refresh_log(force=True)

    def cycle_source(self) -> None:
        if not self.log_sources:
            return
        self.current_index = (self.current_index + 1) % len(self.log_sources)
        self._load_initial()

    def toggle_follow(self) -> None:
        self.follow = not self.follow
        self._set_follow_ui()

    def apply_search(self, term: str | None) -> None:
        self.search_term = term if term else None
        if self.search_term:
            self.follow = False
        self._full_refresh = True
        self._set_follow_ui()

    def clear_search(self) -> None:
        self.apply_search(None)

    def update_lines(self) -> None:
        new_lines, new_position = tail_new_lines(self.current_path, last_position=self._last_position)
        if new_lines:
            if self._lines and new_lines == self._lines[-len(new_lines) :]:
                return
            self._lines.extend(new_lines)
            if len(self._lines) > self.max_lines:
                self._lines = self._lines[-self.max_lines :]
            self._last_position = new_position
            if self._log and not self.search_term and not self._full_refresh:
                self._append_lines(new_lines)
                if self.follow:
                    self._log.scroll_end(animate=False)
            else:
                self.refresh_log()

    def refresh_log(self, *, force: bool = False) -> None:
        if self._log is None:
            return
        if self.search_term or self._full_refresh or force:
            self._log.clear()
            self._render_lines(self._filtered_lines())
            self._full_refresh = False
        self._set_follow_ui()
        if self.follow and not self.search_term:
            self._log.scroll_end(animate=False)

    def _filtered_lines(self) -> list[str]:
        if not self.search_term:
            return self._lines
        term = self.search_term.lower()
        return [line for line in self._lines if term in line.lower()]

    def _render_lines(self, lines: list[str]) -> None:
        for line in lines or ["(no log entries yet)"]:
            if self.search_term:
                self._log.write(self._highlight(line, self.search_term))
            else:
                self._log.write(line)

    def _append_lines(self, lines: list[str]) -> None:
        if not self._log:
            return
        for line in lines:
            if self.search_term:
                self._log.write(self._highlight(line, self.search_term))
            else:
                self._log.write(line)

    def _highlight(self, line: str, term: str) -> Text:
        text = Text(line)
        lower = line.lower()
        term_lower = term.lower()
        start = 0
        while True:
            idx = lower.find(term_lower, start)
            if idx == -1:
                break
            text.stylize("bold yellow", idx, idx + len(term))
            start = idx + len(term)
        return text

    def page_up(self) -> None:
        if self._log:
            self.follow = False
            self._set_follow_ui()
            self._log.scroll_relative(y=-10)

    def page_down(self) -> None:
        if self._log:
            self.follow = False
            self._set_follow_ui()
            self._log.scroll_relative(y=10)

    def _set_follow_ui(self) -> None:
        if not self._log:
            return
        self._log.border_title = self.title
        follow_state = "on" if self.follow else "off"
        search_state = f"search: {self.search_term}" if self.search_term else ""
        subtitle = f"follow: {follow_state}"
        if search_state:
            subtitle = f"{subtitle} · {search_state}"
        self._log.border_subtitle = subtitle
        self._log.auto_scroll = self.follow
