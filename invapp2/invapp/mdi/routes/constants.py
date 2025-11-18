"""Shared constants for MDI route filtering."""

# Value assigned to the special status filter option that excludes
# completed statuses.
ACTIVE_STATUS_FILTER = "not_closed_or_received"

# Statuses that are considered completed and therefore excluded when the
# "active" filter is applied.
COMPLETED_STATUSES = ("Closed", "Received")
