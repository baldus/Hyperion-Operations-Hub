# MDI Meeting Dashboard

The MDI Meeting Dashboard is the command center for daily production standups.
It lives at `/mdi/meeting` and is designed to highlight open work while keeping
metrics visible to the entire team.

## Default view: Active items only
* On load, the dashboard automatically applies the **Active (not
  Closed/Received)** status filter. Items marked `Closed` or `Received` are
  hidden so facilitators see only work that still needs attention.
* The filter selection is written to the URL query string and reused by the
  auto-refresh loop, ensuring live updates stay scoped to the active backlog.

## Navigation and controls
* **Filter bar** – Adjust category, status, and date filters above the grid.
  Submitting the form updates the query string so refreshes and shared links keep
  the same scope.
* **Toolbar** – Create new items ("Add Item"), import/export CSVs, and manually
  refresh the board. Uploading a CSV posts to `/mdi/report_import_csv` for bulk
  updates.
* **Auto-refresh** – The board polls `/api/mdi_entries` every 60 seconds and
  re-renders the lanes without a page reload.

## Category lanes
Each lane represents a pillar (Safety, Quality, Delivery, People, Materials):

* **Header badges** summarize item counts and how many metrics were updated for
  the category.
* **Cards** surface the most relevant context for the category—for example,
  Delivery due dates, People absences and open roles, or Materials vendors and
  PO numbers.
* **Mark Complete** instantly sets the status to `Closed` and refreshes the
  board. When the Active filter is applied the card disappears after completion.

## Data sources
* Entries are stored in `MDIEntry` records (see `invapp/mdi/models.py`).
* The dashboard fetches data from `/api/mdi_entries`, passing the same filters
  visible in the UI so server-side and client-side views remain aligned.

Use this view as the shared, at-a-glance agenda for the daily huddle: it keeps
attention on open work, encourages quick updates, and pairs live metrics with
actions the team can take immediately.
