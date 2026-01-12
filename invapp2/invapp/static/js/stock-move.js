const moveForm = document.getElementById("move-form");

if (moveForm) {
  const fromLocation = document.getElementById("from-location");
  const toLocation = document.getElementById("to-location");
  const filterInput = document.getElementById("line-filter");
  const tableBody = document.getElementById("move-lines-body");
  const selectAllButton = document.getElementById("select-all");
  const clearSelectionButton = document.getElementById("clear-selection");
  const selectedCount = document.getElementById("selected-count");
  const totalLines = document.getElementById("total-lines");
  const totalQty = document.getElementById("total-qty");
  const validationMessage = document.getElementById("move-validation");
  const submitButton = document.getElementById("submit-move");
  const linesPayload = document.getElementById("move-lines-payload");
  const linesUrlTemplate = moveForm.dataset.linesUrl;

  let currentLines = [];

  const buildLinesUrl = (locationId) =>
    linesUrlTemplate.replace("/0/lines", `/${locationId}/lines`);

  const formatQty = (value) => {
    const qty = Number(value);
    if (!Number.isFinite(qty)) {
      return "0";
    }
    return qty.toLocaleString(undefined, { maximumFractionDigits: 3 });
  };

  const parseQty = (value) => {
    const qty = parseFloat(value);
    return Number.isFinite(qty) ? qty : null;
  };

  const setValidation = (message) => {
    validationMessage.textContent = message;
  };

  const updateSummary = () => {
    const rows = Array.from(tableBody.querySelectorAll("tr[data-line-id]"));
    const selectedRows = rows.filter(
      (row) => row.querySelector("input[type='checkbox']").checked
    );
    selectedCount.textContent = selectedRows.length.toString();
    totalLines.textContent = currentLines.length.toString();

    let qtyTotal = 0;
    let hasInvalid = false;

    selectedRows.forEach((row) => {
      const input = row.querySelector("input.move-qty-input");
      const available = parseFloat(row.dataset.available);
      const qty = parseQty(input.value);
      if (qty === null || qty <= 0 || qty > available) {
        input.classList.add("is-invalid");
        hasInvalid = true;
      } else {
        input.classList.remove("is-invalid");
        qtyTotal += qty;
      }
    });

    totalQty.textContent = formatQty(qtyTotal);

    let validation = "";
    const fromValue = fromLocation.value;
    const toValue = toLocation.value;
    if (!fromValue) {
      validation = "Select a source location to load inventory.";
    } else if (!toValue) {
      validation = "Select a destination location.";
    } else if (fromValue === toValue) {
      validation = "Destination location must be different from source.";
    } else if (!selectedRows.length) {
      validation = "Select at least one line to move.";
    } else if (hasInvalid) {
      validation = "Enter valid move quantities within available stock.";
    }

    setValidation(validation);
    submitButton.disabled = Boolean(validation);
  };

  const resetSelection = () => {
    tableBody.querySelectorAll("tr[data-line-id]").forEach((row) => {
      const checkbox = row.querySelector("input[type='checkbox']");
      const input = row.querySelector("input.move-qty-input");
      checkbox.checked = false;
      input.value = "";
      input.disabled = true;
      input.classList.remove("is-invalid");
      row.classList.remove("is-selected");
    });
    updateSummary();
  };

  const applyFilter = () => {
    const query = filterInput.value.trim().toLowerCase();
    tableBody.querySelectorAll("tr[data-line-id]").forEach((row) => {
      const match = row.dataset.search.includes(query);
      row.hidden = !match;
    });
  };

  const renderRows = (lines) => {
    tableBody.innerHTML = "";
    if (!lines.length) {
      tableBody.innerHTML =
        "<tr><td colspan='7'>No inventory found in this location.</td></tr>";
      updateSummary();
      return;
    }

    const fragment = document.createDocumentFragment();
    lines.forEach((line) => {
      const row = document.createElement("tr");
      const lineId = `${line.item_id}:${line.batch_id ?? "none"}`;
      const isNotCounted = line.presence_status === "present_not_counted";
      row.dataset.lineId = lineId;
      row.dataset.itemId = line.item_id;
      row.dataset.batchId = line.batch_id ?? "";
      row.dataset.available = line.on_hand;
      row.dataset.presence = line.presence_status || "";
      row.dataset.search = `${line.sku} ${line.name} ${line.lot_number}`.toLowerCase();

      row.innerHTML = `
        <td>
          <input type="checkbox" aria-label="Select ${line.sku}" ${isNotCounted ? "disabled" : ""}>
        </td>
        <td>${line.sku}</td>
        <td>${line.name}</td>
        <td>${line.lot_number || "-"}</td>
        <td>${isNotCounted ? '<span class="muted">Present (not counted)</span>' : formatQty(line.on_hand)}</td>
        <td>${line.unit || "-"}</td>
        <td>
          <input
            type="number"
            min="0"
            step="0.001"
            class="move-qty-input"
            inputmode="decimal"
            placeholder="0"
            disabled
          >
        </td>
      `;

      const checkbox = row.querySelector("input[type='checkbox']");
      const input = row.querySelector("input.move-qty-input");

      if (isNotCounted) {
        row.classList.add("is-not-counted");
        input.disabled = true;
      } else {
        checkbox.addEventListener("change", () => {
          input.disabled = !checkbox.checked;
          row.classList.toggle("is-selected", checkbox.checked);
          if (!checkbox.checked) {
            input.value = "";
            input.classList.remove("is-invalid");
          }
          updateSummary();
        });
      }

      if (!isNotCounted) {
        input.addEventListener("input", updateSummary);
      }

      fragment.appendChild(row);
    });
    tableBody.appendChild(fragment);
    applyFilter();
    updateSummary();
  };

  const loadLines = async (locationId) => {
    tableBody.innerHTML =
      "<tr><td colspan='7'>Loading inventory lines...</td></tr>";
    currentLines = [];
    updateSummary();
    try {
      const response = await fetch(buildLinesUrl(locationId));
      if (!response.ok) {
        throw new Error("Unable to load inventory lines.");
      }
      const data = await response.json();
      currentLines = data.lines || [];
      renderRows(currentLines);
    } catch (error) {
      tableBody.innerHTML =
        "<tr><td colspan='7'>Unable to load inventory lines.</td></tr>";
      setValidation("Unable to load inventory lines.");
      submitButton.disabled = true;
    }
  };

  fromLocation.addEventListener("change", () => {
    if (fromLocation.value) {
      loadLines(fromLocation.value);
    } else {
      currentLines = [];
      tableBody.innerHTML =
        "<tr><td colspan='7'>Select a source location to load inventory lines.</td></tr>";
      updateSummary();
    }
  });

  toLocation.addEventListener("change", updateSummary);
  filterInput.addEventListener("input", applyFilter);

  selectAllButton.addEventListener("click", () => {
    tableBody.querySelectorAll("tr[data-line-id]").forEach((row) => {
      if (row.hidden) {
        return;
      }
      const checkbox = row.querySelector("input[type='checkbox']");
      if (checkbox.disabled) {
        return;
      }
      if (!checkbox.checked) {
        checkbox.checked = true;
        row.classList.add("is-selected");
        const input = row.querySelector("input.move-qty-input");
        input.disabled = false;
      }
    });
    updateSummary();
  });

  clearSelectionButton.addEventListener("click", resetSelection);

  moveForm.addEventListener("submit", (event) => {
    updateSummary();
    if (submitButton.disabled) {
      event.preventDefault();
      return;
    }
    const selectedLines = [];
    tableBody.querySelectorAll("tr[data-line-id]").forEach((row) => {
      const checkbox = row.querySelector("input[type='checkbox']");
      if (!checkbox.checked) {
        return;
      }
      const input = row.querySelector("input.move-qty-input");
      selectedLines.push({
        item_id: row.dataset.itemId,
        batch_id: row.dataset.batchId || null,
        move_qty: input.value,
      });
    });
    linesPayload.value = JSON.stringify(selectedLines);
  });

  if (fromLocation.value) {
    loadLines(fromLocation.value);
  } else {
    updateSummary();
  }
}
