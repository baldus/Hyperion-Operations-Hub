document.addEventListener("DOMContentLoaded", () => {
  const removeModal = document.getElementById("removeFromLocationModal");
  if (removeModal) {
    const form = document.getElementById("removeFromLocationForm");
    const itemLabel = document.getElementById("remove-item-label");
    const locationLabel = document.getElementById("remove-location-label");
    const batchLabel = document.getElementById("remove-batch-label");
    const currentQty = document.getElementById("remove-current-qty");
    const batchNote = document.getElementById("remove-batch-note");
    const itemIdInput = document.getElementById("remove-item-id");
    const locationIdInput = document.getElementById("remove-location-id");
    const batchIdInput = document.getElementById("remove-batch-id");
    const quantityWrapper = document.getElementById("remove-partial-wrapper");
    const quantityInput = document.getElementById("remove-quantity");
    const allRadio = document.getElementById("remove-mode-all");
    const partialRadio = document.getElementById("remove-mode-partial");
    const closeButton = document.getElementById("closeRemoveFromLocationModal");

    const updateQuantityMode = () => {
      const isPartial = partialRadio.checked;
      quantityWrapper.hidden = !isPartial;
      quantityInput.required = isPartial;
    };

    const openModal = (button) => {
      if (!form) {
        return;
      }
      form.reset();
      const qtyValue = parseFloat(button.dataset.qty || "0");
      const batchCount = parseInt(button.dataset.batchCount || "0", 10);

      itemIdInput.value = button.dataset.itemId || "";
      locationIdInput.value = button.dataset.locationId || "";
      batchIdInput.value = button.dataset.batchId || "";
      itemLabel.textContent = button.dataset.itemLabel || "-";
      locationLabel.textContent = button.dataset.locationLabel || "-";
      batchLabel.textContent = button.dataset.lotNumber || "-";
      currentQty.textContent = isNaN(qtyValue) ? "0" : qtyValue;

      quantityInput.max = isNaN(qtyValue) ? "" : qtyValue;
      allRadio.checked = true;
      partialRadio.checked = false;
      updateQuantityMode();

      if (batchCount > 1) {
        partialRadio.disabled = true;
        batchNote.hidden = false;
      } else {
        partialRadio.disabled = false;
        batchNote.hidden = true;
      }

      removeModal.style.display = "block";
    };

    document.querySelectorAll(".remove-from-location-btn").forEach((button) => {
      button.addEventListener("click", () => openModal(button));
    });

    [allRadio, partialRadio].forEach((radio) => {
      radio.addEventListener("change", updateQuantityMode);
    });

    closeButton?.addEventListener("click", () => {
      removeModal.style.display = "none";
    });

    window.addEventListener("click", (event) => {
      if (event.target === removeModal) {
        removeModal.style.display = "none";
      }
    });
  }

  const removeAllModal = document.getElementById("removeAllFromLocationModal");
  if (removeAllModal) {
    const openAllButton = document.getElementById("openRemoveAllFromLocation");
    const closeAllButton = document.getElementById("closeRemoveAllFromLocation");

    openAllButton?.addEventListener("click", () => {
      removeAllModal.style.display = "block";
    });

    closeAllButton?.addEventListener("click", () => {
      removeAllModal.style.display = "none";
    });

    window.addEventListener("click", (event) => {
      if (event.target === removeAllModal) {
        removeAllModal.style.display = "none";
      }
    });
  }
});
