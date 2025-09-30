"""Legacy receiving routes that delegate to the inventory workflow.

This module keeps the historical ``/receiving`` endpoints available while
reusing the Movement/Batch based implementation that now lives under the
inventory blueprint.
"""

from flask import Blueprint

from .inventory import (
    receiving as inventory_receiving,
    reprint_receiving_label as inventory_reprint_receiving_label,
)

bp = Blueprint("receiving", __name__, url_prefix="/receiving")


@bp.route("/", methods=["GET", "POST"])
def receiving_home():
    """Proxy the legacy receiving route to the inventory workflow."""

    return inventory_receiving()


@bp.post("/<int:receipt_id>/reprint")
def reprint_receiving_label(receipt_id: int):
    """Proxy reprint requests to the inventory workflow."""

    return inventory_reprint_receiving_label(receipt_id)
