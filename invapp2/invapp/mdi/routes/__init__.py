"""Route package for the MDI blueprint."""

from . import api, dashboard, email, meeting, reports

__all__ = ["api", "dashboard", "email", "meeting", "reports"]


def register_routes(bp):
    """Attach all route modules to the provided blueprint."""
    for module in (meeting, dashboard, reports, email, api):
        register = getattr(module, "register", None)
        if register is not None:
            register(bp)
