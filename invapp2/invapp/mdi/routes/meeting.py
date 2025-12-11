from datetime import date, datetime, timedelta

from flask import render_template, request
from sqlalchemy import case

from invapp.mdi.models import CATEGORY_DISPLAY, MDIEntry, STATUS_BADGES

from .constants import ACTIVE_STATUS_FILTER, COMPLETED_STATUSES


# Colors used for the trend visualization. Matches the wider dashboard palette.
TREND_COLORS = {
    "Safety": "#dc3545",
    "Quality": "#0d6efd",
    "Delivery": "#198754",
    "People": "#fd7e14",
    "Materials": "#6c757d",
}


def meeting_view():
    status_filter = request.args.get("status")
    if status_filter is None:
        status_filter = ACTIVE_STATUS_FILTER
    category_filter = request.args.get("category")
    date_filter = request.args.get("date")

    query = MDIEntry.query
    if status_filter == ACTIVE_STATUS_FILTER:
        query = query.filter(MDIEntry.status.notin_(COMPLETED_STATUSES))
    elif status_filter:
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

    # Build the 14-day window and calculate open items per category per day.
    today = date.today()
    start_date = today - timedelta(days=13)
    date_range = [start_date + timedelta(days=offset) for offset in range(14)]
    labels = [day.strftime("%m/%d") for day in date_range]

    categories = list(CATEGORY_DISPLAY.keys())
    series = {category: [0 for _ in date_range] for category in categories}

    # Limit the query to entries that existed during the window to avoid unnecessary work.
    window_start_dt = datetime.combine(start_date, datetime.min.time())
    window_end_dt = datetime.combine(today, datetime.max.time())
    trend_candidates = (
        MDIEntry.query.filter(MDIEntry.created_at <= window_end_dt)
        .filter(
            (MDIEntry.status.notin_(COMPLETED_STATUSES))
            | (MDIEntry.updated_at.is_(None))
            | (MDIEntry.updated_at >= window_start_dt)
        )
        .all()
    )

    for entry in trend_candidates:
        opened_date = entry.date_logged or (entry.created_at.date() if entry.created_at else today)
        closed_date = None
        if entry.status in COMPLETED_STATUSES and entry.updated_at:
            # Treat the last update as the completion date for trend calculations.
            closed_date = entry.updated_at.date()

        if opened_date > today:
            continue

        for idx, current_day in enumerate(date_range):
            if opened_date <= current_day and (closed_date is None or closed_date > current_day):
                series.setdefault(entry.category, [0 for _ in date_range])[idx] += 1

    summary = {}
    for category, counts in series.items():
        start_count = counts[0] if counts else 0
        latest_count = counts[-1] if counts else 0
        if latest_count > start_count:
            direction = "up"
        elif latest_count < start_count:
            direction = "down"
        else:
            direction = "flat"

        summary[category] = {
            "start": start_count,
            "latest": latest_count,
            "direction": direction,
        }

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
        mdi_trend_labels=labels,
        mdi_trend_series=series,
        mdi_trend_colors=TREND_COLORS,
        mdi_trend_summary=summary,
        filters={
            "status": status_filter,
            "category": category_filter,
            "date": date_filter,
        },
        active_status_filter=ACTIVE_STATUS_FILTER,
        current_time=datetime.utcnow(),
    )


def register(bp):
    bp.add_url_rule("/mdi/meeting", view_func=meeting_view)

