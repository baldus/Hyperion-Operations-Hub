import React, { createElement, useMemo } from "../lib/mini-react.js";

const h = createElement;

export function LabelList({ labels, selectedLabelId, onSelect }) {
  const orderedLabels = useMemo(() => {
    return [...(labels || [])].sort((a, b) => a.name.localeCompare(b.name));
  }, [labels]);

  if (!orderedLabels.length) {
    return h(
      "div",
      { class: "p-4 space-y-3" },
      h("h2", { class: "text-sm uppercase tracking-wide text-slate-400" }, "Labels"),
      h(
        "p",
        { class: "text-sm text-slate-500 leading-relaxed" },
        "No labels are registered yet. Define label metadata in the backend to make them available here."
      )
    );
  }

  return h(
    "div",
    { class: "p-4 space-y-4" },
    h("div", { class: "space-y-2" },
      h("h2", { class: "text-sm uppercase tracking-wide text-slate-400" }, "Labels"),
      h(
        "p",
        { class: "text-xs text-slate-500" },
        "Pick a label to load its layout, available data bindings, and toolbox."
      )
    ),
    h(
      "div",
      { class: "space-y-2" },
      orderedLabels.map((label) => {
        const isActive = label.id === selectedLabelId;
        const className = [
          "w-full",
          "p-4",
          "rounded-lg",
          "transition-colors",
          "duration-150",
          "ease-out",
          "text-left",
          "border",
          isActive ? "border-highlight bg-white/10" : "border-slate-800 bg-slate-900",
        ].join(" ");
        return h(
          "button",
          {
            key: label.id,
            class: className,
            onClick: () => onSelect && onSelect(label.id),
          },
          h(
            "div",
            { class: "flex items-center justify-between" },
            h(
              "div",
              { class: "space-y-1" },
              h("p", { class: "text-sm font-semibold text-slate-100" }, label.name),
              label.description
                ? h("p", { class: "text-xs text-slate-500" }, label.description)
                : null
            ),
            h(
              "span",
              {
                class: "badge bg-white/10 text-slate-300",
              },
              `${label.fields?.length ?? 0} fields`
            )
          )
        );
      })
    )
  );
}

export default LabelList;
