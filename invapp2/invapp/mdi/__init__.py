"""Blueprint registration for the integrated MDI module."""
from __future__ import annotations

from flask import Blueprint

from .routes import register_routes

mdi_bp: Blueprint | None = None


def _build_blueprint() -> Blueprint:
    bp = Blueprint(
        "mdi",
        __name__,
        template_folder="../templates/mdi",
        static_folder="../static/mdi",
    )
    register_routes(bp)
    return bp


def init_blueprint() -> Blueprint:
    """Create a fresh blueprint so repeated app factories stay isolated."""
    global mdi_bp
    mdi_bp = _build_blueprint()
    return mdi_bp


# Initialize the module-level blueprint once so runtime imports can use it.
init_blueprint()

__all__ = ["mdi_bp", "init_blueprint"]
