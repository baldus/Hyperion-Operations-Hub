import os
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from invapp.auth import blueprint_page_guard
from invapp.login import current_user
from invapp.models import (
    Order,
    OrderLine,
    OrderStatus,
    RoutingStep,
    WorkInstruction,
    db,
)
from invapp.security import require_roles

bp = Blueprint("work", __name__, url_prefix="/work")

bp.before_request(blueprint_page_guard("work"))


def _allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in current_app.config["WORK_INSTRUCTION_ALLOWED_EXTENSIONS"]
    )


@dataclass
class StationQueue:
    name: str
    slug: str
    entries: list[dict[str, object]]

    @property
    def waiting_count(self) -> int:
        return len(self.entries)

    @property
    def next_order(self) -> str | None:
        if not self.entries:
            return None
        return self.entries[0].get("order_number")  # type: ignore[return-value]

    @property
    def next_promised_date(self) -> str | None:
        if not self.entries:
            return None
        promised = self.entries[0].get("promised_date")
        return promised if isinstance(promised, str) else None


def _slugify_station_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not base:
        base = "station"
    slug = base
    counter = 2
    while slug in used:
        slug = f"{base}-{counter}"
        counter += 1
    used.add(slug)
    return slug


def _build_queue_entry(step: RoutingStep) -> dict[str, object]:
    order = step.order
    primary_line: OrderLine | None = order.primary_line

    promised_date = (
        order.promised_date.isoformat() if order.promised_date else None
    )
    scheduled_completion = (
        order.scheduled_completion_date.isoformat()
        if order.scheduled_completion_date
        else None
    )

    quantity = primary_line.quantity if primary_line is not None else None
    item_sku = (
        primary_line.item.sku
        if primary_line is not None and primary_line.item is not None
        else None
    )
    item_name = (
        primary_line.item.name
        if primary_line is not None and primary_line.item is not None
        else None
    )

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_status": order.status_label,
        "sequence": step.sequence,
        "description": step.description,
        "promised_date": promised_date,
        "scheduled_completion": scheduled_completion,
        "quantity": quantity,
        "item_sku": item_sku,
        "item_name": item_name,
    }


def _gather_station_queues() -> tuple[list[StationQueue], dict[str, StationQueue], int]:
    active_statuses = (
        OrderStatus.SCHEDULED,
        OrderStatus.OPEN,
    )

    steps = (
        RoutingStep.query.join(Order)
        .options(
            joinedload(RoutingStep.order)
            .joinedload(Order.order_lines)
            .joinedload(OrderLine.item)
        )
        .filter(Order.status.in_(active_statuses))
        .order_by(RoutingStep.order_id.asc(), RoutingStep.sequence.asc())
        .all()
    )

    steps_by_order: dict[int, list[RoutingStep]] = defaultdict(list)
    for step in steps:
        steps_by_order[step.order_id].append(step)

    station_steps: dict[str, list[RoutingStep]] = defaultdict(list)
    for order_steps in steps_by_order.values():
        order_steps.sort(key=lambda s: s.sequence)
        for step in order_steps:
            if step.completed:
                continue
            if not step.work_cell:
                # Unassigned steps block downstream work from appearing in queues.
                break
            station_steps[step.work_cell].append(step)
            break

    def _sort_key(step: RoutingStep) -> tuple[date, date, str, int]:
        promised = step.order.promised_date or date.max
        scheduled = step.order.scheduled_completion_date or date.max
        order_number = step.order.order_number or ""
        return (promised, scheduled, order_number, step.sequence)

    used_slugs: set[str] = set()
    stations: list[StationQueue] = []
    by_slug: dict[str, StationQueue] = {}

    for name, queue_steps in sorted(
        station_steps.items(), key=lambda item: (-len(item[1]), item[0].lower())
    ):
        queue_steps.sort(key=_sort_key)
        entries = [_build_queue_entry(step) for step in queue_steps]
        slug = _slugify_station_name(name, used_slugs)
        station = StationQueue(name=name, slug=slug, entries=entries)
        stations.append(station)
        by_slug[slug] = station

    total_waiting = sum(station.waiting_count for station in stations)
    return stations, by_slug, total_waiting


@bp.route("/")
def work_home():
    return redirect(url_for("work.station_overview"))


@bp.route("/instructions")
def list_instructions():
    instructions = WorkInstruction.query.order_by(
        WorkInstruction.uploaded_at.desc()
    ).all()
    return render_template(
        "work/instructions.html",
        instructions=instructions,
        is_admin=current_user.is_authenticated and current_user.has_role("admin"),
    )


@bp.route("/stations")
def station_overview():
    stations, _, total_waiting = _gather_station_queues()
    return render_template(
        "work/home.html",
        stations=stations,
        total_waiting=total_waiting,
    )


@bp.route("/stations/<string:station_slug>")
def station_detail(station_slug: str):
    _, by_slug, _ = _gather_station_queues()
    station = by_slug.get(station_slug)
    if station is None:
        abort(404)
    return render_template(
        "work/station_detail.html",
        station=station,
    )


@bp.route("/instructions/upload", methods=["POST"])
@require_roles("admin")
def upload_instruction():
    file = request.files.get("file")
    if not file or file.filename == "" or not _allowed_file(file.filename):
        return redirect(url_for("work.list_instructions"))

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    upload_folder = current_app.config["WORK_INSTRUCTION_UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, unique_name)
    file.save(file_path)

    wi = WorkInstruction(filename=unique_name, original_name=filename)
    db.session.add(wi)
    db.session.commit()

    return redirect(url_for("work.list_instructions"))


@bp.route("/instructions/<int:instruction_id>/delete", methods=["POST"])
@require_roles("admin")
def delete_instruction(instruction_id):
    wi = WorkInstruction.query.get_or_404(instruction_id)
    file_path = os.path.join(
        current_app.config["WORK_INSTRUCTION_UPLOAD_FOLDER"], wi.filename
    )
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(wi)
    db.session.commit()
    return redirect(url_for("work.list_instructions"))

