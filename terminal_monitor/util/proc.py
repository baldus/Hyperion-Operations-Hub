from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence


@dataclass
class CommandResult:
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def run_command(command: Sequence[str], *, timeout: float = 1.5) -> CommandResult:
    cmd = list(command)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            command=cmd,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
            exit_code=completed.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(command=cmd, stdout="", stderr="timeout", exit_code=1, timed_out=True)
    except OSError as exc:
        return CommandResult(command=cmd, stdout="", stderr=str(exc), exit_code=1, timed_out=False)
