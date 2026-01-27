document.addEventListener('DOMContentLoaded', () => {
  const picker = document.querySelector('.location-picker');
  if (!picker) {
    return;
  }

  const input = picker.querySelector('.location-picker-text');
  const hiddenInput = picker.querySelector('#location-id-input');
  const dropdown = picker.querySelector('.location-picker-dropdown');
  const clearButton = picker.querySelector('.location-picker-clear');
  const searchUrl = picker.dataset.searchUrl;
  const unassignedId = picker.dataset.unassignedId;
  const unassignedLabel = picker.dataset.unassignedLabel || 'UNASSIGNED';
  const selectedLabel = picker.dataset.selectedLabel || unassignedLabel;
  const debounceMs = 200;

  if (input) {
    input.value = selectedLabel;
  }

  let results = [];
  let activeIndex = -1;
  let isOpen = false;
  let debounceTimer;

  const openDropdown = () => {
    if (!dropdown) return;
    dropdown.classList.add('open');
    if (input) {
      input.setAttribute('aria-expanded', 'true');
    }
    isOpen = true;
  };

  const closeDropdown = () => {
    if (!dropdown) return;
    dropdown.classList.remove('open');
    dropdown.innerHTML = '';
    activeIndex = -1;
    if (input) {
      input.setAttribute('aria-expanded', 'false');
    }
    isOpen = false;
  };

  const setSelection = (location) => {
    if (!location) return;
    if (hiddenInput) {
      hiddenInput.value = location.id || '';
    }
    if (input) {
      input.value = location.label || location.code || '';
    }
    closeDropdown();
  };

  const renderResults = (items) => {
    if (!dropdown) return;
    results = Array.isArray(items) ? items : [];
    if (!results.length) {
      dropdown.innerHTML = '<div class="location-picker-status">No locations found.</div>';
      return;
    }

    const options = results
      .map((item, index) => {
        const description = item.description ? `<span class="location-picker-desc">${item.description}</span>` : '';
        return `
          <div
            class="location-picker-option"
            role="option"
            data-index="${index}"
          >
            <span class="location-picker-code">${item.code}</span>
            ${description}
          </div>
        `;
      })
      .join('');
    dropdown.innerHTML = options;

    dropdown.querySelectorAll('.location-picker-option').forEach((option) => {
      option.addEventListener('mousedown', (event) => {
        event.preventDefault();
      });
      option.addEventListener('click', (event) => {
        const index = Number(event.currentTarget.dataset.index);
        const selected = results[index];
        setSelection(selected);
      });
    });
  };

  const fetchResults = async (query) => {
    if (!dropdown || !searchUrl) return;
    dropdown.innerHTML = '<div class="location-picker-status">Loading…</div>';
    try {
      const response = await fetch(`${searchUrl}?q=${encodeURIComponent(query)}`);
      if (!response.ok) {
        dropdown.innerHTML = '<div class="location-picker-status">Unable to load locations.</div>';
        return;
      }
      const data = await response.json();
      renderResults(data);
    } catch (error) {
      dropdown.innerHTML = '<div class="location-picker-status">Unable to load locations.</div>';
    }
  };

  const scheduleSearch = (query) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => fetchResults(query), debounceMs);
  };

  const highlightActive = () => {
    if (!dropdown) return;
    dropdown.querySelectorAll('.location-picker-option').forEach((option, index) => {
      if (index === activeIndex) {
        option.classList.add('active');
        option.scrollIntoView({ block: 'nearest' });
      } else {
        option.classList.remove('active');
      }
    });
  };

  if (input) {
    input.addEventListener('input', (event) => {
      const value = event.target.value.trim();
      openDropdown();
      scheduleSearch(value);
    });

    input.addEventListener('focus', () => {
      openDropdown();
      scheduleSearch(input.value.trim());
    });

    input.addEventListener('keydown', (event) => {
      if (!isOpen) {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          openDropdown();
          scheduleSearch(input.value.trim());
        }
        return;
      }

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        activeIndex = Math.min(activeIndex + 1, results.length - 1);
        highlightActive();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        highlightActive();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (results[activeIndex]) {
          setSelection(results[activeIndex]);
        }
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeDropdown();
      }
    });

    input.addEventListener('blur', () => {
      if (!input.value.trim() && unassignedId) {
        setSelection({ id: unassignedId, label: unassignedLabel, code: unassignedLabel });
      }
    });
  }

  if (clearButton) {
    clearButton.addEventListener('click', () => {
      if (unassignedId) {
        setSelection({ id: unassignedId, label: unassignedLabel, code: unassignedLabel });
      } else {
        if (hiddenInput) {
          hiddenInput.value = '';
        }
        if (input) {
          input.value = '';
        }
      }
    });
  }

  document.addEventListener('click', (event) => {
    if (!picker.contains(event.target)) {
      closeDropdown();
    }
  });
});
