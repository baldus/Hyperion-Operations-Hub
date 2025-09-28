export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function roundToGrid(value, grid) {
  if (!grid) {
    return value;
  }
  return Math.round(value / grid) * grid;
}

export function normalizeRect(rect) {
  return {
    x: rect.x,
    y: rect.y,
    width: Math.max(rect.width, 4),
    height: Math.max(rect.height, 4),
  };
}

export function snapRect(rect, canvas, otherFields = [], options = {}) {
  const grid = options.grid ?? 4;
  const tolerance = options.tolerance ?? 6;
  const snapped = { ...rect };
  snapped.x = roundToGrid(snapped.x, grid);
  snapped.y = roundToGrid(snapped.y, grid);
  snapped.width = Math.max(8, roundToGrid(snapped.width, grid));
  snapped.height = Math.max(8, roundToGrid(snapped.height, grid));
  const guides = { vertical: [], horizontal: [] };
  const edges = {
    left: snapped.x,
    right: snapped.x + snapped.width,
    centerX: snapped.x + snapped.width / 2,
    top: snapped.y,
    bottom: snapped.y + snapped.height,
    centerY: snapped.y + snapped.height / 2,
  };

  const canvasGuides = [
    { axis: "vertical", value: 0, target: "left" },
    { axis: "vertical", value: canvas.width / 2, target: "centerX" },
    { axis: "vertical", value: canvas.width, target: "right" },
    { axis: "horizontal", value: 0, target: "top" },
    { axis: "horizontal", value: canvas.height / 2, target: "centerY" },
    { axis: "horizontal", value: canvas.height, target: "bottom" },
  ];

  canvasGuides.forEach((guide) => {
    const delta = Math.abs(edges[guide.target] - guide.value);
    if (delta <= tolerance) {
      if (guide.axis === "vertical") {
        snapped.x += guide.value - edges[guide.target];
        guides.vertical.push(guide.value);
        edges.left = snapped.x;
        edges.right = snapped.x + snapped.width;
        edges.centerX = snapped.x + snapped.width / 2;
      } else {
        snapped.y += guide.value - edges[guide.target];
        guides.horizontal.push(guide.value);
        edges.top = snapped.y;
        edges.bottom = snapped.y + snapped.height;
        edges.centerY = snapped.y + snapped.height / 2;
      }
    }
  });

  otherFields.forEach((field) => {
    const other = {
      left: field.position.x,
      right: field.position.x + field.size.width,
      centerX: field.position.x + field.size.width / 2,
      top: field.position.y,
      bottom: field.position.y + field.size.height,
      centerY: field.position.y + field.size.height / 2,
    };
    [
      { axis: "vertical", value: other.left, target: "left" },
      { axis: "vertical", value: other.centerX, target: "centerX" },
      { axis: "vertical", value: other.right, target: "right" },
    ].forEach((alignment) => {
      const delta = Math.abs(edges[alignment.target] - alignment.value);
      if (delta <= tolerance) {
        guides.vertical.push(alignment.value);
        snapped.x += alignment.value - edges[alignment.target];
        edges.left = snapped.x;
        edges.right = snapped.x + snapped.width;
        edges.centerX = snapped.x + snapped.width / 2;
      }
    });
    [
      { axis: "horizontal", value: other.top, target: "top" },
      { axis: "horizontal", value: other.centerY, target: "centerY" },
      { axis: "horizontal", value: other.bottom, target: "bottom" },
    ].forEach((alignment) => {
      const delta = Math.abs(edges[alignment.target] - alignment.value);
      if (delta <= tolerance) {
        guides.horizontal.push(alignment.value);
        snapped.y += alignment.value - edges[alignment.target];
        edges.top = snapped.y;
        edges.bottom = snapped.y + snapped.height;
        edges.centerY = snapped.y + snapped.height / 2;
      }
    });
  });

  snapped.x = clamp(snapped.x, 0, canvas.width - snapped.width);
  snapped.y = clamp(snapped.y, 0, canvas.height - snapped.height);
  snapped.width = clamp(snapped.width, 8, canvas.width);
  snapped.height = clamp(snapped.height, 8, canvas.height);

  return { rect: snapped, guides };
}

export function toPixels(value) {
  return `${Math.round(value)}px`;
}

export function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function buildFieldInstance(definition, overrides = {}) {
  const now = Date.now();
  return {
    id: `${definition.key || definition.id || "field"}-${now}`,
    name: definition.label || definition.name || "Field",
    type: definition.type || "text",
    bindingKey: definition.key || definition.bindingKey || null,
    text: definition.sample || definition.text || definition.label || "Sample",
    position: {
      x: definition.position?.x ?? 24,
      y: definition.position?.y ?? 24,
    },
    size: {
      width: definition.size?.width ?? 160,
      height: definition.size?.height ?? 48,
    },
    rotation: definition.rotation ?? 0,
    style: {
      fontSize: definition.style?.fontSize ?? 18,
      fontWeight: definition.style?.fontWeight ?? 500,
      letterSpacing: definition.style?.letterSpacing ?? 0,
      textAlign: definition.style?.textAlign ?? "center",
      color: definition.style?.color ?? "#0f172a",
      backgroundColor: definition.style?.backgroundColor ?? "rgba(255,255,255,0.9)",
      borderColor: definition.style?.borderColor ?? "#94a3b8",
      borderWidth: definition.style?.borderWidth ?? 1,
      borderStyle: definition.style?.borderStyle ?? "solid",
    },
    ...overrides,
  };
}

export function fieldToRect(field) {
  return {
    x: field.position.x,
    y: field.position.y,
    width: field.size.width,
    height: field.size.height,
  };
}
