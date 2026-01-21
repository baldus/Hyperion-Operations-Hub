from __future__ import annotations

import getpass
import logging
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_LOG_PATH = Path("/var/log/hyperion/terminal_monitor.log")
FALLBACK_LOG_PATH = Path("/tmp/hyperion_terminal_monitor.log")


def setup_logging(log_path: Path | str | None = None) -> Path:
    target = Path(log_path) if log_path else DEFAULT_LOG_PATH
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    handler = _create_file_handler(target)
    if handler is None:
        handler = _create_file_handler(FALLBACK_LOG_PATH)
        if handler is not None:
            target = FALLBACK_LOG_PATH

    if handler is None:
        logging.basicConfig(level=logging.INFO)
        return target

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    logger.handlers.clear()
    logger.addHandler(handler)
    return target


def _create_file_handler(path: Path) -> logging.Handler | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return logging.FileHandler(path)
    except OSError:
        return None


def log_startup_details(logger: logging.Logger, *, log_path: Path, headless: bool) -> None:
    logger.info("terminal monitor starting")
    logger.info("version=%s", _safe_version())
    logger.info("python=%s", sys.version.replace("\n", " "))
    logger.info("platform=%s", platform.platform())
    logger.info("term=%s", os.environ.get("TERM", ""))
    logger.info("isatty stdin=%s stdout=%s", sys.stdin.isatty(), sys.stdout.isatty())
    logger.info("cwd=%s", os.getcwd())
    logger.info("user=%s", getpass.getuser())
    logger.info("headless=%s", headless)
    logger.info("log_path=%s", log_path)
    logger.info("started_at=%s", datetime.utcnow().isoformat())


def _safe_version() -> str:
    try:
        from terminal_monitor import __version__

        return __version__
    except Exception:
        return "unknown"
