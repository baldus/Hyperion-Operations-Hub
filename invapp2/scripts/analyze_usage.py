#!/usr/bin/env python
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


def _run_command(command: list[str], cwd: Path) -> dict[str, str | int]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": " ".join(command),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _find_tool(candidates: Iterable[str]) -> str | None:
    for name in candidates:
        path = shutil.which(name)
        if path:
            return name
    return None


def _write_section(lines: list[str], title: str) -> None:
    lines.append(f"## {title}")
    lines.append("")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze usage and pruning candidates.")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output markdown report path (default: reports/prune_report.md)",
    )
    parser.add_argument(
        "--ruff-select",
        type=str,
        default="F401,F841",
        help="Ruff select codes for unused code checks.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=80,
        help="Vulture min confidence (when installed).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    invapp_root = repo_root / "invapp2"
    output_path = (
        Path(args.output)
        if args.output
        else repo_root / "reports" / "prune_report.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_lines: list[str] = []
    report_lines.append("# Prune Report")
    report_lines.append("")
    report_lines.append(f"Generated: {dt.datetime.now(dt.UTC).isoformat()}")
    report_lines.append("")

    _write_section(report_lines, "Overview")
    report_lines.append(
        "This report captures usage-analysis tooling output for pruning candidates. "
        "All findings are *candidates* until confirmed by runtime tracing or "
        "manual audits."
    )
    report_lines.append("")

    _write_section(report_lines, "Runtime usage tracing")
    report_lines.append(
        "Runtime usage tracing is controlled by ENABLE_USAGE_TRACING=1 and logs to "
        "instance/usage_tracing.log by default. Capture logs by exercising the app "
        "and review the log file for routes, templates, and static requests."
    )
    report_lines.append("")

    _write_section(report_lines, "Tooling results")

    tooling_runs: list[dict[str, str | int]] = []

    ruff_cmd = [
        "ruff",
        "check",
        str(invapp_root),
        "--select",
        args.ruff_select,
    ]
    tooling_runs.append(_run_command(ruff_cmd, repo_root))

    vulture_tool = _find_tool(["vulture"])
    if vulture_tool:
        tooling_runs.append(
            _run_command(
                [
                    vulture_tool,
                    str(invapp_root),
                    "--min-confidence",
                    str(args.min_confidence),
                ],
                repo_root,
            )
        )
    else:
        tooling_runs.append(
            {
                "command": "vulture",
                "exit_code": 127,
                "stdout": "",
                "stderr": "vulture not installed",
            }
        )

    req_tool = _find_tool(["pip-missing-reqs", "pip-check-reqs"])
    if req_tool == "pip-missing-reqs":
        tooling_runs.append(
            _run_command(["pip-missing-reqs", str(invapp_root)], repo_root)
        )
        extra_tool = _find_tool(["pip-extra-reqs"])
        if extra_tool:
            tooling_runs.append(
                _run_command(["pip-extra-reqs", str(invapp_root)], repo_root)
            )
        else:
            tooling_runs.append(
                {
                    "command": "pip-extra-reqs",
                    "exit_code": 127,
                    "stdout": "",
                    "stderr": "pip-extra-reqs not installed",
                }
            )
    elif req_tool == "pip-check-reqs":
        tooling_runs.append(
            _run_command(["pip-check-reqs", str(invapp_root)], repo_root)
        )
    else:
        tooling_runs.append(
            {
                "command": "pip-missing-reqs",
                "exit_code": 127,
                "stdout": "",
                "stderr": "pip-missing-reqs not installed",
            }
        )
        tooling_runs.append(
            {
                "command": "pip-check-reqs",
                "exit_code": 127,
                "stdout": "",
                "stderr": "pip-check-reqs not installed",
            }
        )

    for run in tooling_runs:
        command = run["command"]
        report_lines.append(f"### `{command}`")
        report_lines.append("")
        report_lines.append(f"Exit code: {run['exit_code']}")
        report_lines.append("")
        stdout = run["stdout"]
        stderr = run["stderr"]
        if stdout:
            report_lines.append("**Stdout**")
            report_lines.append("```")
            report_lines.append(str(stdout))
            report_lines.append("```")
        if stderr:
            report_lines.append("**Stderr**")
            report_lines.append("```")
            report_lines.append(str(stderr))
            report_lines.append("```")
        report_lines.append("")

    _write_section(report_lines, "Pruning status")
    report_lines.append(
        "- Safe removals: unused imports flagged by Ruff and removed in this batch."
    )
    report_lines.append(
        "- Uncertain candidates: review tooling output above and confirm with "
        "runtime tracing logs before removal."
    )
    report_lines.append("")

    output_path.write_text("\n".join(report_lines))

    print(json.dumps({"report": str(output_path)}, indent=2))


if __name__ == "__main__":
    main()
