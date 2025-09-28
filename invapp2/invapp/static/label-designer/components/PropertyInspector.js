import React, { createElement } from "../lib/mini-react.js";

const h = createElement;

function numberInput(value) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "";
  }
  return String(value);
}

export function PropertyInspector({
  field,
  availableBindings,
  onChange,
  onDuplicate,
  onDelete,
}) {
  if (!field) {
    return h(
      "div",
      { class: "p-5 space-y-3" },
      h("h3", { class: "section-title" }, "Field inspector"),
      h(
        "p",
        { class: "text-sm text-slate-500" },
        "Select a field to adjust its data binding, layout, and typographic styles."
      )
    );
  }

  const handleNumericChange = (section, key, event) => {
    const raw = parseFloat(event.target.value);
    if (Number.isNaN(raw)) {
      return;
    }
    if (section === "position") {
      onChange && onChange({ position: { ...field.position, [key]: raw } });
    } else if (section === "size") {
      onChange && onChange({ size: { ...field.size, [key]: raw } });
    } else if (section === "style") {
      onChange && onChange({ style: { ...field.style, [key]: raw } });
    } else if (section === "root") {
      onChange && onChange({ [key]: raw });
    }
  };

  const handleStyleChange = (key, value) => {
    onChange && onChange({ style: { ...field.style, [key]: value } });
  };

  return h(
    "div",
    { class: "p-5 space-y-5" },
    h("div", { class: "space-y-2" },
      h("h3", { class: "section-title" }, "Field inspector"),
      h("p", { class: "text-xs text-slate-500" }, `${field.name} • ${field.type}`)
    ),
    h(
      "div",
      { class: "space-y-3" },
      h("label", null, "Display name"),
      h("input", {
        type: "text",
        value: field.name,
        onInput: (event) => onChange && onChange({ name: event.target.value }),
      })
    ),
    h(
      "div",
      { class: "space-y-3" },
      h("label", null, "Binding"),
      h(
        "select",
        {
          value: field.bindingKey || "",
          onChange: (event) =>
            onChange &&
            onChange({ bindingKey: event.target.value || null }),
        },
        h("option", { value: "" }, "Unbound"),
        ...(availableBindings || []).map((binding) =>
          h(
            "option",
            { key: binding.key, value: binding.key },
            `${binding.label} (${binding.key})`
          )
        )
      ),
      h("label", null, "Sample text"),
      h("textarea", {
        rows: 3,
        value: field.text || "",
        onInput: (event) => onChange && onChange({ text: event.target.value }),
      })
    ),
    h(
      "div",
      { class: "grid grid-cols-2 gap-3" },
      h(
        "div",
        { class: "space-y-3" },
        h("label", null, "X"),
        h("input", {
          type: "number",
          value: numberInput(field.position.x),
          onInput: (event) => handleNumericChange("position", "x", event),
        })
      ),
      h(
        "div",
        { class: "space-y-3" },
        h("label", null, "Y"),
        h("input", {
          type: "number",
          value: numberInput(field.position.y),
          onInput: (event) => handleNumericChange("position", "y", event),
        })
      ),
      h(
        "div",
        { class: "space-y-3" },
        h("label", null, "Width"),
        h("input", {
          type: "number",
          value: numberInput(field.size.width),
          onInput: (event) => handleNumericChange("size", "width", event),
        })
      ),
      h(
        "div",
        { class: "space-y-3" },
        h("label", null, "Height"),
        h("input", {
          type: "number",
          value: numberInput(field.size.height),
          onInput: (event) => handleNumericChange("size", "height", event),
        })
      )
    ),
    h(
      "div",
      { class: "space-y-3" },
      h("label", null, "Rotation"),
      h("input", {
        type: "number",
        value: numberInput(field.rotation),
        onInput: (event) => handleNumericChange("root", "rotation", event),
      })
    ),
    h(
      "div",
      { class: "space-y-3" },
      h("label", null, "Font size"),
      h("input", {
        type: "number",
        value: numberInput(field.style.fontSize),
        onInput: (event) => handleNumericChange("style", "fontSize", event),
      }),
      h("label", null, "Font weight"),
      h(
        "select",
        {
          value: field.style.fontWeight,
          onChange: (event) =>
            handleStyleChange("fontWeight", parseInt(event.target.value, 10)),
        },
        [300, 400, 500, 600, 700].map((weight) =>
          h("option", { key: weight, value: weight }, weight)
        )
      ),
      h("label", null, "Alignment"),
      h(
        "select",
        {
          value: field.style.textAlign,
          onChange: (event) => handleStyleChange("textAlign", event.target.value),
        },
        h("option", { value: "left" }, "Left"),
        h("option", { value: "center" }, "Center"),
        h("option", { value: "right" }, "Right")
      ),
      h("label", null, "Text color"),
      h("input", {
        type: "color",
        value: field.style.color,
        onInput: (event) => handleStyleChange("color", event.target.value),
      }),
      h("label", null, "Background"),
      h("input", {
        type: "color",
        value: field.style.backgroundColor,
        onInput: (event) => handleStyleChange("backgroundColor", event.target.value),
      }),
      h("label", null, "Border color"),
      h("input", {
        type: "color",
        value: field.style.borderColor,
        onInput: (event) => handleStyleChange("borderColor", event.target.value),
      }),
      h("label", null, "Border width"),
      h("input", {
        type: "number",
        value: numberInput(field.style.borderWidth),
        onInput: (event) => handleNumericChange("style", "borderWidth", event),
      })
    ),
    h(
      "div",
      { class: "flex items-center gap-3" },
      h(
        "button",
        { class: "btn btn-ghost", onClick: () => onDuplicate && onDuplicate(field) },
        "Duplicate"
      ),
      h(
        "button",
        {
          class: "btn bg-red-500",
          onClick: () => onDelete && onDelete(field),
        },
        "Delete"
      )
    )
  );
}

export default PropertyInspector;
