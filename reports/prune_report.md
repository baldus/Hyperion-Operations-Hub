# Prune Report

Generated: 2026-01-22T17:57:59.114114+00:00

## Overview

This report captures usage-analysis tooling output for pruning candidates. All findings are *candidates* until confirmed by runtime tracing or manual audits.

## Runtime usage tracing

Runtime usage tracing is controlled by ENABLE_USAGE_TRACING=1 and logs to instance/usage_tracing.log by default. Capture logs by exercising the app and review the log file for routes, templates, and static requests.

## Tooling results

### `ruff check /workspace/Hyperion-Operations-Hub/invapp2 --select F401,F841`

Exit code: 1

**Stdout**
```
F841 [*] Local variable `exc` is assigned to but never used
   --> invapp2/invapp/__init__.py:842:39
    |
840 |                         exc_info=current_app.debug,
841 |                     )
842 |             except SQLAlchemyError as exc:  # pragma: no cover - defensive guard
    |                                       ^^^
843 |                 database_available = False
844 |                 database_error_message = (
    |
help: Remove assignment to unused variable `exc`

F841 [*] Local variable `exc` is assigned to but never used
    --> invapp2/invapp/routes/admin.py:1486:33
     |
1484 |                 current_app.logger.exception("Database migration failed")
1485 |                 flash(f"Migration failed: {exc}", "danger")
1486 |             except Exception as exc:  # pragma: no cover - defensive guard
     |                                 ^^^
1487 |                 current_app.logger.exception("Unexpected error during migration")
1488 |                 flash("An unexpected error occurred during migration.", "danger")
     |
help: Remove assignment to unused variable `exc`

Found 2 errors.
[*] 2 fixable with the `--fix` option.
```

### `vulture`

Exit code: 127

**Stderr**
```
vulture not installed
```

### `pip-missing-reqs`

Exit code: 127

**Stderr**
```
pip-missing-reqs not installed
```

### `pip-check-reqs`

Exit code: 127

**Stderr**
```
pip-check-reqs not installed
```

## Pruning status

- Safe removals: unused imports flagged by Ruff and removed in this batch.
- Uncertain candidates: review tooling output above and confirm with runtime tracing logs before removal.
