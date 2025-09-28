import React, {
  createElement,
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "../lib/mini-react.js";
import { buildFieldInstance, deepClone } from "../utils/geometry.js";
import LabelList from "./LabelList.js";
import LayoutToolbar from "./LayoutToolbar.js";
import LabelPreview from "./LabelPreview.js";
import FieldToolbox from "./FieldToolbox.js";
import PropertyInspector from "./PropertyInspector.js";

const h = createElement;

export function LabelDesignerApp({ labels = [], trialPrintUrl, printers = [], selectedPrinter }) {
  const [selectedLabelId, setSelectedLabelId] = useState(() => labels[0]?.id ?? null);
  const [layout, setLayout] = useState(() =>
    labels[0] ? hydrateLayout(labels[0]) : null
  );
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [guides, setGuides] = useState({ vertical: [], horizontal: [] });
  const [status, setStatus] = useState(null);
  const [isPrinting, setIsPrinting] = useState(false);
  const [unsavedChanges, setUnsavedChanges] = useState(false);

  const selectedLabel = useMemo(
    () => labels.find((label) => label.id === selectedLabelId) || null,
    [labels, selectedLabelId]
  );

  const bindingOptions = useMemo(() => {
    const result = [];
    if (!selectedLabel) {
      return result;
    }
    (selectedLabel.dataSources || []).forEach((source) => {
      (source.fields || []).forEach((field) => {
        result.push({
          key: field.key,
          label: field.label || field.key,
          sample: field.sample,
          source: source.id,
        });
      });
    });
    return result;
  }, [selectedLabel]);

  const sampleLookup = useMemo(() => {
    const map = new Map();
    bindingOptions.forEach((binding) => {
      map.set(binding.key, binding.sample || binding.label || binding.key);
    });
    return map;
  }, [bindingOptions]);

  useEffect(() => {
    if (!selectedLabel) {
      setLayout(null);
      setSelectedFieldId(null);
      return;
    }
    setLayout(hydrateLayout(selectedLabel));
    setSelectedFieldId(null);
    setGuides({ vertical: [], horizontal: [] });
    setUnsavedChanges(false);
    setStatus(null);
  }, [selectedLabel]);

  const handleSelectLabel = useCallback((labelId) => {
    setSelectedLabelId(labelId);
  }, []);

  const handleUpdateField = useCallback((fieldId, updates) => {
    setLayout((current) => {
      if (!current) {
        return current;
      }
      const nextFields = current.fields.map((field) => {
        if (field.id !== fieldId) {
          return field;
        }
        return mergeField(field, updates);
      });
      return { ...current, fields: nextFields };
    });
    setUnsavedChanges(true);
  }, []);

  const handleSelectField = useCallback((field) => {
    setSelectedFieldId(field ? field.id : null);
  }, []);

  const handleAddField = useCallback(
    (source, fieldDefinition) => {
      if (!layout) {
        return;
      }
      const instance = buildFieldInstance(fieldDefinition, {
        name: fieldDefinition.label || fieldDefinition.key,
        bindingKey: fieldDefinition.key,
        text: fieldDefinition.sample || fieldDefinition.label || fieldDefinition.key,
      });
      setLayout((current) => ({
        ...current,
        fields: [...current.fields, instance],
      }));
      setSelectedFieldId(instance.id);
      setUnsavedChanges(true);
    },
    [layout]
  );

  const handleAddStatic = useCallback(() => {
    if (!layout) {
      return;
    }
    const instance = buildFieldInstance({
      id: `static-${Date.now()}`,
      label: "Static text",
      text: "Sample text",
      key: null,
    });
    setLayout((current) => ({
      ...current,
      fields: [...current.fields, instance],
    }));
    setSelectedFieldId(instance.id);
    setUnsavedChanges(true);
  }, [layout]);

  const handleDeleteField = useCallback((field) => {
    setLayout((current) => {
      if (!current) {
        return current;
      }
      const nextFields = current.fields.filter((candidate) => candidate.id !== field.id);
      return { ...current, fields: nextFields };
    });
    setSelectedFieldId(null);
    setUnsavedChanges(true);
  }, []);

  const handleDuplicateField = useCallback((field) => {
    const copy = buildFieldInstance(field, {
      id: `${field.id}-copy-${Date.now()}`,
      position: {
        x: field.position.x + 12,
        y: field.position.y + 12,
      },
    });
    copy.style = deepClone(field.style);
    setLayout((current) => ({
      ...current,
      fields: [...current.fields, copy],
    }));
    setSelectedFieldId(copy.id);
    setUnsavedChanges(true);
  }, []);

  const handleInspectorChange = useCallback(
    (updates) => {
      if (!selectedFieldId) {
        return;
      }
      handleUpdateField(selectedFieldId, updates);
    },
    [handleUpdateField, selectedFieldId]
  );

  const handleGuidesChange = useCallback((nextGuides) => {
    setGuides(nextGuides || { vertical: [], horizontal: [] });
  }, []);

  const handleResetLayout = useCallback(() => {
    if (!selectedLabel) {
      return;
    }
    setLayout(hydrateLayout(selectedLabel));
    setSelectedFieldId(null);
    setUnsavedChanges(false);
  }, [selectedLabel]);

  const handleTrialPrint = useCallback(async () => {
    if (!trialPrintUrl) {
      setStatus({ tone: "error", message: "Trial print endpoint is not configured." });
      return;
    }
    if (!layout) {
      return;
    }
    setIsPrinting(true);
    setStatus(null);
    try {
      const response = await fetch(trialPrintUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ layout }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.message || `Trial print failed with status ${response.status}.`);
      }
      setStatus({ tone: "success", message: payload.message || "Trial print queued." });
    } catch (error) {
      setStatus({ tone: "error", message: error.message || "Trial print failed." });
    } finally {
      setIsPrinting(false);
    }
  }, [layout, trialPrintUrl]);

  const selectedField = useMemo(() => {
    return layout?.fields.find((field) => field.id === selectedFieldId) || null;
  }, [layout, selectedFieldId]);

  const resolveSample = useCallback(
    (field) => {
      if (field.bindingKey && sampleLookup.has(field.bindingKey)) {
        return sampleLookup.get(field.bindingKey);
      }
      return field.text || field.name;
    },
    [sampleLookup]
  );

  const printerSummary = useMemo(() => {
    if (selectedPrinter) {
      return selectedPrinter;
    }
    const [primary] = printers;
    return primary || null;
  }, [printers, selectedPrinter]);

  return h(
    Fragment,
    null,
    h(
      "div",
      { class: "min-h-screen bg-slate-950 text-slate-100 flex" },
      h("aside", { class: "w-72 border-r border-slate-800 bg-slate-900 h-screen overflow-y-auto" },
        h(LabelList, {
          labels,
          selectedLabelId,
          onSelect: handleSelectLabel,
        })
      ),
      h(
        "main",
        { class: "flex-1 flex flex-col h-screen" },
        h(LayoutToolbar, {
          selectedLabel,
          selectedPrinter: printerSummary,
          hasUnsavedChanges: unsavedChanges,
          onResetLayout: handleResetLayout,
          onTrialPrint: handleTrialPrint,
          isPrinting,
        }),
        status
          ? h(
              "div",
              {
                class: `px-5 py-3 ${
                  status.tone === "success"
                    ? "bg-emerald-500/10 text-emerald-400"
                    : "bg-red-500/10 text-red-400"
                }`,
              },
              status.message
            )
          : null,
        h(
          "div",
          { class: "flex flex-1 overflow-hidden" },
          h(LabelPreview, {
            label: selectedLabel,
            layout,
            selectedFieldId,
            onSelectField: handleSelectField,
            onUpdateField: handleUpdateField,
            onGuidesChange: handleGuidesChange,
            guides,
            resolveSample,
          })
        )
      ),
      h("aside", { class: "w-80 border-l border-slate-800 bg-slate-900 h-screen overflow-y-auto" },
        h(FieldToolbox, {
          sources: selectedLabel?.dataSources || [],
          onAddField: handleAddField,
          onAddStatic: handleAddStatic,
        }),
        h("div", { class: "border-t border-slate-800" }),
        h(PropertyInspector, {
          field: selectedField,
          availableBindings: bindingOptions,
          onChange: handleInspectorChange,
          onDuplicate: handleDuplicateField,
          onDelete: handleDeleteField,
        })
      )
    )
  );
}

function hydrateLayout(label) {
  const canvas = {
    width: label.canvas?.width ?? 400,
    height: label.canvas?.height ?? 240,
    unit: label.canvas?.unit ?? "px",
    backgroundColor: label.canvas?.backgroundColor ?? "#ffffff",
  };
  const fields = (label.fields || []).map((field) => buildFieldInstance(field, { id: field.id || `${field.key || "field"}-${Date.now()}-${Math.random()}` }));
  return {
    labelId: label.id,
    canvas,
    fields,
  };
}

function mergeField(field, updates) {
  const next = { ...field };
  Object.keys(updates || {}).forEach((key) => {
    const value = updates[key];
    if (key === "position" || key === "size" || key === "style") {
      next[key] = { ...field[key], ...value };
    } else {
      next[key] = value;
    }
  });
  return next;
}

export default LabelDesignerApp;
