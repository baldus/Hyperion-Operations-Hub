import React, { createElement } from "../lib/mini-react.js";

const h = createElement;

export function LayoutToolbar({
  selectedLabel,
  selectedPrinter,
  hasUnsavedChanges,
  onResetLayout,
  onTrialPrint,
  isPrinting,
}) {
  return h(
    "div",
    { class: "px-5 py-4 border-b border-slate-800 bg-slate-900" },
    h(
      "div",
      { class: "flex items-center justify-between gap-4" },
      h(
        "div",
        { class: "space-y-1" },
        h(
          "h1",
          { class: "text-lg font-semibold text-slate-100" },
          selectedLabel ? selectedLabel.name : "Label Designer"
        ),
        selectedPrinter
          ? h(
              "p",
              { class: "text-xs text-slate-500" },
              `Trial prints target ${selectedPrinter.name} (${selectedPrinter.connection_label}).`
            )
          : h(
              "p",
              { class: "text-xs text-amber-400" },
              "No printer selected. Configure one in printer settings before trial printing."
            )
      ),
      h(
        "div",
        { class: "flex items-center gap-3" },
        hasUnsavedChanges
          ? h(
              "span",
              { class: "status-pill bg-amber-500/10 text-amber-400" },
              "Unsaved"
            )
          : h(
              "span",
              { class: "status-pill bg-emerald-500/10 text-emerald-400" },
              "Up to date"
            ),
        h(
          "button",
          {
            class: "btn btn-ghost",
            onClick: () => onResetLayout && onResetLayout(),
            disabled: !selectedLabel,
          },
          "Reset layout"
        ),
        h(
          "button",
          {
            class: "btn btn-primary",
            onClick: () => onTrialPrint && onTrialPrint(),
            disabled: !selectedLabel || !selectedPrinter || isPrinting,
          },
          isPrinting ? "Sending..." : "Print trial"
        )
      )
    )
  );
}

export default LayoutToolbar;
