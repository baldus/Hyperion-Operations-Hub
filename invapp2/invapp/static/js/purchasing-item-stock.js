(() => {
  const summary = document.querySelector('.item-stock-summary');
  if (!summary) {
    return;
  }

  const stockUrl = summary.dataset.stockUrl;
  const totalTarget = summary.querySelector('[data-stock-total]');
  const locationsTarget = summary.querySelector('[data-stock-locations]');
  const updatedTarget = summary.querySelector('[data-stock-updated]');

  if (!stockUrl || !totalTarget || !locationsTarget || !updatedTarget) {
    return;
  }

  const formatTimestamp = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const renderLocations = (locations) => {
    locationsTarget.innerHTML = '';
    if (!Array.isArray(locations) || locations.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'item-stock-location muted';
      empty.textContent = 'No locations found';
      locationsTarget.appendChild(empty);
      return;
    }

    locations.forEach((location) => {
      const entry = document.createElement('li');
      entry.className = 'item-stock-location';
      const description = location.description ? ` (${location.description})` : '';
      entry.textContent = `${location.code}${description}: ${location.quantity}`;
      locationsTarget.appendChild(entry);
    });
  };

  const updateStock = () => {
    fetch(stockUrl, { headers: { Accept: 'application/json' } })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Unable to fetch stock');
        }
        return response.json();
      })
      .then((data) => {
        totalTarget.textContent = `${data.on_hand_total ?? 0}`;
        renderLocations(data.locations || []);
        updatedTarget.textContent = `Updated ${formatTimestamp()}`;
      })
      .catch(() => {
        updatedTarget.textContent = 'Unable to refresh inventory right now.';
      });
  };

  updateStock();
  window.setInterval(updateStock, 30000);
})();
