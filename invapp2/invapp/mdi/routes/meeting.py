from datetime import datetime

from flask import Blueprint, render_template, request

from sqlalchemy import case

from models.mdi_models import MDIEntry, CATEGORY_DISPLAY, STATUS_BADGES


meeting_bp = Blueprint("meeting", __name__, template_folder="../templates")


@meeting_bp.route("/mdi/meeting")
def meeting_view():
    status_filter = request.args.get("status")
    category_filter = request.args.get("category")
    date_filter = request.args.get("date")

    query = MDIEntry.query
    if status_filter:
        query = query.filter(MDIEntry.status == status_filter)
    if category_filter:
        query = query.filter(MDIEntry.category == category_filter)
    if date_filter:
        try:
            query = query.filter(MDIEntry.date_logged == datetime.strptime(date_filter, "%Y-%m-%d").date())
        except ValueError:
            pass

    priority_case = case(
        (MDIEntry.priority == "High", 3),
        (MDIEntry.priority == "Medium", 2),
        (MDIEntry.priority == "Low", 1),
        else_=0,
    )

    entries = query.order_by(priority_case.desc(), MDIEntry.date_logged.desc()).all()

    grouped_entries = {category: [] for category in CATEGORY_DISPLAY.keys()}
    metrics_overview = {category: {"metric_count": 0} for category in CATEGORY_DISPLAY.keys()}
    for entry in entries:
        grouped_entries.setdefault(entry.category, []).append(entry)

    for category, category_entries in grouped_entries.items():
        metric_count = sum(1 for item in category_entries if item.metric_value is not None)
        metrics_overview.setdefault(category, {})["metric_count"] = metric_count

    return render_template(
        "meeting_view.html",
        grouped_entries=grouped_entries,
        metrics_overview=metrics_overview,
        category_meta=CATEGORY_DISPLAY,
        status_badges=STATUS_BADGES,
        filters={
            "status": status_filter,
            "category": category_filter,
            "date": date_filter,
        },
        current_time=datetime.utcnow(),
    )

