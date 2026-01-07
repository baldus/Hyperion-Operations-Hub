(() => {
  const layoutDataEl = document.getElementById("home-layout-data");
  if (!layoutDataEl) {
    return;
  }

  let layoutData;
  try {
    layoutData = JSON.parse(layoutDataEl.textContent);
  } catch (error) {
    return;
  }

  const grid = document.getElementById("home-summary-grid");
  if (!grid) {
    return;
  }

  const customizeToggle = document.getElementById("home-customize-toggle");
  const saveButton = document.getElementById("home-customize-save");
  const cancelButton = document.getElementById("home-customize-cancel");
  const availablePanel = document.getElementById("home-cube-panel");
  const availableList = document.getElementById("home-cube-available-list");

  if (!customizeToggle || !saveButton || !cancelButton || !availablePanel || !availableList) {
    return;
  }

  const cubeMeta = new Map();
  layoutData.layout.forEach((cube) => {
    cubeMeta.set(cube.key, cube);
  });

  let isCustomizing = false;
  let draggedCard = null;

  const updateAvailableEmpty = () => {
    const emptyState = availableList.querySelector(".home-cube-empty");
    if (!emptyState) {
      return;
    }
    const hasItems = availableList.querySelectorAll(".home-cube-item").length > 0;
    emptyState.classList.toggle("is-hidden", hasItems);
  };

  const addAvailableItem = (key) => {
    if (availableList.querySelector(`[data-cube-key="${key}"]`)) {
      return;
    }
    const cube = cubeMeta.get(key);
    if (!cube) {
      return;
    }
    const item = document.createElement("li");
    item.className = "home-cube-item";
    item.dataset.cubeKey = key;

    const info = document.createElement("div");
    info.className = "home-cube-info";
    const title = document.createElement("strong");
    title.textContent = cube.display_name;
    info.appendChild(title);
    if (cube.description) {
      const description = document.createElement("span");
      description.textContent = cube.description;
      info.appendChild(description);
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "action-btn secondary";
    button.dataset.action = "add-cube";
    button.textContent = "Add";

    item.appendChild(info);
    item.appendChild(button);
    availableList.appendChild(item);
    updateAvailableEmpty();
  };

  const removeAvailableItem = (key) => {
    const item = availableList.querySelector(`[data-cube-key="${key}"]`);
    if (item) {
      item.remove();
      updateAvailableEmpty();
    }
  };

  const moveCardToVisibleZone = (card) => {
    const firstHidden = grid.querySelector(".home-summary-card.is-hidden");
    if (firstHidden) {
      grid.insertBefore(card, firstHidden);
    } else {
      grid.appendChild(card);
    }
  };

  const hideCube = (card) => {
    const key = card.dataset.cubeKey;
    if (!key) {
      return;
    }
    card.classList.add("is-hidden");
    card.setAttribute("aria-hidden", "true");
    card.draggable = false;
    grid.appendChild(card);
    addAvailableItem(key);
  };

  const showCube = (key) => {
    const card = grid.querySelector(`.home-summary-card[data-cube-key="${key}"]`);
    if (!card) {
      return;
    }
    card.classList.remove("is-hidden");
    card.removeAttribute("aria-hidden");
    if (isCustomizing) {
      card.draggable = true;
    }
    moveCardToVisibleZone(card);
    removeAvailableItem(key);
  };

  const setCustomizeMode = (enabled) => {
    isCustomizing = enabled;
    document.body.classList.toggle("home-customize-mode", enabled);
    customizeToggle.hidden = enabled;
    saveButton.hidden = !enabled;
    cancelButton.hidden = !enabled;
    availablePanel.hidden = !enabled;

    grid.querySelectorAll(".home-summary-card").forEach((card) => {
      const hidden = card.classList.contains("is-hidden");
      card.draggable = enabled && !hidden;
      card.setAttribute("aria-grabbed", enabled && !hidden ? "false" : "false");
    });
  };

  const buildLayoutPayload = () => {
    const cards = Array.from(grid.querySelectorAll(".home-summary-card"));
    return cards.map((card) => ({
      key: card.dataset.cubeKey,
      visible: !card.classList.contains("is-hidden"),
    }));
  };

  customizeToggle.addEventListener("click", () => setCustomizeMode(true));

  cancelButton.addEventListener("click", () => {
    window.location.reload();
  });

  saveButton.addEventListener("click", async () => {
    const layout = buildLayoutPayload();
    saveButton.disabled = true;
    try {
      const response = await fetch("/api/home_layout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ layout }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload.error || "Unable to save layout.";
        window.alert(detail);
        return;
      }
      window.location.reload();
    } catch (error) {
      window.alert("Unable to save layout.");
    } finally {
      saveButton.disabled = false;
    }
  });

  grid.addEventListener("click", (event) => {
    if (!isCustomizing) {
      return;
    }
    const button = event.target.closest("[data-action='hide-cube']");
    if (!button) {
      return;
    }
    const card = button.closest(".home-summary-card");
    if (!card) {
      return;
    }
    hideCube(card);
  });

  availableList.addEventListener("click", (event) => {
    if (!isCustomizing) {
      return;
    }
    const button = event.target.closest("[data-action='add-cube']");
    if (!button) {
      return;
    }
    const item = button.closest("li");
    if (!item) {
      return;
    }
    showCube(item.dataset.cubeKey);
  });

  grid.addEventListener("dragstart", (event) => {
    if (!isCustomizing) {
      return;
    }
    const card = event.target.closest(".home-summary-card");
    if (!card || card.classList.contains("is-hidden")) {
      return;
    }
    draggedCard = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.cubeKey || "");
  });

  grid.addEventListener("dragend", () => {
    if (draggedCard) {
      draggedCard.classList.remove("is-dragging");
    }
    draggedCard = null;
  });

  grid.addEventListener("dragover", (event) => {
    if (!isCustomizing || !draggedCard) {
      return;
    }
    event.preventDefault();
    const target = event.target.closest(".home-summary-card");
    if (!target || target === draggedCard || target.classList.contains("is-hidden")) {
      return;
    }
    const rect = target.getBoundingClientRect();
    const shouldInsertAfter = event.clientY > rect.top + rect.height / 2;
    if (shouldInsertAfter) {
      grid.insertBefore(draggedCard, target.nextSibling);
    } else {
      grid.insertBefore(draggedCard, target);
    }
  });

  updateAvailableEmpty();
})();
