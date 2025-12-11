from datetime import date, datetime, timedelta

from flask import render_template, request
from sqlalchemy import case

from invapp.mdi.models import CATEGORY_DISPLAY, MDIEntry, STATUS_BADGES

from .constants import ACTIVE_STATUS_FILTER, COMPLETED_STATUSES


def _trend_date_range(days: int = 14):
    """Return a list of dates covering the trailing window (oldest to newest)."""

    today = date.today()
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _entry_open_close_dates(entry: MDIEntry):
    """Return tuple of (opened_date, closed_date) used for trend counting.

    An entry is considered open for any day where:
    - opened_date <= day
    - closed_date is None or closed_date > day

    We treat `date_logged` as the opened/created date with a fallback to
    `created_at`. If the entry is in a completed status and has an
    `updated_at` timestamp, that timestamp is treated as the closed date to
    avoid adding new schema just for this visualization.
    """

    opened_date = entry.date_logged or (entry.created_at.date() if entry.created_at else None)
    closed_date = None
    if entry.status in COMPLETED_STATUSES and entry.updated_at:
        closed_date = entry.updated_at.date()
    return opened_date, closed_date


def _trend_series(entries, date_range):
    """Build per-category daily open counts for the provided date range."""

    series = {category: [0 for _ in date_range] for category in CATEGORY_DISPLAY.keys()}

    for entry in entries:
        opened_date, closed_date = _entry_open_close_dates(entry)
        if opened_date is None:
            continue

        category = entry.category or "Uncategorized"
        if category not in series:
            series[category] = [0 for _ in date_range]

        for index, day in enumerate(date_range):
            if opened_date <= day and (closed_date is None or closed_date > day):
                series[category][index] += 1

    return series


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

    grouped_entries = {category: [] for category in CATEGORY_DISPLAY.keys()}
    metrics_overview = {category: {"metric_count": 0} for category in CATEGORY_DISPLAY.keys()}
    for entry in entries:
        grouped_entries.setdefault(entry.category, []).append(entry)

    for category, category_entries in grouped_entries.items():
        metric_count = sum(1 for item in category_entries if item.metric_value is not None)
        metrics_overview.setdefault(category, {})["metric_count"] = metric_count

    trend_dates = _trend_date_range()
    trend_series = _trend_series(MDIEntry.query.all(), trend_dates)
    trend_labels = [day.strftime("%b %d") for day in trend_dates]
    trend_summary = []

    for category, values in trend_series.items():
        if not values:
            continue
        start_count = values[0]
        end_count = values[-1]
        if end_count > start_count:
            direction = "up"
        elif end_count < start_count:
            direction = "down"
        else:
            direction = "flat"

        trend_summary.append(
            {
                "category": category,
                "start": start_count,
                "end": end_count,
                "direction": direction,
            }
        )

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
        active_status_filter=ACTIVE_STATUS_FILTER,
        current_time=datetime.utcnow(),
        mdi_trend_labels=trend_labels,
        mdi_trend_series=trend_series,
        mdi_trend_summary=trend_summary,
    )


def register(bp):
    bp.add_url_rule("/mdi/meeting", view_func=meeting_view)

