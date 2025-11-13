const autoRefreshInterval = 60000;

function formatNumber(value) {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return '';
  }
  return numeric.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: Math.abs(numeric % 1) > 0 ? 1 : 0,
  });
}

function escapeHtml(value) {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function fetchUpdates() {
  const grid = document.getElementById('category-grid');
  if (!grid) {
    return;
  }

  const params = new URLSearchParams(window.location.search);
  const queryString = params.toString();
  const url = queryString ? `/api/mdi_entries?${queryString}` : '/api/mdi_entries';

  fetch(url)
    .then((response) => response.json())
    .then((entries) => {
      const grouped = entries.reduce((acc, entry) => {
        if (!acc[entry.category]) acc[entry.category] = [];
        acc[entry.category].push(entry);
        return acc;
      }, {});

  grid.querySelectorAll('.card').forEach((card) => {
    const headerText = card.getAttribute('data-category');
    if (!headerText) return;
    const listGroup = card.querySelector('.list-group');
    const countBadge = card.querySelector('.item-count');
    const metricsSummary = card.querySelector('.metrics-summary');
    const metricsBadge = metricsSummary ? metricsSummary.querySelector('.metrics-count') : null;

    const categoryEntries = grouped[headerText] || [];
    if (countBadge) {
      countBadge.textContent = `${categoryEntries.length} items`;
    }

    const metricCount = categoryEntries.filter(
      (entry) =>
        entry.metric_name
        || (entry.metric_value !== null && entry.metric_value !== undefined)
        || (entry.metric_target !== null && entry.metric_target !== undefined),
    ).length;
    if (metricsSummary) {
      if (metricCount > 0) {
        if (metricsBadge) {
          metricsBadge.textContent = `${metricCount} metrics`;
        } else {
          const badge = document.createElement('span');
          badge.className = 'badge bg-white text-dark border metrics-count';
          badge.textContent = `${metricCount} metrics`;
          metricsSummary.appendChild(badge);
        }
      } else if (metricsBadge) {
        metricsBadge.remove();
      }
    }

        if (listGroup) {
          listGroup.innerHTML = categoryEntries
            .map(
              (entry) => `
                <a href="/mdi/report?id=${entry.id}" class="list-group-item list-group-item-action py-3">
                  <div class="d-flex w-100 justify-content-between">
                    <h6 class="mb-1">${entry.description.substring(0, 80)}${entry.description.length > 80 ? '…' : ''}</h6>
                    <span class="badge bg-${statusToBadge(entry.status)}">${entry.status}</span>
                  </div>
                  <p class="mb-1 text-muted small">
                    <strong>Owner:</strong> ${entry.owner || 'Unassigned'} &middot;
                    <strong>Priority:</strong> ${entry.priority || 'N/A'}
                  </p>
                  <div class="d-flex justify-content-between align-items-center">
                    <small class="text-muted">Area: ${entry.area || 'N/A'}</small>
                    <small class="text-muted">Logged: ${formatDate(entry.date_logged)}</small>
                  </div>
                  ${entry.metric_name || entry.metric_value !== null || entry.metric_target !== null ? `
                    <div class="mt-2">
                      <div class="text-muted small mb-1">Metric</div>
                      <div class="d-flex flex-wrap align-items-center gap-2 small">
                        ${entry.metric_name ? `<span class="fw-semibold">${escapeHtml(entry.metric_name)}</span>` : ''}
                        ${entry.metric_value !== null && entry.metric_value !== undefined ? `<span class="badge bg-light text-primary border metric-value">${formatNumber(entry.metric_value)}${entry.metric_unit ? ` ${escapeHtml(entry.metric_unit)}` : ''}</span>` : entry.metric_unit ? `<span class="badge bg-light text-primary border metric-value">${escapeHtml(entry.metric_unit)}</span>` : ''}
                        ${entry.metric_target !== null && entry.metric_target !== undefined ? `<span class="text-muted">Target: ${formatNumber(entry.metric_target)}${entry.metric_unit ? ` ${escapeHtml(entry.metric_unit)}` : ''}</span>` : ''}
                      </div>
                    </div>
                  ` : ''}
                </a>
              `
            )
            .join('');
          if (!categoryEntries.length) {
            listGroup.innerHTML =
              '<div class="p-4 text-center text-muted empty-state">No entries logged.</div>';
          }
        }
      });
    })
    .catch((error) => console.error('Failed to refresh entries', error));
}

function statusToBadge(status) {
  switch (status) {
    case 'In Progress':
      return 'warning';
    case 'Closed':
      return 'success';
    case 'Open':
    default:
      return 'secondary';
  }
}

function formatDate(dateStr) {
  if (!dateStr) return 'N/A';
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return 'N/A';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

fetchUpdates();
setInterval(fetchUpdates, autoRefreshInterval);

