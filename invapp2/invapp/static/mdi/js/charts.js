(function () {
  document.addEventListener("DOMContentLoaded", function () {
    if (typeof Chart === "undefined") {
      console.warn("Chart.js was not loaded; skipping chart rendering.");
    } else {
      const canvases = document.querySelectorAll("canvas[data-chart-config]");
      canvases.forEach((canvas) => {
        const configText = canvas.dataset.chartConfig;
        if (!configText) {
          return;
        }

        try {
          const config = JSON.parse(configText);
          const context = canvas.getContext("2d");
          // eslint-disable-next-line no-new
          new Chart(context, config);
        } catch (error) {
          console.error("Failed to render chart", error);
        }
      });
    }

    const toggleButtons = document.querySelectorAll("[data-collapse-toggle]");
    toggleButtons.forEach((button) => {
      const targetId = button.getAttribute("data-collapse-toggle");
      if (!targetId) {
        return;
      }

      const target = document.getElementById(targetId);
      if (!target) {
        return;
      }

      const textElement = button.querySelector("[data-collapse-toggle-text]");
      const iconElement = button.querySelector("[data-collapse-toggle-icon]");

      const hideLabel = textElement?.dataset.collapseHide || "Hide";
      const showLabel = textElement?.dataset.collapseShow || "Show";
      const expandedIcon = iconElement?.dataset.iconExpanded || "bi-chevron-up";
      const collapsedIcon = iconElement?.dataset.iconCollapsed || "bi-chevron-down";

      const updateState = () => {
        const expanded = target.classList.contains("show");
        if (textElement) {
          textElement.textContent = expanded ? hideLabel : showLabel;
        }
        if (iconElement) {
          iconElement.classList.remove(expanded ? collapsedIcon : expandedIcon);
          iconElement.classList.add(expanded ? expandedIcon : collapsedIcon);
        }
        button.setAttribute("aria-expanded", expanded ? "true" : "false");
      };

      updateState();

      target.addEventListener("shown.bs.collapse", updateState);
      target.addEventListener("hidden.bs.collapse", updateState);
    });

    const bulkForms = document.querySelectorAll("[data-metric-bulk-form]");
    bulkForms.forEach((form) => {
      const formId = form.getAttribute("id");
      if (!formId) {
        return;
      }

      const deleteButton = form.querySelector("[data-metric-bulk-delete]");
      const checkboxes = Array.from(
        document.querySelectorAll(`[data-metric-select][form="${formId}"]`)
      );
      const selectAll = document.querySelector(
        `[data-metric-select-all][form="${formId}"]`
      );

      const updateState = () => {
        const anyChecked = checkboxes.some((checkbox) => checkbox.checked);
        const allChecked =
          anyChecked && checkboxes.every((checkbox) => checkbox.checked);

        if (deleteButton) {
          deleteButton.disabled = !anyChecked;
        }

        if (selectAll) {
          selectAll.checked = allChecked;
          selectAll.indeterminate = anyChecked && !allChecked;
        }
      };

      checkboxes.forEach((checkbox) => {
        checkbox.addEventListener("change", updateState);
      });

      if (selectAll) {
        selectAll.addEventListener("change", () => {
          checkboxes.forEach((checkbox) => {
            checkbox.checked = selectAll.checked;
          });
          updateState();
        });
      }

      updateState();
    });
  });
})();
