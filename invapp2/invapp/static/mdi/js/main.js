const AUTO_REFRESH_INTERVAL = 60000;

function formatDate(dateStr) {
  if (!dateStr) return 'N/A';
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return 'N/A';
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
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

function truncateText(value, maxLength = 140) {
  if (!value) return '';
  const text = String(value);
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.substring(0, maxLength)}…`;
}

function parseJsonAttribute(attributeValue, fallback = {}) {
  if (!attributeValue) {
    return fallback;
  }
  try {
    return JSON.parse(attributeValue);
  } catch (error) {
    console.warn('Failed to parse attribute', error);
    return fallback;
  }
}

function renderCategoryDetails(entry) {
  switch (entry.category) {
    case 'Safety':
      return `<p class="text-muted small mb-0">Review focus: ${escapeHtml(entry.related_reference || 'Observation')}</p>`;
    case 'Quality': {
      const reference = entry.related_reference ? `Reference: ${escapeHtml(entry.related_reference)} &middot; ` : '';
      return `<p class="text-muted small mb-0">${reference}Logged ${formatDate(entry.date_logged)}</p>`;
    }
    case 'Delivery': {
      const notes = entry.notes ? `<p class="text-muted small mb-0">${escapeHtml(truncateText(entry.notes))}</p>` : '';
      return `
        <dl class="row row-cols-2 g-2 small text-muted mb-0">
          <dt class="col">Order</dt>
          <dd class="col mb-0">${escapeHtml(entry.order_number || 'N/A')}</dd>
          <dt class="col">Due</dt>
          <dd class="col mb-0">${formatDate(entry.due_date)}</dd>
        </dl>
        ${notes}
      `;
    }
    case 'People':
      return `
        <div class="d-flex flex-wrap gap-3 small text-muted mb-0">
          <span><strong>Absentees:</strong> ${entry.number_absentees != null ? entry.number_absentees : 'N/A'}</span>
          <span><strong>Open Roles:</strong> ${entry.open_positions != null ? entry.open_positions : 'N/A'}</span>
        </div>
      `;
    case 'Materials':
      return `
        <div class="text-muted small mb-0 d-flex flex-wrap gap-2">
          ${entry.item_part_number ? `<span>Part ${escapeHtml(entry.item_part_number)}</span>` : ''}
          ${entry.vendor ? `<span>Vendor ${escapeHtml(entry.vendor)}</span>` : ''}
          ${entry.eta ? `<span>ETA ${escapeHtml(entry.eta)}</span>` : ''}
          ${entry.po_number ? `<span>PO ${escapeHtml(entry.po_number)}</span>` : ''}
        </div>
      `;
    default:
      return '';
  }
}

function statusToBadge(status, statusBadges) {
  return statusBadges[status] || 'secondary';
}

function countMetricEntries(entries) {
  return entries.filter(
    (entry) =>
      entry.metric_name
      || entry.metric_value !== null
      || entry.metric_target !== null,
  ).length;
}

function renderEntryCard(entry, categoryMeta, statusBadges, entryUrl) {
  const meta = categoryMeta[entry.category] || {};
  const color = meta.color || 'primary';
  const statusClass = statusToBadge(entry.status, statusBadges);
  const entryTitle = (() => {
    if (entry.category === 'Delivery') {
      return entry.item_description || entry.description || 'Delivery Item';
    }
    if (entry.category === 'People') {
      return 'People Update';
    }
    return entry.description || 'No description provided';
  })();

  return `
    <a href="${entryUrl}?id=${entry.id}" class="mdi-entry-card text-reset">
      <div class="p-3 d-flex flex-column gap-2">
        <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
          <div class="d-flex align-items-center gap-2">
            ${entry.priority ? `<span class="mdi-pill text-${color} bg-${color}-subtle">${escapeHtml(entry.priority)}</span>` : ''}
            <small class="text-muted">#${String(entry.id).padStart(4, '0')}</small>
          </div>
          ${entry.status ? `<span class="mdi-pill mdi-pill--status bg-${statusClass}">${escapeHtml(entry.status)}</span>` : ''}
        </div>

        <h6 class="mdi-entry-card__title fw-semibold mb-0">${escapeHtml(entryTitle)}</h6>

        <div class="mdi-entry-meta text-muted d-flex flex-wrap gap-2 align-items-center">
          <span><i class="bi bi-person-circle me-1"></i>${escapeHtml(entry.owner || 'Unassigned')}</span>
          ${entry.area ? `<span class="text-muted">&middot; <i class="bi bi-geo-alt ms-1 me-1"></i>${escapeHtml(entry.area)}</span>` : ''}
        </div>

        <div class="mdi-entry-divider"></div>
        ${renderCategoryDetails(entry)}
        <div class="d-flex justify-content-between align-items-center text-muted small pt-1">
          <span><i class="bi bi-calendar-event me-1"></i>${formatDate(entry.date_logged)}</span>
          <span class="fw-semibold">View details →</span>
        </div>
      </div>
    </a>
  `;
}

function renderEmptyState(category) {
  return `
    <div class="mdi-empty-state text-center text-muted">
      <i class="bi bi-clipboard-data fs-3 mb-3 d-block"></i>
      <p class="mb-0">No entries logged for ${escapeHtml(category)} yet.</p>
      <small>Use “Add Item” to create the first record.</small>
    </div>
  `;
}

function refreshBoard(board) {
  const apiUrl = board.dataset.apiUrl || '/api/mdi_entries';
  const entryUrl = board.dataset.entryUrl || '/mdi/report';
  const statusBadges = parseJsonAttribute(board.dataset.statusBadges, {});
  const categoryMeta = parseJsonAttribute(board.dataset.categoryMeta, {});

  const params = new URLSearchParams(window.location.search);
  const queryString = params.toString();
  const url = queryString ? `${apiUrl}?${queryString}` : apiUrl;

  fetch(url)
    .then((response) => response.json())
    .then((entries) => {
      const grouped = entries.reduce((acc, entry) => {
        if (!acc[entry.category]) acc[entry.category] = [];
        acc[entry.category].push(entry);
        return acc;
      }, {});

      board.querySelectorAll('[data-category]').forEach((lane) => {
        const category = lane.dataset.category;
        const stack = lane.querySelector('[data-category-stack]');
        const countBadge = lane.querySelector('[data-category-count]');
        const metricBadge = lane.querySelector('[data-metric-count]');
        const items = grouped[category] || [];

        if (countBadge) {
          const label = items.length === 1 ? 'item' : 'items';
          countBadge.textContent = `${items.length} ${label}`;
        }

        if (metricBadge) {
          const metricCount = countMetricEntries(items);
          metricBadge.textContent = metricCount ? `${metricCount} metrics updated` : 'No metrics logged yet';
        }

        if (stack) {
          stack.innerHTML = items.length
            ? items
                .map((entry) => renderEntryCard(entry, categoryMeta, statusBadges, entryUrl))
                .join('')
            : renderEmptyState(category);
        }
      });
    })
    .catch((error) => console.error('Failed to refresh entries', error));
}

document.addEventListener('DOMContentLoaded', () => {
  const board = document.getElementById('category-grid');
  if (!board) {
    return;
  }

  const refresh = () => refreshBoard(board);
  const refreshButton = document.getElementById('refresh-btn');
  if (refreshButton) {
    refreshButton.addEventListener('click', refresh);
  }

  refresh();
  setInterval(refresh, AUTO_REFRESH_INTERVAL);
});
