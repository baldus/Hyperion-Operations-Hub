import React, { createElement } from "../lib/mini-react.js";

const h = createElement;

export function FieldToolbox({ sources, onAddField, onAddStatic }) {
  return h(
    "div",
    { class: "p-4 space-y-4" },
    h("div", { class: "space-y-2" },
      h("h3", { class: "section-title" }, "Field toolbox"),
      h(
        "p",
        { class: "text-xs text-slate-500" },
        "Drag new fields into the canvas. Bindings can be fine-tuned in the inspector."
      ),
      h(
        "button",
        {
          class: "btn btn-ghost w-full justify-center",
          onClick: () => onAddStatic && onAddStatic(),
        },
        "Add static text"
      )
    ),
    ...(sources || []).map((source) =>
      h(
        "div",
        { key: source.id, class: "space-y-2" },
        h("div", { class: "space-y-1" },
          h("p", { class: "text-xs uppercase tracking-wide text-slate-400" }, source.name),
          source.description
            ? h("p", { class: "text-xs text-slate-500" }, source.description)
            : null
        ),
        h(
          "div",
          { class: "space-y-2" },
          (source.fields || []).map((field) =>
            h(
              "button",
              {
                key: field.key,
                class: "w-full text-left p-3 rounded-md border border-slate-800 bg-slate-900 hover:bg-slate-800 transition-colors", 
                onClick: () => onAddField && onAddField(source, field),
              },
              h(
                "div",
                { class: "space-y-1" },
                h(
                  "div",
                  { class: "flex items-center justify-between" },
                  h("span", { class: "text-sm font-medium text-slate-100" }, field.label || field.key),
                  field.sample
                    ? h("span", { class: "badge bg-white/10 text-slate-300" }, "sample")
                    : null
                ),
                field.sample
                  ? h(
                      "p",
                      { class: "text-xs text-slate-500" },
                      `Ex: ${field.sample}`
                    )
                  : null
              )
            )
          )
        )
      )
    )
  );
}

export default FieldToolbox;
