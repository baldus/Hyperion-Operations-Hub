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

function formatQty(value) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '0';
  }
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function renderMaterialsSummary(container, payload, shortagesUrl) {
  const body = container.querySelector('[data-materials-summary-body]');
  if (!body) {
    return;
  }
  const byStatus = Array.isArray(payload.by_status) ? payload.by_status : [];
  const totalCount = payload.total_count ?? 0;
  const totalQty = payload.total_qty ?? 0;

  const totalsMarkup = `
    <div class="mdi-materials-summary__totals">
      <div class="mdi-materials-summary__total">
        <div class="text-muted small">Total shortages</div>
        <div class="mdi-materials-summary__total-value">${formatQty(totalCount)}</div>
      </div>
      <div class="mdi-materials-summary__total">
        <div class="text-muted small">Total quantity needed</div>
        <div class="mdi-materials-summary__total-value">${formatQty(totalQty)}</div>
      </div>
    </div>
  `;

  const statusMarkup = byStatus.length
    ? `
      <div class="mdi-materials-summary__list">
        ${byStatus
          .map((item) => {
            const statusLabel = escapeHtml(item.status || 'Other');
            const countLabel = formatQty(item.count || 0);
            const qtyLabel = formatQty(item.qty_total || 0);
            const statusFilter = item.status_filter;
            const href = statusFilter
              ? `${shortagesUrl}?status=${encodeURIComponent(statusFilter)}`
              : shortagesUrl;
            const wrapperStart = shortagesUrl ? `<a href="${href}" class="d-block">` : '';
            const wrapperEnd = shortagesUrl ? '</a>' : '';
            return `
              <div class="mdi-materials-summary__item">
                ${wrapperStart}
                <div class="mdi-materials-summary__item-status">${statusLabel}</div>
                <div class="mdi-materials-summary__item-meta">
                  <span>${countLabel} shortages</span>
                  <span>• ${qtyLabel} qty</span>
                </div>
                ${wrapperEnd}
              </div>
            `;
          })
          .join('')}
      </div>
    `
    : '<div class="text-muted">No item shortages found.</div>';

  body.innerHTML = `${totalsMarkup}${statusMarkup}`;

  const updated = container.querySelector('[data-materials-last-updated]');
  if (updated && payload.last_updated) {
    const parsed = new Date(payload.last_updated);
    updated.textContent = Number.isNaN(parsed.getTime())
      ? ''
      : `Updated ${parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
  }
}

function showMaterialsSummaryError(container, message) {
  const body = container.querySelector('[data-materials-summary-body]');
  if (!body) {
    return;
  }
  body.innerHTML = `
    <div class="mdi-materials-summary__error">
      <i class="bi bi-exclamation-triangle"></i>
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function loadMaterialsSummary(container) {
  const summaryUrl = container.dataset.summaryUrl;
  if (!summaryUrl) {
    showMaterialsSummaryError(container, 'Summary endpoint unavailable.');
    return;
  }
  const shortagesUrl = container.dataset.shortagesUrl || '';

  fetch(summaryUrl)
    .then((response) => {
      if (!response.ok) {
        throw new Error('Failed to load materials summary');
      }
      return response.json();
    })
    .then((payload) => renderMaterialsSummary(container, payload, shortagesUrl))
    .catch((error) => {
      console.error('Unable to load materials summary', error);
      showMaterialsSummaryError(container, 'Unable to load item shortages right now.');
    });
}

document.addEventListener('DOMContentLoaded', () => {
  const container = document.querySelector('[data-materials-summary]');
  if (!container) {
    return;
  }
  loadMaterialsSummary(container);
});
