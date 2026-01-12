document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-move-form]");
  if (!form) {
    return;
  }

  const linesUrlTemplate = form.dataset.linesUrl;
  const fromSelect = form.querySelector("[data-from-location]");
  const toSelect = form.querySelector("[data-to-location]");
  const filterInput = form.querySelector("[data-filter-input]");
  const tableBody = form.querySelector("[data-lines-body]");
  const emptyState = form.querySelector("[data-empty-state]");
  const loadingState = form.querySelector("[data-loading-state]");
  const selectAllButton = form.querySelector("[data-select-all]");
  const clearSelectionButton = form.querySelector("[data-clear-selection]");
  const selectedCountEl = form.querySelector("[data-selected-count]");
  const selectedQtyEl = form.querySelector("[data-selected-qty]");
  const statusEl = form.querySelector("[data-form-status]");
  const linesInput = document.getElementById("move-lines-input");
  const submitButton = form.querySelector("[data-submit-btn]");

  let lines = [];
  let filterText = "";

  const buildLinesUrl = (locationId) =>
    linesUrlTemplate.replace(/0$/, String(locationId));

  const setLoading = (isLoading) => {
    if (loadingState) {
      loadingState.hidden = !isLoading;
    }
  };

  const setEmptyState = (isEmpty) => {
    if (emptyState) {
      emptyState.hidden = !isEmpty;
    }
  };

  const formatQty = (qty) => Number(qty || 0).toFixed(3);

  const isLineValid = (line) => {
    if (!line.selected) {
      return true;
    }
    const qty = Number(line.moveQty);
    if (!qty || Number.isNaN(qty)) {
      return false;
    }
    return qty > 0 && qty <= line.onHand;
  };

  const buildLinesPayload = () =>
    lines
      .filter((line) => line.selected && isLineValid(line))
      .map((line) => ({
        item_id: line.item_id,
        batch_id: line.batch_id,
        move_qty: line.moveQty,
      }));

  const updateSummary = () => {
    const selectedLines = lines.filter((line) => line.selected);
    const invalidSelected = selectedLines.filter((line) => !isLineValid(line));
    const totalQty = selectedLines.reduce((sum, line) => {
      const qty = Number(line.moveQty);
      if (!qty || Number.isNaN(qty)) {
        return sum;
      }
      return sum + qty;
    }, 0);

    if (selectedCountEl) {
      selectedCountEl.textContent = selectedLines.length;
    }
    if (selectedQtyEl) {
      selectedQtyEl.textContent = formatQty(totalQty);
    }

    const hasLocations =
      fromSelect.value &&
      toSelect.value &&
      fromSelect.value !== toSelect.value;
    const hasValidLines =
      selectedLines.length > 0 && invalidSelected.length === 0;

    if (statusEl) {
      if (!fromSelect.value || !toSelect.value) {
        statusEl.textContent = "Select both From and To locations to continue.";
      } else if (fromSelect.value === toSelect.value) {
        statusEl.textContent =
          "From and To locations must be different.";
      } else if (!selectedLines.length) {
        statusEl.textContent =
          "Select at least one line and enter quantities to move.";
      } else if (invalidSelected.length) {
        statusEl.textContent =
          "Fix quantities so they are greater than 0 and within available stock.";
      } else {
        statusEl.textContent = "Ready to submit transfer.";
      }
    }

    const canSubmit = hasLocations && hasValidLines;
    submitButton.disabled = !canSubmit;
    linesInput.value = JSON.stringify(buildLinesPayload());
  };

  const renderLines = () => {
    tableBody.innerHTML = "";
    const filtered = lines.filter((line) =>
      line.searchText.includes(filterText)
    );

    setEmptyState(!lines.length);

    filtered.forEach((line) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>
          <input type="checkbox" class="move-line-checkbox">
        </td>
        <td>${line.sku}</td>
        <td>${line.name}</td>
        <td>${line.lot_number}</td>
        <td>${line.on_hand}</td>
        <td>${line.unit || "-"}</td>
        <td>
          <input
            type="number"
            class="move-qty-input"
            step="0.001"
            min="0.001"
            max="${line.on_hand}"
            placeholder="0.000"
          >
        </td>
      `;

      const checkbox = row.querySelector(".move-line-checkbox");
      const qtyInput = row.querySelector(".move-qty-input");

      checkbox.checked = line.selected;
      qtyInput.value = line.moveQty;
      qtyInput.disabled = !line.selected;
      if (!isLineValid(line) && line.selected) {
        qtyInput.classList.add("is-invalid");
      } else {
        qtyInput.classList.remove("is-invalid");
      }

      checkbox.addEventListener("change", () => {
        line.selected = checkbox.checked;
        if (!line.selected) {
          line.moveQty = "";
          qtyInput.value = "";
          qtyInput.classList.remove("is-invalid");
        }
        qtyInput.disabled = !line.selected;
        updateSummary();
      });

      qtyInput.addEventListener("input", () => {
        line.moveQty = qtyInput.value;
        if (!isLineValid(line)) {
          qtyInput.classList.add("is-invalid");
        } else {
          qtyInput.classList.remove("is-invalid");
        }
        updateSummary();
      });

      tableBody.appendChild(row);
    });

    updateSummary();
  };

  const loadLines = async (locationId) => {
    if (!locationId) {
      lines = [];
      renderLines();
      return;
    }

    setLoading(true);
    setEmptyState(false);
    try {
      const response = await fetch(buildLinesUrl(locationId), {
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) {
        throw new Error("Failed to load lines");
      }
      const payload = await response.json();
      lines = (payload.lines || []).map((line) => ({
        ...line,
        onHand: Number(line.on_hand),
        selected: false,
        moveQty: "",
        searchText: `${line.sku} ${line.name} ${line.lot_number}`
          .toLowerCase(),
      }));
    } catch (error) {
      lines = [];
    } finally {
      setLoading(false);
      renderLines();
    }
  };

  filterInput.addEventListener("input", (event) => {
    filterText = event.target.value.trim().toLowerCase();
    renderLines();
  });

  fromSelect.addEventListener("change", () => {
    filterInput.value = "";
    filterText = "";
    loadLines(fromSelect.value);
  });

  toSelect.addEventListener("change", updateSummary);

  selectAllButton.addEventListener("click", () => {
    lines.forEach((line) => {
      line.selected = true;
    });
    renderLines();
  });

  clearSelectionButton.addEventListener("click", () => {
    lines.forEach((line) => {
      line.selected = false;
      line.moveQty = "";
    });
    renderLines();
  });

  form.addEventListener("submit", (event) => {
    updateSummary();
    if (submitButton.disabled) {
      event.preventDefault();
    }
  });

  if (fromSelect.value) {
    loadLines(fromSelect.value);
  }
});
