function formatQty(value) {
  if (value === null || value === undefined) {
    return 'N/A';
  }
  const number = Number(value);
  if (Number.isNaN(number)) {
    return 'N/A';
  }
  return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function buildStatusCard(status, count, qtyTotal, link) {
  const label = count === 1 ? 'item' : 'items';
  const qtyValue = formatQty(qtyTotal);
  const card = `
    <a class="text-decoration-none" href="${link}">
      <div class="card h-100 border-0 shadow-sm">
        <div class="card-body d-flex flex-column gap-2">
          <div class="d-flex justify-content-between align-items-start">
            <span class="text-uppercase text-muted small">Status</span>
            <span class="badge bg-light text-dark border">${count} ${label}</span>
          </div>
          <h3 class="h6 mb-0">${status}</h3>
          <div class="text-muted small">
            Total qty: <span class="fw-semibold">${qtyValue}</span>
          </div>
        </div>
      </div>
    </a>
  `;
  return card;
}

function renderSummary(container, summary, itemShortagesUrl) {
  if (!summary || !summary.by_status) {
    container.innerHTML = '<div class="text-muted">No shortage data available.</div>';
    return;
  }

  const totalQty = formatQty(summary.total_qty);
  const lastUpdated = summary.last_updated
    ? new Date(summary.last_updated).toLocaleString()
    : 'N/A';
  const header = `
    <div class="d-flex flex-wrap gap-3 mb-3 align-items-center">
      <span class="badge bg-primary-subtle text-primary">
        ${summary.total_count} total ${summary.total_count === 1 ? 'shortage' : 'shortages'}
      </span>
      <span class="badge bg-light text-dark border">Total qty: ${totalQty}</span>
      <span class="text-muted small">Last updated ${lastUpdated}</span>
    </div>
  `;

  const cards = summary.by_status.map((bucket) => {
    const statusValues = Array.isArray(bucket.status_values) ? bucket.status_values : [];
    const statusParam = statusValues.length ? `?status=${statusValues.join(',')}` : '';
    const link = `${itemShortagesUrl}${statusParam}`;
    return buildStatusCard(bucket.status, bucket.count, bucket.qty_total, link);
  });

  container.innerHTML = `
    ${header}
    <div class="row row-cols-1 row-cols-md-2 row-cols-xl-3 g-3">
      ${cards.map((card) => `<div class="col">${card}</div>`).join('')}
    </div>
  `;
}

function showError(container, message) {
  container.innerHTML = `
    <div class="alert alert-warning mb-0">
      ${message}
    </div>
  `;
}

function initMaterialsSummary() {
  const summaryCard = document.getElementById('materials-summary');
  if (!summaryCard) return;

  const summaryUrl = summaryCard.dataset.summaryUrl;
  const itemShortagesUrl = summaryCard.dataset.itemShortagesUrl || '/purchasing';
  const content = summaryCard.querySelector('[data-summary-content]');

  if (!summaryUrl || !content) {
    return;
  }

  fetch(summaryUrl)
    .then((response) => {
      if (!response.ok) {
        throw new Error('Failed to load materials summary.');
      }
      return response.json();
    })
    .then((summary) => {
      renderSummary(content, summary, itemShortagesUrl);
    })
    .catch((error) => {
      console.error(error);
      showError(content, 'Unable to load material shortages right now.');
    });
}

document.addEventListener('DOMContentLoaded', initMaterialsSummary);
