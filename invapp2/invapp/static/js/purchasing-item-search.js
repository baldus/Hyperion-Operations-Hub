(() => {
  const searchInput = document.getElementById('item-search');
  if (!searchInput) {
    return;
  }

  const resultsPanel = document.getElementById('item-search-results');
  const statusPanel = document.getElementById('item-search-status');
  const itemIdField = document.getElementById('item_id');
  const itemNumberField = document.getElementById('item_number');
  const itemNameField = document.getElementById('item_name');
  const itemDescriptionField = document.getElementById('item_description');
  const titleField = document.getElementById('title');
  const descriptionField = document.getElementById('description');
  const unitField = document.getElementById('unit');
  const quantityField = document.getElementById('quantity');
  const supplierField = document.getElementById('supplier_name');
  const notesField = document.getElementById('notes');
  const summaryPanel = document.getElementById('item-search-summary');
  const summaryTotal = document.getElementById('item-search-total');
  const summaryLocations = document.getElementById('item-search-locations');

  let results = [];
  let selectedIndex = -1;
  let debounceTimer = null;
  let activeController = null;

  const clearStatus = () => {
    if (statusPanel) {
      statusPanel.textContent = '';
    }
  };

  const closeResults = () => {
    results = [];
    selectedIndex = -1;
    if (resultsPanel) {
      resultsPanel.innerHTML = '';
      resultsPanel.classList.remove('is-visible');
    }
    clearStatus();
    searchInput.setAttribute('aria-expanded', 'false');
  };

  const clearSummary = () => {
    if (summaryPanel) {
      summaryPanel.classList.remove('is-visible');
    }
    if (summaryTotal) {
      summaryTotal.textContent = '—';
    }
    if (summaryLocations) {
      summaryLocations.innerHTML = '';
    }
  };

  const setStatus = (message) => {
    if (statusPanel) {
      statusPanel.textContent = message;
    }
  };

  const formatMeta = (item) => {
    const metaParts = [];
    if (item.preferred_supplier_name) {
      metaParts.push(item.preferred_supplier_name);
    }
    if (item.category) {
      metaParts.push(item.category);
    }
    return metaParts.join(' · ');
  };

  const renderResults = (items) => {
    if (!resultsPanel) {
      return;
    }

    resultsPanel.innerHTML = '';
    if (!items.length) {
      resultsPanel.classList.remove('is-visible');
      setStatus('No results');
      searchInput.setAttribute('aria-expanded', 'false');
      return;
    }

    items.forEach((item, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'item-search-option';
      option.setAttribute('role', 'option');
      option.dataset.index = String(index);

      const title = document.createElement('div');
      title.className = 'item-search-title';
      title.innerHTML = `<strong>${item.item_number}</strong>`;

      const description = document.createElement('div');
      description.className = 'item-search-description secondary-text';
      description.textContent = item.description || item.name || '';

      option.appendChild(title);
      option.appendChild(description);

      const meta = formatMeta(item);
      if (meta) {
        const metaLine = document.createElement('div');
        metaLine.className = 'item-search-meta secondary-text';
        metaLine.textContent = meta;
        option.appendChild(metaLine);
      }

      option.addEventListener('click', () => selectItem(index));
      resultsPanel.appendChild(option);
    });

    resultsPanel.classList.add('is-visible');
    clearStatus();
    searchInput.setAttribute('aria-expanded', 'true');
  };

  const updateSelection = (newIndex) => {
    if (!resultsPanel) {
      return;
    }
    const options = resultsPanel.querySelectorAll('.item-search-option');
    options.forEach((option, index) => {
      if (index === newIndex) {
        option.classList.add('is-active');
        option.setAttribute('aria-selected', 'true');
      } else {
        option.classList.remove('is-active');
        option.setAttribute('aria-selected', 'false');
      }
    });
  };

  const fillIfEmpty = (field, value) => {
    if (!field || !value) {
      return;
    }
    if (field.value.trim() === '') {
      field.value = value;
    }
  };

  const selectItem = (index) => {
    const item = results[index];
    if (!item) {
      return;
    }

    searchInput.value = item.item_number;
    if (itemIdField) {
      itemIdField.value = item.id;
    }
    if (itemNumberField) {
      itemNumberField.value = item.item_number;
    }

    if (itemNameField) {
      itemNameField.value = item.name || '';
    }
    if (itemDescriptionField) {
      itemDescriptionField.value = item.description || item.name || '';
    }

    if (titleField) {
      const titleParts = [item.item_number, item.name].filter(Boolean);
      titleField.value = titleParts.join(' – ');
    }
    if (descriptionField) {
      descriptionField.value = item.description || item.name || '';
    }

    fillIfEmpty(unitField, item.uom);
    if (quantityField && item.default_reorder_qty && quantityField.value.trim() === '') {
      quantityField.value = item.default_reorder_qty;
    }
    fillIfEmpty(supplierField, item.preferred_supplier_name);

    if (notesField && notesField.value.trim() === '') {
      const details = [item.item_number, item.description || item.name].filter(Boolean).join(' - ');
      notesField.value = details ? `Item details: ${details}` : '';
    }

    if (summaryPanel) {
      summaryPanel.classList.add('is-visible');
    }
    if (summaryTotal) {
      summaryTotal.textContent = `${item.on_hand_total ?? 0}`;
    }
    if (summaryLocations) {
      summaryLocations.innerHTML = '';
      const locations = Array.isArray(item.locations) ? item.locations : [];
      if (!locations.length) {
        const empty = document.createElement('li');
        empty.className = 'item-search-location muted';
        empty.textContent = 'No locations found';
        summaryLocations.appendChild(empty);
      } else {
        locations.forEach((location) => {
          const entry = document.createElement('li');
          entry.className = 'item-search-location';
          const description = location.description ? ` (${location.description})` : '';
          entry.textContent = `${location.code}${description}: ${location.quantity}`;
          summaryLocations.appendChild(entry);
        });
      }
    }

    closeResults();
  };

  const fetchResults = (query) => {
    if (activeController) {
      activeController.abort();
    }

    const url = searchInput.dataset.searchUrl;
    if (!url) {
      return;
    }

    activeController = new AbortController();
    fetch(`${url}?q=${encodeURIComponent(query)}`, {
      headers: { Accept: 'application/json' },
      signal: activeController.signal,
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Unable to fetch results');
        }
        return response.json();
      })
      .then((data) => {
        results = Array.isArray(data) ? data : [];
        selectedIndex = -1;
        renderResults(results);
      })
      .catch((error) => {
        if (error.name === 'AbortError') {
          return;
        }
        closeResults();
        setStatus('Unable to load results. Check your connection and try again.');
      });
  };

  searchInput.addEventListener('input', () => {
    if (itemIdField) {
      itemIdField.value = '';
    }
    if (itemNumberField) {
      itemNumberField.value = '';
    }
    clearSummary();

    const query = searchInput.value.trim();
    if (query.length < 2) {
      closeResults();
      return;
    }

    clearStatus();
    if (debounceTimer) {
      window.clearTimeout(debounceTimer);
    }

    debounceTimer = window.setTimeout(() => {
      fetchResults(query);
    }, 250);
  });

  searchInput.addEventListener('keydown', (event) => {
    if (!results.length) {
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      selectedIndex = (selectedIndex + 1) % results.length;
      updateSelection(selectedIndex);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      selectedIndex = (selectedIndex - 1 + results.length) % results.length;
      updateSelection(selectedIndex);
    } else if (event.key === 'Enter') {
      if (selectedIndex >= 0) {
        event.preventDefault();
        selectItem(selectedIndex);
      }
    } else if (event.key === 'Escape') {
      closeResults();
    }
  });

  document.addEventListener('click', (event) => {
    if (!searchInput.contains(event.target) && !resultsPanel.contains(event.target)) {
      closeResults();
    }
  });
})();
