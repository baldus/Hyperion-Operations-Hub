from __future__ import annotations

from pathlib import Path


def safe_read_text(path: Path | str, *, default: str = "UNKNOWN") -> str:
    target = Path(path)
    try:
        data = target.read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return data if data else default


def safe_read_lines(path: Path | str, *, max_lines: int = 200) -> list[str]:
    target = Path(path)
    if not target.exists():
        return [f"Log file not found: {target}"]
    try:
        with target.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read().splitlines()[-max_lines:]
    except OSError as exc:
        return [f"Unable to read log: {exc}"]


def tail_new_lines(
    path: Path | str,
    *,
    last_position: int = 0,
    max_bytes: int = 65536,
) -> tuple[list[str], int]:
    target = Path(path)
    if not target.exists():
        return [f"Log file not found: {target}"], last_position
    try:
        with target.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(0, 2)
            end_position = handle.tell()
            if end_position < last_position:
                last_position = 0
            handle.seek(last_position)
            buffer = handle.read(max_bytes)
            new_position = handle.tell()
    except OSError as exc:
        return [f"Unable to read log: {exc}"], last_position

    lines = buffer.splitlines()
    return lines, new_position
