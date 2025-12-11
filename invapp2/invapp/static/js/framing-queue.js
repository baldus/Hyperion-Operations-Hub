(() => {
  const saveButton = document.getElementById('framingOffsetSave');
  const offsetInput = document.getElementById('framingOffsetInput');
  const appliedOffsetValue = document.getElementById('appliedOffsetValue');
  const offsetUpdatedBadge = document.getElementById('offsetUpdatedBadge');

  function formatUpdatedText(updatedAt, updatedBy) {
    if (!updatedAt) {
      return 'Last updated: —';
    }
    const date = new Date(updatedAt);
    let text = `Last updated: ${date.toLocaleString()}`;
    if (updatedBy) {
      text += ` by ${updatedBy}`;
    }
    return text;
  }

  function recalculatePanelLengths(offsetValue) {
    if (Number.isNaN(offsetValue)) {
      return;
    }
    document.querySelectorAll('[data-panel-length-cell]').forEach((cell) => {
      const totalHeightRaw = cell.getAttribute('data-total-height');
      const totalHeight = totalHeightRaw ? parseFloat(totalHeightRaw) : NaN;
      const valueEl = cell.querySelector('.panel-length-value');
      const warningEl = cell.querySelector('.offset-warning');

      if (!valueEl) {
        return;
      }

      if (Number.isNaN(totalHeight)) {
        valueEl.textContent = '—';
        if (warningEl) warningEl.hidden = true;
        return;
      }

      const calculated = Math.max(0, totalHeight - offsetValue);
      valueEl.textContent = calculated.toFixed(2);
      if (warningEl) {
        warningEl.hidden = totalHeight >= offsetValue;
      }
    });
  }

  function updateAppliedOffset(value, updatedAt, updatedBy) {
    if (appliedOffsetValue) {
      appliedOffsetValue.textContent = value;
    }
    if (offsetInput) {
      offsetInput.value = value;
    }
    if (offsetUpdatedBadge) {
      offsetUpdatedBadge.textContent = formatUpdatedText(updatedAt, updatedBy);
    }
    recalculatePanelLengths(parseFloat(value));
  }

  if (window.framingQueueConfig && window.framingQueueConfig.offsetValue) {
    recalculatePanelLengths(parseFloat(window.framingQueueConfig.offsetValue));
  }

  if (!saveButton || !offsetInput) {
    return;
  }

  saveButton.addEventListener('click', async () => {
    const submittedValue = offsetInput.value;
    try {
      const response = await fetch('/production/framing/offset', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ value: submittedValue }),
      });

      const payload = await response.json();
      if (!response.ok) {
        const message = payload && payload.error ? payload.error : 'Unable to save offset';
        alert(message);
        return;
      }

      updateAppliedOffset(payload.value, payload.updated_at, payload.updated_by);
    } catch (error) {
      alert('Unable to save offset. Please try again.');
    }
  });
})();
