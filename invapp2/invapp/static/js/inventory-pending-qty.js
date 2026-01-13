document.addEventListener("DOMContentLoaded", () => {
  const setQtyModal = document.getElementById("pendingSetQtyModal");
  const moveModal = document.getElementById("pendingMoveModal");
  const removeModal = document.getElementById("pendingRemoveModal");

  const wireModal = ({
    modal,
    openSelector,
    closeSelector,
    formId,
    itemLabelId,
    locationLabelId,
    lotLabelId,
  }) => {
    if (!modal) {
      return;
    }
    const form = document.getElementById(formId);
    const itemLabel = document.getElementById(itemLabelId);
    const locationLabel = document.getElementById(locationLabelId);
    const lotLabel = document.getElementById(lotLabelId);
    const closeButton = document.getElementById(closeSelector);

    const openModal = (button) => {
      if (!form) {
        return;
      }
      const baseAction = form.dataset.actionBase || "";
      form.reset();
      form.action = baseAction.replace(/0$/, button.dataset.receiptId || "0");
      itemLabel.textContent = button.dataset.itemLabel || "-";
      locationLabel.textContent = button.dataset.locationLabel || "-";
      lotLabel.textContent = button.dataset.lotNumber || "-";
      modal.style.display = "block";
    };

    document.querySelectorAll(openSelector).forEach((button) => {
      button.addEventListener("click", () => openModal(button));
    });

    closeButton?.addEventListener("click", () => {
      modal.style.display = "none";
    });

    window.addEventListener("click", (event) => {
      if (event.target === modal) {
        modal.style.display = "none";
      }
    });
  };

  wireModal({
    modal: setQtyModal,
    openSelector: ".pending-set-qty-btn",
    closeSelector: "closePendingSetQtyModal",
    formId: "pendingSetQtyForm",
    itemLabelId: "pending-set-item",
    locationLabelId: "pending-set-location",
    lotLabelId: "pending-set-lot",
  });

  wireModal({
    modal: moveModal,
    openSelector: ".pending-move-btn",
    closeSelector: "closePendingMoveModal",
    formId: "pendingMoveForm",
    itemLabelId: "pending-move-item",
    locationLabelId: "pending-move-location",
    lotLabelId: "pending-move-lot",
  });

  wireModal({
    modal: removeModal,
    openSelector: ".pending-remove-btn",
    closeSelector: "closePendingRemoveModal",
    formId: "pendingRemoveForm",
    itemLabelId: "pending-remove-item",
    locationLabelId: "pending-remove-location",
    lotLabelId: "pending-remove-lot",
  });
});
