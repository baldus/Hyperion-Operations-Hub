import React, { createElement, useEffect, useMemo, useRef, useState } from "../lib/mini-react.js";
import { snapRect, fieldToRect, toPixels } from "../utils/geometry.js";

const h = createElement;

const HANDLE_OFFSETS = ["nw", "ne", "sw", "se", "n", "s", "e", "w"];

function classNames(...values) {
  return values.filter(Boolean).join(" ");
}

export function LabelPreview({
  label,
  layout,
  selectedFieldId,
  onSelectField,
  onUpdateField,
  onGuidesChange,
  guides,
  resolveSample,
}) {
  const canvasRef = useRef(null);
  const viewportRef = useRef(null);
  const [scale, setScale] = useState(1);
  const scaleRef = useRef(1);
  const dragState = useRef(null);

  const canvas = layout?.canvas || label?.canvas || { width: 400, height: 240, unit: "px", backgroundColor: "#fff" };
  const fields = layout?.fields || [];

  useEffect(() => {
    scaleRef.current = scale;
  }, [scale]);

  useEffect(() => {
    const viewportEl = viewportRef.current;
    if (!viewportEl) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        if (!canvas.width) {
          setScale(1);
          continue;
        }
        const nextScale = Math.min(width / canvas.width, 2);
        setScale(nextScale > 0 ? nextScale : 1);
      }
    });
    observer.observe(viewportEl);
    return () => observer.disconnect();
  }, [canvas.width]);

  const otherFields = useMemo(() => {
    return (fields || []).map((field) => ({
      id: field.id,
      position: { ...field.position },
      size: { ...field.size },
    }));
  }, [fields]);

  useEffect(() => {
    onGuidesChange && onGuidesChange({ vertical: [], horizontal: [] });
  }, [selectedFieldId]);

  const handlePointerMove = (event) => {
    const state = dragState.current;
    if (!state || !canvasRef.current) {
      return;
    }
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleFactor = scaleRef.current || 1;
    const pointerX = (event.clientX - rect.left) / scaleFactor;
    const pointerY = (event.clientY - rect.top) / scaleFactor;
    if (state.mode === "move") {
      const offsetX = pointerX - state.pointerStart.x;
      const offsetY = pointerY - state.pointerStart.y;
      const nextRect = {
        x: state.startRect.x + offsetX,
        y: state.startRect.y + offsetY,
        width: state.startRect.width,
        height: state.startRect.height,
      };
      const siblings = otherFields.filter((f) => f.id !== state.fieldId);
      const { rect: snappedRect, guides: snapGuides } = snapRect(nextRect, canvas, siblings);
      onUpdateField &&
        onUpdateField(state.fieldId, {
          position: { x: snappedRect.x, y: snappedRect.y },
        });
      onGuidesChange && onGuidesChange(snapGuides);
    } else if (state.mode === "resize") {
      const dx = pointerX - state.pointerStart.x;
      const dy = pointerY - state.pointerStart.y;
      const nextRect = computeResizeRect(state, dx, dy);
      const siblings = otherFields.filter((f) => f.id !== state.fieldId);
      const { rect: snappedRect, guides: snapGuides } = snapRect(nextRect, canvas, siblings);
      onUpdateField &&
        onUpdateField(state.fieldId, {
          position: { x: snappedRect.x, y: snappedRect.y },
          size: { width: snappedRect.width, height: snappedRect.height },
        });
      onGuidesChange && onGuidesChange(snapGuides);
    } else if (state.mode === "rotate") {
      const angle = Math.atan2(pointerY - state.center.y, pointerX - state.center.x);
      const delta = angle - state.startAngle;
      const degrees = (state.startRotation + delta * (180 / Math.PI)) % 360;
      const snapped = Math.round(degrees / 5) * 5;
      onUpdateField &&
        onUpdateField(state.fieldId, {
          rotation: snapped,
        });
    }
  };

  const endInteraction = () => {
    const state = dragState.current;
    if (state) {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", endInteraction);
      dragState.current = null;
      onGuidesChange && onGuidesChange({ vertical: [], horizontal: [] });
    }
  };

  const beginMove = (field, event) => {
    if (!canvasRef.current) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleFactor = scaleRef.current || 1;
    const pointerX = (event.clientX - rect.left) / scaleFactor;
    const pointerY = (event.clientY - rect.top) / scaleFactor;
    dragState.current = {
      mode: "move",
      fieldId: field.id,
      startRect: fieldToRect(field),
      pointerStart: { x: pointerX, y: pointerY },
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", endInteraction);
  };

  const beginResize = (field, handle, event) => {
    if (!canvasRef.current) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleFactor = scaleRef.current || 1;
    const pointerX = (event.clientX - rect.left) / scaleFactor;
    const pointerY = (event.clientY - rect.top) / scaleFactor;
    dragState.current = {
      mode: "resize",
      fieldId: field.id,
      handle,
      pointerStart: { x: pointerX, y: pointerY },
      startRect: fieldToRect(field),
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", endInteraction);
  };

  const beginRotate = (field, event) => {
    if (!canvasRef.current) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleFactor = scaleRef.current || 1;
    const pointerX = (event.clientX - rect.left) / scaleFactor;
    const pointerY = (event.clientY - rect.top) / scaleFactor;
    const fieldRect = fieldToRect(field);
    const center = {
      x: fieldRect.x + fieldRect.width / 2,
      y: fieldRect.y + fieldRect.height / 2,
    };
    dragState.current = {
      mode: "rotate",
      fieldId: field.id,
      center,
      pointerStart: { x: pointerX, y: pointerY },
      startAngle: Math.atan2(pointerY - center.y, pointerX - center.x),
      startRotation: field.rotation || 0,
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", endInteraction);
  };

  const handleFieldClick = (field, event) => {
    event.stopPropagation();
    onSelectField && onSelectField(field);
  };

  return h(
    "div",
    { class: "flex-1 overflow-hidden" },
    h(
      "div",
      { class: "h-full flex flex-col" },
      h(
        "div",
        { class: "px-5 py-4 border-b border-slate-800 bg-slate-900 flex items-center justify-between" },
        h(
          "div",
          { class: "space-y-1" },
          h("h3", { class: "text-sm uppercase tracking-wide text-slate-400" }, "Canvas preview"),
          h(
            "p",
            { class: "text-xs text-slate-500" },
            `Working area ${canvas.width}×${canvas.height} ${canvas.unit || "px"}`
          )
        ),
        h(
          "div",
          { class: "text-xs text-slate-500" },
          `Scale ${scale.toFixed(2)}×`
        )
      ),
      h(
        "div",
        { ref: viewportRef, class: "flex-1 overflow-auto" },
        h(
          "div",
          { class: "w-full h-full flex items-center justify-center py-6" },
          h(
            "div",
            {
              class: "relative",
              style: {
                width: toPixels(canvas.width * scale),
                height: toPixels(canvas.height * scale),
              },
            },
            h(
              "div",
              {
                ref: canvasRef,
                class: "label-designer-canvas shadow-inner rounded-lg",
                onClick: () => onSelectField && onSelectField(null),
                style: {
                  width: toPixels(canvas.width),
                  height: toPixels(canvas.height),
                  backgroundColor: canvas.backgroundColor || "#ffffff",
                  transform: `scale(${scale})`,
                  transformOrigin: "top left",
                },
              },
              fields.map((field) =>
                h(
                  "div",
                  {
                    key: field.id,
                    class: classNames(
                      "label-designer-field",
                      field.id === selectedFieldId ? "selected" : null
                    ),
                    style: buildFieldStyle(field),
                    onPointerDown: (event) => beginMove(field, event),
                    onClick: (event) => handleFieldClick(field, event),
                  },
                  h(
                    "div",
                    { class: "w-full select-none" },
                    resolveSample ? resolveSample(field) : field.text || field.name
                  ),
                  field.id === selectedFieldId
                    ? HANDLE_OFFSETS.map((handle) =>
                        h("span", {
                          key: handle,
                          class: `handle ${handle}`,
                          onPointerDown: (event) => beginResize(field, handle, event),
                        })
                      )
                    : null,
                  field.id === selectedFieldId
                    ? h("span", {
                        class: "handle rotate",
                        onPointerDown: (event) => beginRotate(field, event),
                      })
                    : null
                )
              ),
              (guides?.vertical || []).map((x, index) =>
                h("span", {
                  key: `v-${index}-${x}`,
                  class: "guideline vertical",
                  style: {
                    left: toPixels(x),
                  },
                })
              ),
              (guides?.horizontal || []).map((y, index) =>
                h("span", {
                  key: `h-${index}-${y}`,
                  class: "guideline horizontal",
                  style: {
                    top: toPixels(y),
                  },
                })
              )
            )
          )
        )
      )
    )
  );
}

function computeResizeRect(state, dx, dy) {
  const rect = { ...state.startRect };
  if (state.handle.includes("n")) {
    rect.y += dy;
    rect.height -= dy;
  }
  if (state.handle.includes("s")) {
    rect.height += dy;
  }
  if (state.handle.includes("w")) {
    rect.x += dx;
    rect.width -= dx;
  }
  if (state.handle.includes("e")) {
    rect.width += dx;
  }
  if (rect.width < 8) {
    rect.width = 8;
  }
  if (rect.height < 8) {
    rect.height = 8;
  }
  return rect;
}

function buildFieldStyle(field) {
  return {
    left: toPixels(field.position.x),
    top: toPixels(field.position.y),
    width: toPixels(field.size.width),
    height: toPixels(field.size.height),
    transform: `rotate(${field.rotation || 0}deg)`,
    fontSize: `${field.style.fontSize}px`,
    fontWeight: field.style.fontWeight,
    letterSpacing: field.style.letterSpacing ? `${field.style.letterSpacing}px` : undefined,
    textAlign: field.style.textAlign,
    color: field.style.color,
    backgroundColor: field.style.backgroundColor,
    borderColor: field.style.borderColor,
    borderWidth: `${field.style.borderWidth}px`,
    borderStyle: field.style.borderStyle || "solid",
  };
}

export default LabelPreview;
