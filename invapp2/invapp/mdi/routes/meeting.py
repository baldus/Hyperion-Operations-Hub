from datetime import date, datetime, timedelta

from flask import render_template, request
from sqlalchemy import case

from invapp.mdi.models import CATEGORY_DISPLAY, MDIEntry, STATUS_BADGES

from .constants import ACTIVE_STATUS_FILTER, COMPLETED_STATUSES


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

    date_range, trend_labels = _chart_date_range()
    category_trends = _build_category_trends(grouped_entries, date_range)
    open_card_count = (
        MDIEntry.query.filter(MDIEntry.status.notin_(COMPLETED_STATUSES)).count()
    )

    return render_template(
        "meeting_view.html",
        grouped_entries=grouped_entries,
        metrics_overview=metrics_overview,
        category_meta=CATEGORY_DISPLAY,
        status_badges=STATUS_BADGES,
        trend_labels=trend_labels,
        category_trends=category_trends,
        filters={
            "status": status_filter,
            "category": category_filter,
            "date": date_filter,
        },
        open_card_count=open_card_count,
        active_status_filter=ACTIVE_STATUS_FILTER,
        completed_statuses=COMPLETED_STATUSES,
        current_time=datetime.utcnow(),
    )


def _chart_date_range(days: int = 14):
    today = date.today()
    dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    labels = [day.strftime("%b %d") for day in dates]
    return dates, labels


def _build_category_trends(grouped_entries, date_range):
    trends = {}
    for category, entries in grouped_entries.items():
        totals = {day: 0 for day in date_range}
        open_totals = {day: 0 for day in date_range}
        for entry in entries:
            if entry.date_logged in totals:
                totals[entry.date_logged] += 1
                if entry.status not in COMPLETED_STATUSES:
                    open_totals[entry.date_logged] += 1
        trends[category] = {
            "logged": [totals[day] for day in date_range],
            "open": [open_totals[day] for day in date_range],
        }
    return trends


def register(bp):
    bp.add_url_rule("/mdi/meeting", view_func=meeting_view)

