(function () {
  const board = document.querySelector('[data-priority-board]');
  const list = document.getElementById('priority-list');
  if (!board || !list) {
    return;
  }

  const statusEl = document.querySelector('[data-priority-status]');
  const config = window.orderPriorityConfig || {};
  const updateUrl = config.updateUrl;

  let draggingItem = null;

  const setStatus = (message, tone = 'info') => {
    if (!statusEl) return;
    statusEl.textContent = message || '';
    statusEl.dataset.tone = tone;
  };

  const reorderRanks = () => {
    list.querySelectorAll('.priority-card').forEach((card, index) => {
      const rank = card.querySelector('.priority-rank');
      if (rank) {
        rank.textContent = index + 1;
      }
    });
  };

  const persistOrder = async () => {
    if (!updateUrl) {
      setStatus('Cannot save priority changes right now.', 'danger');
      return;
    }

    const orderedIds = Array.from(list.querySelectorAll('[data-order-id]')).map(
      (element) => Number(element.dataset.orderId)
    );

    try {
      const response = await fetch(updateUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_ids: orderedIds }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || 'Unable to save the new order.');
      }

      setStatus('Priority updated.', 'success');
    } catch (error) {
      console.error(error);
      setStatus(error.message, 'danger');
    }
  };

  list.addEventListener('dragstart', (event) => {
    const item = event.target.closest('.priority-card');
    if (!item) return;

    draggingItem = item;
    item.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', item.dataset.orderId);
  });

  list.addEventListener('dragover', (event) => {
    if (!draggingItem) return;
    event.preventDefault();
    const target = event.target.closest('.priority-card');
    if (!target || target === draggingItem) return;

    const bounding = target.getBoundingClientRect();
    const offset = event.clientY - bounding.top;
    const shouldPlaceBefore = offset < bounding.height / 2;

    if (shouldPlaceBefore) {
      list.insertBefore(draggingItem, target);
    } else {
      list.insertBefore(draggingItem, target.nextSibling);
    }
  });

  list.addEventListener('dragend', () => {
    if (!draggingItem) return;
    draggingItem.classList.remove('is-dragging');
    draggingItem = null;
    reorderRanks();
    persistOrder();
  });
})();
