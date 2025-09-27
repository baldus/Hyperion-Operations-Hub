(() => {
  const { useState, useMemo, useRef, useEffect, useCallback } = React;
  const DESIGNER_CONFIG = window.labelDesignerConfig || {};

  const uniqueId = () => `element-${Math.random().toString(36).slice(2)}-${Date.now()}`;

  const GRID_SIZE = 8;

  const LABEL_SIZES = [
    { id: '2x1', name: '2" x 1" (Shipping)', width: 600, height: 300 },
    { id: '4x6', name: '4" x 6" (Parcel)', width: 900, height: 1350 },
    { id: '3x2', name: '3" x 2" (Shelf)', width: 720, height: 480 },
    { id: 'custom', name: 'Custom', width: 700, height: 400 }
  ];

  const CUSTOM_TEMPLATE = {
    name: '__custom__',
    display_name: 'Custom Layout',
    description: 'Start from a blank canvas with all available inventory fields.',
    layout: { width: LABEL_SIZES[0].width, height: LABEL_SIZES[0].height, elements: [] },
    fields: {},
    field_keys: [],
    triggers: [],
    source: 'custom'
  };

  const DATA_FIELD_GROUPS = [
    {
      id: 'item',
      label: 'Item Details',
      description: 'Core item master data for inventory pieces.',
      fields: [
        {
          id: 'item-name',
          label: 'Item Name',
          fieldKey: 'inventory.item.name',
          preview: 'Aluminum Gate Panel',
          description: 'Primary description of the inventory item.'
        },
        {
          id: 'item-sku',
          label: 'Item SKU',
          fieldKey: 'inventory.item.sku',
          preview: 'SKU: GATE-AL-42',
          description: 'Stock keeping unit or part number for the item.'
        },
        {
          id: 'item-description',
          label: 'Item Description',
          fieldKey: 'inventory.item.description',
          preview: '42" powder coated aluminum gate panel',
          description: 'Extended item description or notes.',
          defaultHeight: 96
        },
        {
          id: 'item-type',
          label: 'Item Type',
          fieldKey: 'inventory.item.type',
          preview: 'Type: Assembly',
          description: 'Item type or classification value.'
        },
        {
          id: 'item-unit',
          label: 'Unit of Measure',
          fieldKey: 'inventory.item.unit',
          preview: 'Unit: ea',
          description: 'Selling or stocking unit of measure.'
        },
        {
          id: 'item-class',
          label: 'Item Class',
          fieldKey: 'inventory.item.item_class',
          preview: 'Class: Finished Goods',
          description: 'Inventory class or reporting bucket.'
        }
      ]
    },
    {
      id: 'stock',
      label: 'Stock & Batch',
      description: 'Details about quantities, batches, and tracking.',
      fields: [
        {
          id: 'quantity',
          label: 'Quantity',
          fieldKey: 'inventory.stock.quantity',
          preview: 'Qty: 24',
          description: 'Quantity represented by the label.'
        },
        {
          id: 'min-stock',
          label: 'Min Stock',
          fieldKey: 'inventory.item.min_stock',
          preview: 'Min: 12',
          description: 'Minimum stocking level for the item.'
        },
        {
          id: 'lot-number',
          label: 'Lot Number',
          fieldKey: 'inventory.batch.lot_number',
          preview: 'Lot #A1-2048',
          description: 'Supplier or production lot identifier.'
        },
        {
          id: 'received-date',
          label: 'Received Date',
          fieldKey: 'inventory.batch.received_date',
          preview: 'Received: 2024-03-12',
          description: 'Date the batch was received or produced.'
        },
        {
          id: 'barcode',
          label: 'Item Barcode',
          fieldKey: 'inventory.item.barcode',
          preview: '|| ITEM BARCODE ||',
          description: 'Scannable barcode tied to the SKU or barcode value.',
          type: 'barcode',
          defaultHeight: 120
        }
      ]
    },
    {
      id: 'location',
      label: 'Location',
      description: 'Storage and fulfillment locations for the item.',
      fields: [
        {
          id: 'location-code',
          label: 'Location Code',
          fieldKey: 'inventory.location.code',
          preview: 'LOC: RACK-3B',
          description: 'Warehouse or storage location identifier.'
        },
        {
          id: 'location-description',
          label: 'Location Description',
          fieldKey: 'inventory.location.description',
          preview: 'North warehouse - Rack aisle 3, bay B',
          description: 'Human-friendly description of the storage location.',
          defaultHeight: 90
        }
      ]
    },
    {
      id: 'order',
      label: 'Work & Order Tracking',
      description: 'Downstream fulfillment, customer, and order values.',
      fields: [
        {
          id: 'order-number',
          label: 'Order Number',
          fieldKey: 'orders.order.number',
          preview: 'WO-5843',
          description: 'Work or sales order identifier for the label.'
        },
        {
          id: 'customer-name',
          label: 'Customer Name',
          fieldKey: 'orders.customer.name',
          preview: 'Customer: Horizon Builders',
          description: 'Customer receiving the labeled goods.'
        },
        {
          id: 'order-item-number',
          label: 'Order Item Number',
          fieldKey: 'orders.item.number',
          preview: 'Item: 100-445-AX',
          description: 'Primary item identifier associated with the order.'
        },
        {
          id: 'ship-date',
          label: 'Ship Date',
          fieldKey: 'orders.shipment.date',
          preview: 'Ship: 2024-03-15',
          description: 'Target shipment or due date for the order.'
        }
      ]
    }
  ];

  const ALL_FIELDS = DATA_FIELD_GROUPS.flatMap((group) =>
    group.fields.map((field) => ({ ...field, groupId: group.id, groupLabel: group.label }))
  );

  const RAW_TEMPLATES = Array.isArray(DESIGNER_CONFIG.labelTemplates)
    ? DESIGNER_CONFIG.labelTemplates.filter((entry) => entry && typeof entry === 'object')
    : [];
  const LABEL_TEMPLATES = [
    CUSTOM_TEMPLATE,
    ...RAW_TEMPLATES.filter((template) => template.name && template.name !== CUSTOM_TEMPLATE.name),
  ];
  const TEMPLATE_LOOKUP = LABEL_TEMPLATES.reduce((accumulator, template) => {
    accumulator[template.name] = template;
    return accumulator;
  }, {});
  const DEFAULT_LABEL_NAME = (() => {
    const configured = DESIGNER_CONFIG.defaultLabelName;
    if (configured && TEMPLATE_LOOKUP[configured]) {
      return configured;
    }
    const firstReal = LABEL_TEMPLATES.find((template) => template.name !== CUSTOM_TEMPLATE.name);
    return firstReal ? firstReal.name : CUSTOM_TEMPLATE.name;
  })();

  const FIELD_LOOKUP = new Map(ALL_FIELDS.map((field) => [field.fieldKey, field]));

  const ensureNumber = (value, fallback) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const deriveAllowedFieldKeys = (template) => {
    if (!template || template.name === CUSTOM_TEMPLATE.name) {
      return null;
    }
    const keys = new Set(Array.isArray(template.field_keys) ? template.field_keys : []);
    if (template.fields && typeof template.fields === 'object') {
      Object.keys(template.fields).forEach((key) => keys.add(key));
    }
    const layoutElements = (template.layout && Array.isArray(template.layout.elements))
      ? template.layout.elements
      : template.layout?.elements || [];
    layoutElements.forEach((element) => {
      if (element && typeof element === 'object' && element.fieldKey) {
        keys.add(element.fieldKey);
      }
    });
    const normalized = [...keys].filter((key) => FIELD_LOOKUP.has(key));
    return normalized.length ? new Set(normalized) : null;
  };

  const filterFieldGroups = (allowedKeys) => {
    const allowAll = !allowedKeys || !(allowedKeys instanceof Set) || allowedKeys.size === 0;
    return DATA_FIELD_GROUPS.map((group) => {
      const filteredFields = group.fields
        .filter((field) => allowAll || allowedKeys.has(field.fieldKey))
        .map((field) => ({ ...field, groupId: group.id, groupLabel: group.label }));
      if (!filteredFields.length) {
        return null;
      }
      return { ...group, fields: filteredFields };
    }).filter(Boolean);
  };

  const flattenFieldGroups = (groups) => groups.flatMap((group) => group.fields);

  const resolveLabelSizeOption = (width, height) => {
    const numericWidth = ensureNumber(width, LABEL_SIZES[0].width);
    const numericHeight = ensureNumber(height, LABEL_SIZES[0].height);
    const match = LABEL_SIZES.find(
      (size) => size.id !== 'custom' && size.width === numericWidth && size.height === numericHeight,
    );
    const customSize = { width: numericWidth, height: numericHeight };
    return {
      size: match || LABEL_SIZES.find((entry) => entry.id === 'custom') || LABEL_SIZES[LABEL_SIZES.length - 1],
      custom: customSize,
    };
  };

  const normalizeLayoutElement = (element) => {
    if (!element || typeof element !== 'object') {
      return null;
    }
    const typeRaw = typeof element.type === 'string' ? element.type.toLowerCase() : 'field';
    const type = ['text', 'image', 'barcode', 'box'].includes(typeRaw) ? typeRaw : 'field';
    const fieldKey = element.fieldKey || null;
    const fieldMeta = fieldKey ? FIELD_LOOKUP.get(fieldKey) : null;
    const baseWidth = ensureNumber(
      element.width,
      type === 'barcode' ? 280 : type === 'image' ? 220 : fieldMeta?.defaultWidth || 220,
    );
    const baseHeight = ensureNumber(
      element.height,
      element.defaultHeight || (type === 'barcode' ? 140 : type === 'image' ? 120 : fieldMeta?.defaultHeight || 64),
    );
    const normalized = {
      id: element.id || uniqueId(),
      type,
      fieldKey,
      label: element.label || element.text || fieldMeta?.label || null,
      text: element.text || fieldMeta?.preview || '',
      dataBinding: element.dataBinding || null,
      x: ensureNumber(element.x, 0),
      y: ensureNumber(element.y, 0),
      width: Math.max(baseWidth, MIN_SIZE),
      height: Math.max(baseHeight, MIN_SIZE),
      rotation: ensureNumber(element.rotation, 0),
      fontFamily: element.fontFamily || 'Inter, sans-serif',
      fontSize: ensureNumber(element.fontSize, type === 'barcode' ? 32 : 18),
      fontWeight: element.fontWeight || '600',
      textAlign: element.textAlign || (type === 'text' ? 'center' : 'left'),
      color: element.color || '#111827',
      background:
        element.background !== undefined
          ? element.background
          : type === 'barcode'
            ? '#0f172a'
            : 'rgba(255,255,255,0.85)',
      locked: Boolean(element.locked),
    };

    if (typeof element.prefix === 'string') {
      normalized.prefix = element.prefix;
    }
    if (typeof element.suffix === 'string') {
      normalized.suffix = element.suffix;
    }
    if (element.uppercase) {
      normalized.uppercase = Boolean(element.uppercase);
    }

    if (type === 'image') {
      normalized.src = element.src || '';
      normalized.background = 'transparent';
      delete normalized.fontFamily;
      delete normalized.fontSize;
      delete normalized.fontWeight;
      delete normalized.textAlign;
      delete normalized.color;
    } else if (type === 'barcode') {
      normalized.printValue = element.printValue !== undefined ? Boolean(element.printValue) : true;
      normalized.checkDigit = element.checkDigit !== undefined ? Boolean(element.checkDigit) : false;
      normalized.orientation = element.orientation || 'N';
    } else if (type === 'box') {
      normalized.thickness = ensureNumber(element.thickness, 2);
      normalized.background = element.background || 'transparent';
    }

    return normalized;
  };

  const FONT_FAMILIES = [
    { value: 'Inter, sans-serif', label: 'Inter' },
    { value: 'Roboto, sans-serif', label: 'Roboto' },
    { value: '"Fira Code", monospace', label: 'Fira Code' },
    { value: '"IBM Plex Sans", sans-serif', label: 'IBM Plex Sans' }
  ];

  const ALIGN_OPTIONS = [
    { value: 'left', label: 'Left' },
    { value: 'center', label: 'Center' },
    { value: 'right', label: 'Right' },
    { value: 'justify', label: 'Justify' }
  ];

  const FONT_WEIGHTS = [
    { value: '400', label: 'Regular' },
    { value: '500', label: 'Medium' },
    { value: '600', label: 'Semibold' },
    { value: '700', label: 'Bold' }
  ];

  const MIN_SIZE = 30;

  const snapPosition = (value) => Math.round(value / GRID_SIZE) * GRID_SIZE;
  const snapSize = (value) => Math.max(MIN_SIZE, Math.round(value / GRID_SIZE) * GRID_SIZE);

  const clamp = (value, min, max) => {
    if (Number.isNaN(value)) return min;
    return Math.min(Math.max(value, min), max);
  };

  const createElementFromField = (field, point, labelSize) => {
    const width = field.type === 'barcode' ? 280 : 220;
    const height = field.defaultHeight || (field.type === 'barcode' ? 140 : 64);
    return {
      id: uniqueId(),
      type: field.type === 'barcode' ? 'barcode' : 'field',
      fieldKey: field.fieldKey,
      label: field.label,
      text: field.preview,
      dataBinding: field.groupId
        ? {
            groupId: field.groupId,
            fieldId: field.id,
            label: field.label,
            fieldKey: field.fieldKey,
            groupLabel: field.groupLabel
          }
        : null,
      x: clamp(point.x - width / 2, 0, Math.max(labelSize.width - width, 0)),
      y: clamp(point.y - height / 2, 0, Math.max(labelSize.height - height, 0)),
      width,
      height,
      rotation: 0,
      fontFamily: 'Inter, sans-serif',
      fontSize: field.type === 'barcode' ? 32 : 18,
      fontWeight: '600',
      textAlign: 'left',
      color: '#111827',
      background: field.type === 'barcode' ? '#0f172a' : 'rgba(255,255,255,0.85)',
      locked: false
    };
  };

  const createTextElement = (labelSize) => {
    const width = 260;
    const height = 72;
    return {
      id: uniqueId(),
      type: 'text',
      text: 'Custom text',
      dataBinding: null,
      fieldKey: null,
      x: (labelSize.width - width) / 2,
      y: (labelSize.height - height) / 2,
      width,
      height,
      rotation: 0,
      fontFamily: 'Inter, sans-serif',
      fontSize: 20,
      fontWeight: '600',
      textAlign: 'center',
      color: '#0f172a',
      background: 'rgba(255,255,255,0.85)',
      locked: false
    };
  };

  const createImageElement = (labelSize, src, naturalSize) => {
    const baseWidth = Math.min(naturalSize?.width || 220, labelSize.width * 0.6);
    const aspectRatio = (naturalSize?.height || 120) / (naturalSize?.width || 220);
    const height = clamp(baseWidth * aspectRatio, MIN_SIZE, labelSize.height * 0.6);
    return {
      id: uniqueId(),
      type: 'image',
      src,
      dataBinding: null,
      x: (labelSize.width - baseWidth) / 2,
      y: (labelSize.height - height) / 2,
      width: baseWidth,
      height,
      rotation: 0,
      locked: false
    };
  };

  const computeGuides = (targetId, draft, elements, labelSize) => {
    const tolerance = 6;
    let vertical = null;
    let horizontal = null;
    let snappedX = null;
    let snappedY = null;

    const centerX = draft.x + draft.width / 2;
    const centerY = draft.y + draft.height / 2;

    const maybeSnap = (value, target, axis) => {
      if (Math.abs(value - target) <= tolerance) {
        if (axis === 'x') {
          vertical = target;
        } else {
          horizontal = target;
        }
        return true;
      }
      return false;
    };

    if (maybeSnap(centerX, labelSize.width / 2, 'x')) {
      snappedX = labelSize.width / 2 - draft.width / 2;
    }
    if (maybeSnap(centerY, labelSize.height / 2, 'y')) {
      snappedY = labelSize.height / 2 - draft.height / 2;
    }

    elements.forEach((el) => {
      if (el.id === targetId) return;
      const elCenterX = el.x + el.width / 2;
      const elCenterY = el.y + el.height / 2;
      const elLeft = el.x;
      const elRight = el.x + el.width;
      const elTop = el.y;
      const elBottom = el.y + el.height;

      if (maybeSnap(centerX, elCenterX, 'x')) {
        snappedX = draft.x + (elCenterX - centerX);
      }
      if (maybeSnap(centerY, elCenterY, 'y')) {
        snappedY = draft.y + (elCenterY - centerY);
      }
      if (maybeSnap(draft.x, elLeft, 'x')) {
        snappedX = elLeft;
      }
      if (maybeSnap(draft.x, elRight, 'x')) {
        snappedX = elRight;
      }
      if (maybeSnap(draft.x + draft.width, elRight, 'x')) {
        snappedX = elRight - draft.width;
      }
      if (maybeSnap(draft.x + draft.width, elLeft, 'x')) {
        snappedX = elLeft - draft.width;
      }
      if (maybeSnap(draft.y, elTop, 'y')) {
        snappedY = elTop;
      }
      if (maybeSnap(draft.y, elBottom, 'y')) {
        snappedY = elBottom;
      }
      if (maybeSnap(draft.y + draft.height, elBottom, 'y')) {
        snappedY = elBottom - draft.height;
      }
      if (maybeSnap(draft.y + draft.height, elTop, 'y')) {
        snappedY = elTop - draft.height;
      }
    });

    if (maybeSnap(draft.x, 0, 'x')) {
      snappedX = 0;
    }
    if (maybeSnap(draft.y, 0, 'y')) {
      snappedY = 0;
    }
    if (maybeSnap(draft.x + draft.width, labelSize.width, 'x')) {
      snappedX = labelSize.width - draft.width;
    }
    if (maybeSnap(draft.y + draft.height, labelSize.height, 'y')) {
      snappedY = labelSize.height - draft.height;
    }

    return { vertical, horizontal, snappedX, snappedY };
  };

  const round = (value, decimals = 0) => {
    const factor = 10 ** decimals;
    return Math.round(value * factor) / factor;
  };
  const LabelDesigner = () => {
    const defaultTemplate = TEMPLATE_LOOKUP[DEFAULT_LABEL_NAME] || CUSTOM_TEMPLATE;
    const defaultLayout = (defaultTemplate && defaultTemplate.layout) || {};
    const defaultDimensions = resolveLabelSizeOption(defaultLayout.width, defaultLayout.height);
    const [activeTemplateName, setActiveTemplateName] = useState(DEFAULT_LABEL_NAME);
    const [elements, setElements] = useState([]);
    const [selectedIds, setSelectedIds] = useState([]);
    const [labelSize, setLabelSize] = useState(defaultDimensions.size);
    const [zoom, setZoom] = useState(1);
    const [guides, setGuides] = useState({ vertical: null, horizontal: null });
    const [exportedJSON, setExportedJSON] = useState('');
    const [importValue, setImportValue] = useState('');
    const [customSize, setCustomSize] = useState(defaultDimensions.custom);
    const [isPrinting, setIsPrinting] = useState(false);
    const [printFeedback, setPrintFeedback] = useState(null);
    const [showPreview, setShowPreview] = useState(false);
    const canvasRef = useRef(null);
    const fileInputRef = useRef(null);
    const elementsRef = useRef(elements);
    const skipTemplateLoadRef = useRef(false);
    const { trialPrintUrl, selectedPrinterName } = DESIGNER_CONFIG;
    const canSendTrial = Boolean(trialPrintUrl);

    const activeTemplate = useMemo(
      () => TEMPLATE_LOOKUP[activeTemplateName] || CUSTOM_TEMPLATE,
      [activeTemplateName],
    );
    const allowedFieldKeys = useMemo(() => deriveAllowedFieldKeys(activeTemplate), [activeTemplate]);
    const filteredFieldGroups = useMemo(
      () => filterFieldGroups(allowedFieldKeys),
      [allowedFieldKeys],
    );
    const availableFields = useMemo(() => flattenFieldGroups(filteredFieldGroups), [filteredFieldGroups]);
    const templateSourceLabel =
      activeTemplate?.source === 'database'
        ? 'Database template'
        : activeTemplate?.source === 'builtin'
          ? 'Built-in template'
          : activeTemplate?.source === 'custom'
            ? 'Custom layout'
            : null;

    useEffect(() => {
      elementsRef.current = elements;
    }, [elements]);

    useEffect(() => {
      if (!activeTemplate) {
        return;
      }
      if (skipTemplateLoadRef.current) {
        skipTemplateLoadRef.current = false;
        return;
      }
      loadLayout(activeTemplate.layout || {});
    }, [activeTemplate, loadLayout]);

    const activeLabelSize = useMemo(() => {
      if (labelSize.id !== 'custom') {
        return labelSize;
      }
      return { ...labelSize, width: customSize.width, height: customSize.height };
    }, [labelSize, customSize]);

    useEffect(() => {
      setElements((prev) => {
        let changed = false;
        const next = prev.map((el) => {
          const bounded = clampElementWithinBounds(el, activeLabelSize);
          if (
            bounded.x !== el.x ||
            bounded.y !== el.y ||
            bounded.width !== el.width ||
            bounded.height !== el.height
          ) {
            changed = true;
            return bounded;
          }
          return el;
        });
        return changed ? next : prev;
      });
    }, [activeLabelSize.width, activeLabelSize.height]);

    useEffect(() => {
      setElements((prev) => {
        let changed = false;
        const next = prev.map((el) => {
          if (el.fieldKey && !el.dataBinding) {
            const match = FIELD_LOOKUP.get(el.fieldKey);
            if (match) {
              changed = true;
              return {
                ...el,
                label: el.label || match.label,
                dataBinding: {
                  groupId: match.groupId,
                  fieldId: match.id,
                  label: match.label,
                  fieldKey: match.fieldKey,
                  groupLabel: match.groupLabel
                }
              };
            }
          }
          return el;
        });
        return changed ? next : prev;
      });
    }, []);

    const selectedElement = useMemo(() => {
      if (!selectedIds.length) return null;
      const activeId = selectedIds[selectedIds.length - 1];
      return elements.find((el) => el.id === activeId) || null;
    }, [elements, selectedIds]);

    const selectedElements = useMemo(
      () => elements.filter((el) => selectedIds.includes(el.id)),
      [elements, selectedIds]
    );

    useEffect(() => {
      const handleKeyDown = (event) => {
        if (!selectedIds.length) return;
        const tagName = event.target?.tagName || '';
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tagName) || event.target?.isContentEditable) {
          return;
        }

        let deltaX = 0;
        let deltaY = 0;
        if (event.key === 'ArrowLeft') deltaX = -1;
        if (event.key === 'ArrowRight') deltaX = 1;
        if (event.key === 'ArrowUp') deltaY = -1;
        if (event.key === 'ArrowDown') deltaY = 1;

        if (deltaX === 0 && deltaY === 0) return;

        event.preventDefault();
        const increment = event.shiftKey ? GRID_SIZE : 1;

        setElements((prev) => {
          const selectedSet = new Set(selectedIds);
          let changed = false;
          const next = prev.map((el) => {
            if (!selectedSet.has(el.id) || el.locked) {
              return el;
            }
            const updated = clampElementWithinBounds(
              {
                ...el,
                x: el.x + deltaX * increment,
                y: el.y + deltaY * increment
              },
              activeLabelSize
            );
            if (updated.x !== el.x || updated.y !== el.y) {
              changed = true;
            }
            return updated;
          });
          return changed ? next : prev;
        });
      };

      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }, [selectedIds, activeLabelSize]);

    const selectedBinding = useMemo(() => {
      if (!selectedElement) return null;
      if (selectedElement.dataBinding) return selectedElement.dataBinding;
      if (selectedElement.fieldKey) {
        const match = FIELD_LOOKUP.get(selectedElement.fieldKey);
        if (match) {
          return {
            groupId: match.groupId,
            fieldId: match.id,
            label: match.label,
            fieldKey: match.fieldKey,
            groupLabel: match.groupLabel
          };
        }
      }
      return null;
    }, [selectedElement]);

    const bindingFieldGroups = useMemo(() => {
      const baseGroups = filteredFieldGroups.map((group) => ({
        id: group.id,
        label: group.label,
        fields: group.fields,
      }));
      if (selectedBinding && selectedBinding.fieldKey) {
        const exists = baseGroups.some((group) =>
          group.fields.some((field) => field.fieldKey === selectedBinding.fieldKey),
        );
        if (!exists) {
          const fallback = FIELD_LOOKUP.get(selectedBinding.fieldKey);
          if (fallback) {
            baseGroups.unshift({
              id: fallback.groupId || `extra-${fallback.id}`,
              label: fallback.groupLabel || 'Additional fields',
              fields: [{ ...fallback }],
            });
          }
        }
      }
      return baseGroups;
    }, [filteredFieldGroups, selectedBinding]);

    const bindingFields = useMemo(
      () => flattenFieldGroups(bindingFieldGroups),
      [bindingFieldGroups],
    );

    const updateElement = (id, updates) => {
      setElements((prev) => prev.map((el) => (el.id === id ? { ...el, ...updates } : el)));
    };

    const updateElements = (updates) => {
      setElements((prev) => {
        let changed = false;
        const next = prev.map((el) => {
          if (!updates[el.id]) return el;
          changed = true;
          return { ...el, ...updates[el.id] };
        });
        return changed ? next : prev;
      });
    };

    const toggleLockSelection = (forceState) => {
      if (!selectedIds.length) return;
      const shouldLock =
        typeof forceState === 'boolean'
          ? forceState
          : selectedElements.some((el) => !el.locked);
      const updates = {};
      selectedIds.forEach((id) => {
        updates[id] = { locked: shouldLock };
      });
      updateElements(updates);
    };

    const alignSelection = (mode) => {
      if (selectedElements.length < 2) return;
      const movable = selectedElements.filter((el) => !el.locked);
      if (!movable.length) return;

      const left = Math.min(...movable.map((el) => el.x));
      const right = Math.max(...movable.map((el) => el.x + el.width));
      const top = Math.min(...movable.map((el) => el.y));
      const bottom = Math.max(...movable.map((el) => el.y + el.height));
      const centerX = (left + right) / 2;
      const centerY = (top + bottom) / 2;

      const updates = {};
      movable.forEach((el) => {
        let nextX = el.x;
        let nextY = el.y;
        if (mode === 'left') nextX = left;
        if (mode === 'right') nextX = right - el.width;
        if (mode === 'center') nextX = centerX - el.width / 2;
        if (mode === 'top') nextY = top;
        if (mode === 'bottom') nextY = bottom - el.height;
        if (mode === 'middle') nextY = centerY - el.height / 2;
        updates[el.id] = clampElementWithinBounds(
          { ...el, x: nextX, y: nextY },
          activeLabelSize,
          { snap: true }
        );
      });
      updateElements(updates);
    };

    const distributeSelection = (axis) => {
      if (selectedElements.length < 3) return;
      const movable = selectedElements.filter((el) => !el.locked);
      if (movable.length < 3) return;

      const updates = {};
      if (axis === 'horizontal') {
        const sorted = [...movable].sort((a, b) => a.x - b.x);
        const first = sorted[0];
        const last = sorted[sorted.length - 1];
        const start = first.x + first.width / 2;
        const end = last.x + last.width / 2;
        if (Math.abs(end - start) < 1) return;
        const step = (end - start) / (sorted.length - 1);
        sorted.forEach((el, index) => {
          if (index === 0 || index === sorted.length - 1) return;
          const center = start + step * index;
          const nextX = center - el.width / 2;
          updates[el.id] = clampElementWithinBounds(
            { ...el, x: nextX },
            activeLabelSize,
            { snap: true }
          );
        });
      } else {
        const sorted = [...movable].sort((a, b) => a.y - b.y);
        const first = sorted[0];
        const last = sorted[sorted.length - 1];
        const start = first.y + first.height / 2;
        const end = last.y + last.height / 2;
        if (Math.abs(end - start) < 1) return;
        const step = (end - start) / (sorted.length - 1);
        sorted.forEach((el, index) => {
          if (index === 0 || index === sorted.length - 1) return;
          const center = start + step * index;
          const nextY = center - el.height / 2;
          updates[el.id] = clampElementWithinBounds(
            { ...el, y: nextY },
            activeLabelSize,
            { snap: true }
          );
        });
      }

      if (Object.keys(updates).length) {
        updateElements(updates);
      }
    };

    const clampElementWithinBounds = (el, size, options = {}) => {
      const { snap = false } = options;
      let width = clamp(el.width, MIN_SIZE, size.width);
      let height = clamp(el.height, MIN_SIZE, size.height);
      let x = clamp(el.x, 0, Math.max(size.width - width, 0));
      let y = clamp(el.y, 0, Math.max(size.height - height, 0));

    if (snap) {
      width = clamp(snapSize(width), MIN_SIZE, size.width);
      height = clamp(snapSize(height), MIN_SIZE, size.height);
      x = clamp(snapPosition(x), 0, Math.max(size.width - width, 0));
      y = clamp(snapPosition(y), 0, Math.max(size.height - height, 0));
    }

    return { ...el, x, y, width, height };
    };

    const loadLayout = useCallback(
      (layout, options = {}) => {
        const { selectFirst = true } = options;
        const layoutConfig = layout && typeof layout === 'object' ? layout : {};
        const dimensions = resolveLabelSizeOption(layoutConfig.width, layoutConfig.height);
        setLabelSize(dimensions.size);
        setCustomSize(dimensions.custom);
        const targetSize =
          dimensions.size.id === 'custom'
            ? { width: dimensions.custom.width, height: dimensions.custom.height }
            : { width: dimensions.size.width, height: dimensions.size.height };
        const rawElements = Array.isArray(layoutConfig.elements) ? layoutConfig.elements : [];
        const normalized = rawElements
          .map((element) => normalizeLayoutElement(element))
          .filter(Boolean)
          .map((element) => clampElementWithinBounds(element, targetSize, { snap: false }));
        setElements(normalized);
        setSelectedIds(selectFirst && normalized[0] ? [normalized[0].id] : []);
      },
      [setCustomSize, setElements, setLabelSize, setSelectedIds],
    );

    const handleDrop = (event) => {
      event.preventDefault();
      const data = event.dataTransfer.getData('text/plain');
      if (!data) return;
      const [kind, groupId, fieldId] = data.split(':');
      if (kind !== 'field') return;
      const field = availableFields.find((f) => f.groupId === groupId && f.id === fieldId);
      if (!field) return;
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const dropPoint = {
        x: (event.clientX - bounds.left) / zoom,
        y: (event.clientY - bounds.top) / zoom
      };
      let element = createElementFromField(field, dropPoint, activeLabelSize);
      element = clampElementWithinBounds(element, activeLabelSize, { snap: true });
      setElements((prev) => [...prev, element]);
      setSelectedIds([element.id]);
    };

    const handleDragOver = (event) => {
      event.preventDefault();
    };

    const handleElementPointerDown = (event, element) => {
      event.preventDefault();
      event.stopPropagation();
      const multi = event.shiftKey || event.metaKey || event.ctrlKey;

      let nextSelection;
      if (multi) {
        if (selectedIds.includes(element.id)) {
          nextSelection = selectedIds.filter((id) => id !== element.id);
        } else {
          nextSelection = [...selectedIds, element.id];
        }
      } else if (selectedIds.length === 1 && selectedIds[0] === element.id) {
        nextSelection = selectedIds;
      } else {
        nextSelection = [element.id];
      }

      setSelectedIds(nextSelection);

      if (!nextSelection.includes(element.id)) {
        return;
      }

      const movableIds = nextSelection.filter((id) => {
        const target = elementsRef.current.find((item) => item.id === id);
        return target && !target.locked;
      });

      if (!movableIds.includes(element.id) || movableIds.length === 0) {
        return;
      }

      startMove(event, element, movableIds);
    };

    const startMove = (event, element, activeIds) => {
      event.preventDefault();
      event.stopPropagation();
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;

      const startX = event.clientX;
      const startY = event.clientY;
      const initialElements = activeIds
        .map((id) => {
          const current = elementsRef.current.find((el) => el.id === id);
          if (!current) return null;
          return {
            id: current.id,
            x: current.x,
            y: current.y,
            width: current.width,
            height: current.height
          };
        })
        .filter(Boolean);

      if (!initialElements.length) return;

      const primary = initialElements.find((item) => item.id === element.id) || initialElements[0];

      const handlePointerMove = (moveEvent) => {
        const deltaX = (moveEvent.clientX - startX) / zoom;
        const deltaY = (moveEvent.clientY - startY) / zoom;

        const draftPrimary = {
          x: primary.x + deltaX,
          y: primary.y + deltaY,
          width: primary.width,
          height: primary.height
        };

        const { vertical, horizontal, snappedX, snappedY } = computeGuides(
          primary.id,
          draftPrimary,
          elementsRef.current,
          activeLabelSize
        );

        let finalX = snappedX !== null ? snappedX : snapPosition(draftPrimary.x);
        let finalY = snappedY !== null ? snappedY : snapPosition(draftPrimary.y);

        const boundedPrimary = clampElementWithinBounds(
          { ...primary, x: finalX, y: finalY },
          activeLabelSize,
          { snap: false }
        );

        finalX = boundedPrimary.x;
        finalY = boundedPrimary.y;

        const appliedDeltaX = finalX - primary.x;
        const appliedDeltaY = finalY - primary.y;

        const updates = {};
        initialElements.forEach((item) => {
          const draft = {
            ...item,
            x: item.x + appliedDeltaX,
            y: item.y + appliedDeltaY
          };
          updates[item.id] = clampElementWithinBounds(draft, activeLabelSize, { snap: true });
        });

        setGuides({ vertical, horizontal });
        updateElements(updates);
      };

      const handlePointerUp = () => {
        setGuides({ vertical: null, horizontal: null });
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
      };

      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    };

    const startResize = (event, element, direction) => {
      if (element.locked) return;
      event.preventDefault();
      event.stopPropagation();
      setSelectedIds([element.id]);
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const startX = event.clientX;
      const startY = event.clientY;
      const initial = {
        x: element.x,
        y: element.y,
        width: element.width,
        height: element.height
      };

      const handlePointerMove = (moveEvent) => {
        const deltaX = (moveEvent.clientX - startX) / zoom;
        const deltaY = (moveEvent.clientY - startY) / zoom;

        let newWidth = initial.width;
        let newHeight = initial.height;
        let newX = initial.x;
        let newY = initial.y;

        if (direction.includes('e')) {
          newWidth = clamp(initial.width + deltaX, MIN_SIZE, activeLabelSize.width - initial.x);
        }
        if (direction.includes('s')) {
          newHeight = clamp(initial.height + deltaY, MIN_SIZE, activeLabelSize.height - initial.y);
        }
        if (direction.includes('w')) {
          newWidth = clamp(initial.width - deltaX, MIN_SIZE, activeLabelSize.width);
          newX = clamp(initial.x + deltaX, 0, initial.x + initial.width - MIN_SIZE);
        }
        if (direction.includes('n')) {
          newHeight = clamp(initial.height - deltaY, MIN_SIZE, activeLabelSize.height);
          newY = clamp(initial.y + deltaY, 0, initial.y + initial.height - MIN_SIZE);
        }

        const draft = { x: newX, y: newY, width: newWidth, height: newHeight };
        const { vertical, horizontal, snappedX, snappedY } = computeGuides(
          element.id,
          draft,
          elementsRef.current,
          activeLabelSize
        );
        setGuides({ vertical, horizontal });
        const snappedDraft = {
          ...draft,
          x: snappedX !== null ? snappedX : draft.x,
          y: snappedY !== null ? snappedY : draft.y
        };
        const bounded = clampElementWithinBounds(snappedDraft, activeLabelSize, { snap: true });
        updateElement(element.id, bounded);
      };

      const handlePointerUp = () => {
        setGuides({ vertical: null, horizontal: null });
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
      };

      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    };

    const handleWheel = (event) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      setZoom((prev) => clamp(prev - event.deltaY * 0.0015, 0.3, 3));
    };

    const addTextBlock = () => {
      let element = createTextElement(activeLabelSize);
      element = clampElementWithinBounds(element, activeLabelSize, { snap: true });
      setElements((prev) => [...prev, element]);
      setSelectedIds([element.id]);
    };

    const handleUploadLogo = (event) => {
      const [file] = event.target.files || [];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          let element = createImageElement(activeLabelSize, reader.result, {
            width: img.naturalWidth,
            height: img.naturalHeight
          });
          element = clampElementWithinBounds(element, activeLabelSize, { snap: true });
          setElements((prev) => [...prev, element]);
          setSelectedIds([element.id]);
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
      event.target.value = '';
    };

    const handleTemplateReset = () => {
      if (!activeTemplate) {
        return;
      }
      loadLayout(activeTemplate.layout || {}, { selectFirst: true });
    };

    const buildLayoutPayload = () => {
      const targetSize = {
        width: activeLabelSize.width,
        height: activeLabelSize.height,
      };
      const layoutElements = elements.map((el) => {
        const base = {
          id: el.id,
          type: el.type,
          x: el.x,
          y: el.y,
          width: el.width,
          height: el.height,
          rotation: el.rotation,
          locked: Boolean(el.locked),
        };
        if (el.fieldKey) {
          base.fieldKey = el.fieldKey;
          base.label = el.label;
        }
        if (el.dataBinding) {
          base.dataBinding = el.dataBinding;
        }
        if (el.type === 'image') {
          base.src = el.src;
        } else {
          base.text = el.text;
          base.fontFamily = el.fontFamily;
          base.fontSize = el.fontSize;
          base.fontWeight = el.fontWeight;
          base.textAlign = el.textAlign;
          base.color = el.color;
          base.background = el.background;
          if (el.prefix) {
            base.prefix = el.prefix;
          }
          if (el.suffix) {
            base.suffix = el.suffix;
          }
          if (el.uppercase) {
            base.uppercase = Boolean(el.uppercase);
          }
        }
        if (el.type === 'barcode') {
          base.printValue = el.printValue !== undefined ? Boolean(el.printValue) : true;
          base.checkDigit = el.checkDigit !== undefined ? Boolean(el.checkDigit) : false;
          base.orientation = el.orientation || 'N';
        }
        if (el.type === 'box') {
          base.thickness = ensureNumber(el.thickness, 2);
        }
        return base;
      });
      const templateLabel = activeTemplate?.display_name || activeTemplate?.description || activeTemplate?.name;
      return {
        templateName: activeTemplateName,
        templateDisplayName: templateLabel,
        labelSize: targetSize,
        elements: layoutElements,
      };
    };

    const handleExport = () => {
      const payload = buildLayoutPayload();
      setExportedJSON(JSON.stringify(payload, null, 2));
    };

    const handleTrialPrint = async () => {
      setPrintFeedback(null);
      if (!trialPrintUrl) {
        setPrintFeedback({ type: 'error', message: 'Trial print endpoint is not configured.' });
        return;
      }
      setIsPrinting(true);
      try {
        const payload = buildLayoutPayload();
        const response = await fetch(trialPrintUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ layout: payload })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(result?.message || 'Failed to send the trial print.');
        }
        setPrintFeedback({
          type: 'success',
          message:
            result?.message ||
            `Trial print sent${selectedPrinterName ? ` to ${selectedPrinterName}` : ''}.`
        });
      } catch (error) {
        console.error(error);
        setPrintFeedback({
          type: 'error',
          message: error?.message || 'Unable to send a trial print right now.'
        });
      } finally {
        setIsPrinting(false);
      }
    };

    const handleImport = () => {
      if (!importValue.trim()) return;
      try {
        const parsed = JSON.parse(importValue);
        const payload = parsed && typeof parsed === 'object' ? parsed : {};
        const importedLayout =
          payload.layout && typeof payload.layout === 'object' ? payload.layout : payload;
        const importedSize = payload.labelSize && typeof payload.labelSize === 'object'
          ? payload.labelSize
          : {};
        const layout = {
          ...importedLayout,
          width: importedLayout.width || importedSize.width,
          height: importedLayout.height || importedSize.height,
        };
        if (!layout.elements || !Array.isArray(layout.elements)) {
          throw new Error('Invalid layout format');
        }
        if (
          payload.templateName &&
          payload.templateName !== activeTemplateName &&
          TEMPLATE_LOOKUP[payload.templateName]
        ) {
          skipTemplateLoadRef.current = true;
          setActiveTemplateName(payload.templateName);
        }
        loadLayout(layout);
        setImportValue('');
      } catch (error) {
        console.error(error);
        alert('Unable to import layout. Please ensure the JSON is valid.');
      }
    };

    const handleRemoveElement = (id) => {
      setElements((prev) => prev.filter((el) => el.id !== id));
      setSelectedIds((prev) => prev.filter((selectedId) => selectedId !== id));
    };

    const previewScale = useMemo(() => {
      const maxDimension = Math.max(activeLabelSize.width, activeLabelSize.height);
      return 260 / maxDimension;
    }, [activeLabelSize]);

    const lockToggleLabel = selectedElements.some((el) => !el.locked)
      ? 'Lock selection'
      : 'Unlock selection';
    const movableSelectionCount = selectedElements.filter((el) => !el.locked).length;
    const allowAlign = movableSelectionCount >= 2;
    const allowDistribute = movableSelectionCount >= 3;

    const changeZoom = (delta) => {
      setZoom((prev) => clamp(prev + delta, 0.3, 3));
    };

    const handleCanvasClick = (event) => {
      event.stopPropagation();
      setSelectedIds([]);
    };
    const renderElementContent = (element, scale = zoom) => {
      if (element.type === 'image') {
        return React.createElement('img', {
          src: element.src,
          alt: element.label || 'Logo',
          className: 'h-full w-full object-contain pointer-events-none select-none'
        });
      }
      if (element.type === 'barcode') {
        return React.createElement(
          'div',
          {
            className: 'flex h-full w-full items-center justify-center uppercase tracking-[0.4em] text-white pointer-events-none select-none',
            style: {
              fontFamily: element.fontFamily,
              fontSize: `${element.fontSize * scale}px`,
              fontWeight: element.fontWeight
            }
          },
          element.text || element.label
        );
      }
      return React.createElement(
        'div',
        {
          className: 'h-full w-full whitespace-pre-wrap pointer-events-none select-none',
          style: {
            fontFamily: element.fontFamily,
            fontSize: `${element.fontSize * scale}px`,
            fontWeight: element.fontWeight,
            color: element.color,
            textAlign: element.textAlign
          }
        },
        element.text || element.label
      );
    };

    const canvasElements = elements.map((element) => {
      const isSelected = element.id === selectedId;
      const paddingBase = element.type === 'barcode' ? 14 : element.type === 'image' ? 0 : 10;
      const style = {
        position: 'absolute',
        left: element.x * zoom,
        top: element.y * zoom,
        width: element.width * zoom,
        height: element.height * zoom,
        transform: `rotate(${element.rotation}deg)`,
        transformOrigin: 'center center',
        border: isSelected ? '2px solid rgb(56 189 248)' : '1px dashed rgba(148, 163, 184, 0.8)',
        borderRadius: '0.75rem',
        background: element.type === 'image' ? 'transparent' : element.background || 'rgba(255,255,255,0.85)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: `${Math.max(paddingBase * zoom, 4)}px`,
        boxShadow: isSelected ? '0 0 0 4px rgba(56, 189, 248, 0.2)' : 'none',
        cursor: element.locked ? 'default' : 'move'
      };

      const handles = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
      const positions = {
        n: { top: 0, left: '50%', cursor: 'ns-resize', transform: 'translate(-50%, -50%)' },
        s: { top: '100%', left: '50%', cursor: 'ns-resize', transform: 'translate(-50%, -50%)' },
        e: { top: '50%', left: '100%', cursor: 'ew-resize', transform: 'translate(-50%, -50%)' },
        w: { top: '50%', left: 0, cursor: 'ew-resize', transform: 'translate(-50%, -50%)' },
        ne: { top: 0, left: '100%', cursor: 'nesw-resize', transform: 'translate(-50%, -50%)' },
        nw: { top: 0, left: 0, cursor: 'nwse-resize', transform: 'translate(-50%, -50%)' },
        se: { top: '100%', left: '100%', cursor: 'nwse-resize', transform: 'translate(-50%, -50%)' },
        sw: { top: '100%', left: 0, cursor: 'nesw-resize', transform: 'translate(-50%, -50%)' }
      };

      return React.createElement(
        'div',
        {
          key: element.id,
          role: 'presentation',
          className: 'absolute',
          style,
          onPointerDown: (e) => handleElementPointerDown(e, element)
        },
        element.locked &&
          React.createElement(
            'span',
            {
              className:
                'pointer-events-none absolute top-1 right-1 inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-900/80 text-[10px] font-bold text-white shadow'
            },
            '🔒'
          ),
        isSelected && !element.locked &&
          handles.map((dir) =>
            React.createElement('span', {
              key: dir,
              className: 'absolute h-3 w-3 rounded-full border border-white bg-sky-500 shadow',
              style: positions[dir],
              onPointerDown: (e) => startResize(e, element, dir)
            })
          ),
        renderElementContent(element)
      );
    });

    const guideElements = [];
    if (guides.vertical !== null) {
      guideElements.push(
        React.createElement('div', {
          key: 'guide-vertical',
          className: 'absolute top-0 bottom-0 w-px bg-sky-400/70 pointer-events-none',
          style: { left: guides.vertical * zoom }
        })
      );
    }
    if (guides.horizontal !== null) {
      guideElements.push(
        React.createElement('div', {
          key: 'guide-horizontal',
          className: 'absolute left-0 right-0 h-px bg-sky-400/70 pointer-events-none',
          style: { top: guides.horizontal * zoom }
        })
      );
    }

    const previewElements = elements.map((element) => {
      const paddingBase = element.type === 'barcode' ? 12 : element.type === 'image' ? 0 : 8;
      const style = {
        position: 'absolute',
        left: element.x * previewScale,
        top: element.y * previewScale,
        width: element.width * previewScale,
        height: element.height * previewScale,
        transform: `rotate(${element.rotation}deg)`,
        transformOrigin: 'center center',
        borderRadius: '0.5rem',
        background: element.type === 'image' ? 'transparent' : element.background || 'rgba(255,255,255,0.85)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: `${Math.max(paddingBase * previewScale, 2)}px`
      };
      return React.createElement(
        'div',
        { key: `preview-${element.id}`, className: 'absolute', style },
        renderElementContent(element, previewScale)
      );
    });
    const templateMetaDetails = [];
    if (activeTemplate?.triggers && activeTemplate.triggers.length) {
      templateMetaDetails.push(
        React.createElement(
          'span',
          { key: 'triggers', className: 'text-xs text-slate-500' },
          `Mapped processes: ${activeTemplate.triggers.join(', ')}`,
        ),
      );
    }
    if (templateSourceLabel) {
      templateMetaDetails.push(
        React.createElement(
          'span',
          {
            key: 'source',
            className:
              'inline-flex items-center rounded-full border border-slate-200 bg-slate-100 px-2 py-[2px] text-xs font-semibold text-slate-600',
          },
          templateSourceLabel,
        ),
      );
    }

    const fieldCards = filteredFieldGroups.length
      ? filteredFieldGroups.map((group) =>
          React.createElement(
            'div',
            { key: group.id, className: 'space-y-2' },
            React.createElement(
              'div',
              { className: 'space-y-1' },
              React.createElement(
                'h4',
                { className: 'text-xs font-semibold uppercase tracking-wide text-slate-500' },
                group.label
              ),
              React.createElement('p', { className: 'text-xs text-slate-500' }, group.description)
            ),
            ...group.fields.map((field) =>
              React.createElement(
                'div',
                {
                  key: field.id,
                  draggable: true,
                  onDragStart: (event) => {
                    event.dataTransfer.setData('text/plain', `field:${group.id}:${field.id}`);
                    event.dataTransfer.effectAllowed = 'copy';
                  },
                  className:
                    'cursor-grab rounded-lg border border-slate-200 bg-white p-3 text-sm shadow-sm transition hover:border-sky-400 hover:shadow'
                },
                React.createElement('div', { className: 'font-medium text-slate-800' }, field.label),
                React.createElement('p', { className: 'mt-1 text-xs text-slate-500' }, field.description)
              )
            )
          )
        )
      : [
          React.createElement(
            'div',
            {
              key: 'no-fields',
              className:
                'rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs font-medium text-amber-700 shadow-sm'
            },
            'No database fields are mapped to this label yet. Add custom text blocks or images while a data model is prepared.'
          ),
        ];
    const arrangeButtons = [
      { key: 'left', label: 'Align left', action: () => alignSelection('left') },
      { key: 'center', label: 'Align center', action: () => alignSelection('center') },
      { key: 'right', label: 'Align right', action: () => alignSelection('right') },
      { key: 'top', label: 'Align top', action: () => alignSelection('top') },
      { key: 'middle', label: 'Align middle', action: () => alignSelection('middle') },
      { key: 'bottom', label: 'Align bottom', action: () => alignSelection('bottom') }
    ];

    const distributeButtons = [
      { key: 'horizontal', label: 'Distribute horizontal', action: () => distributeSelection('horizontal') },
      { key: 'vertical', label: 'Distribute vertical', action: () => distributeSelection('vertical') }
    ];

    const alignmentButtons = selectedElement
      ? ALIGN_OPTIONS.map((option) =>
          React.createElement(
            'button',
            {
              key: option.value,
              type: 'button',
              onClick: () => updateElement(selectedElement.id, { textAlign: option.value }),
              className: `rounded-md border px-2 py-1 text-xs font-semibold transition ${
                selectedElement.textAlign === option.value
                  ? 'border-sky-500 bg-sky-50 text-sky-600'
                  : 'border-slate-300 text-slate-500 hover:border-sky-400'
              }`
            },
            option.label
          )
        )
      : [];

    const customizationContent = selectedElement
      ? React.createElement(
          'div',
          { className: 'space-y-4' },
          React.createElement(
            'div',
            { className: 'flex items-start justify-between gap-3' },
            React.createElement('div', null,
              React.createElement('h4', { className: 'text-sm font-semibold uppercase tracking-wide text-slate-700' }, 'Element settings'),
              React.createElement('p', { className: 'text-xs text-slate-500' }, selectedElement.type === 'image' ? 'Adjust dimensions, position, and rotation.' : 'Update typography, alignment, and copy.')
            ),
            React.createElement(
              'div',
              { className: 'flex items-center gap-2' },
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => updateElement(selectedElement.id, { locked: !selectedElement.locked }),
                  className: `rounded-md border px-2 py-1 text-xs font-semibold transition ${
                    selectedElement.locked
                      ? 'border-emerald-400 text-emerald-600 hover:bg-emerald-50'
                      : 'border-slate-300 text-slate-600 hover:border-sky-400'
                  }`
                },
                selectedElement.locked ? 'Unlock element' : 'Lock element'
              ),
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => handleRemoveElement(selectedElement.id),
                  className: 'rounded-md border border-rose-200 px-2 py-1 text-xs font-semibold text-rose-500 hover:bg-rose-50'
                },
                'Remove'
              )
            )
          ),
          selectedBinding &&
            React.createElement(
              'div',
              { className: 'rounded-lg bg-slate-100 p-2 text-xs text-slate-600' },
              'Bound field: ',
              React.createElement(
                'span',
                { className: 'font-semibold text-slate-700' },
                `${selectedBinding.groupLabel || ''}${selectedBinding.groupLabel ? ' • ' : ''}${selectedElement.label || selectedBinding.label}`
              )
            ),
          selectedElement.type !== 'image' &&
            React.createElement(
              'label',
              { className: 'block space-y-1 text-xs font-semibold text-slate-600' },
              React.createElement('span', null, 'Data binding'),
              React.createElement(
                'select',
                {
                  value: selectedBinding ? `${selectedBinding.groupId}:${selectedBinding.fieldId}` : '__none__',
                  onChange: (event) => {
                    const value = event.target.value;
                    if (value === '__none__') {
                      updateElement(selectedElement.id, {
                        fieldKey: null,
                        label: selectedElement.type === 'barcode' ? selectedElement.label : null,
                        dataBinding: null
                      });
                      return;
                    }
                    const [groupId, fieldId] = value.split(':');
                    const field = bindingFields.find((f) => f.groupId === groupId && f.id === fieldId);
                    if (!field) return;
                    const updates = {
                      fieldKey: field.fieldKey,
                      label: field.label,
                      dataBinding: {
                        groupId: field.groupId,
                        fieldId: field.id,
                        label: field.label,
                        fieldKey: field.fieldKey,
                        groupLabel: field.groupLabel
                      }
                    };
                    if (selectedElement.type !== 'image') {
                      updates.text = field.preview;
                    }
                    updateElement(selectedElement.id, updates);
                  },
                  className:
                    'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                },
                React.createElement('option', { value: '__none__' }, 'Manual content'),
                ...bindingFieldGroups.map((group) =>
                  React.createElement(
                    'optgroup',
                    { key: group.id, label: group.label },
                    group.fields.map((field) =>
                      React.createElement(
                        'option',
                        { key: `${group.id}:${field.id}`, value: `${group.id}:${field.id}` },
                        field.label
                      )
                    )
                  )
                )
              ),
              React.createElement(
                'p',
                { className: 'text-[11px] font-normal text-slate-500' },
                'Bind this element to inventory, batch, or order data, or keep it as manual text.'
              )
            ),
          selectedElement.type !== 'image' &&
            React.createElement(
              'label',
              { className: 'block space-y-1 text-xs font-semibold text-slate-600' },
              React.createElement('span', null, 'Text'),
              React.createElement('textarea', {
                value: selectedElement.text || '',
                onChange: (event) => updateElement(selectedElement.id, { text: event.target.value }),
                rows: 3,
                className: 'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
              })
            ),
          selectedElement.type !== 'image' &&
            React.createElement(
              'div',
              { className: 'grid grid-cols-2 gap-2 text-xs font-semibold text-slate-600' },
              React.createElement(
                'label',
                { className: 'space-y-1' },
                React.createElement('span', null, 'Font'),
                React.createElement(
                  'select',
                  {
                    value: selectedElement.fontFamily,
                    onChange: (event) => updateElement(selectedElement.id, { fontFamily: event.target.value }),
                    className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                  },
                  FONT_FAMILIES.map((font) =>
                    React.createElement('option', { key: font.value, value: font.value }, font.label)
                  )
                )
              ),
              React.createElement(
                'label',
                { className: 'space-y-1' },
                React.createElement('span', null, 'Weight'),
                React.createElement(
                  'select',
                  {
                    value: selectedElement.fontWeight,
                    onChange: (event) => updateElement(selectedElement.id, { fontWeight: event.target.value }),
                    className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                  },
                  FONT_WEIGHTS.map((option) =>
                    React.createElement('option', { key: option.value, value: option.value }, option.label)
                  )
                )
              )
            ),
          selectedElement.type !== 'image' &&
            React.createElement(
              'div',
              { className: 'flex flex-wrap gap-2 text-xs font-semibold text-slate-600' },
              React.createElement('span', { className: 'self-center' }, 'Align'),
              ...alignmentButtons
            ),
          selectedElement.type !== 'image' &&
            React.createElement(
              'label',
              { className: 'block space-y-1 text-xs font-semibold text-slate-600' },
              React.createElement('span', null, 'Font size (pt)'),
              React.createElement('input', {
                type: 'number',
                min: 6,
                max: 120,
                value: round(selectedElement.fontSize, 0),
                onChange: (event) => {
                  const value = clamp(Number(event.target.value), 6, 120);
                  updateElement(selectedElement.id, { fontSize: value });
                },
                className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
              })
            ),
          selectedElement.type !== 'image' &&
            React.createElement(
              'label',
              { className: 'flex items-center justify-between gap-2 text-xs font-semibold text-slate-600' },
              React.createElement('span', null, 'Color'),
              React.createElement('input', {
                type: 'color',
                value: selectedElement.color || '#111827',
                onChange: (event) => updateElement(selectedElement.id, { color: event.target.value })
              })
            ),
          React.createElement(
            'label',
            { className: 'block space-y-1 text-xs font-semibold text-slate-600' },
            React.createElement('span', null, 'Rotation'),
            React.createElement('input', {
              type: 'range',
              min: -180,
              max: 180,
              step: 1,
              value: selectedElement.rotation,
              onChange: (event) => updateElement(selectedElement.id, { rotation: Number(event.target.value) }),
              className: 'w-full accent-sky-500'
            }),
            React.createElement('div', { className: 'text-right text-xs text-slate-500' }, `${selectedElement.rotation}\u00B0`)
          ),
          React.createElement(
            'div',
            { className: 'grid grid-cols-2 gap-2 text-xs font-semibold text-slate-600' },
            ['X', 'Y', 'Width', 'Height'].map((label, index) => {
              const keys = ['x', 'y', 'width', 'height'];
              const key = keys[index];
              return React.createElement(
                'label',
                { key: key, className: 'space-y-1' },
                React.createElement('span', null, label),
                React.createElement('input', {
                  type: 'number',
                  value: round(selectedElement[key], 0),
                  onChange: (event) => {
                    const value = Number(event.target.value);
                    if (Number.isNaN(value)) return;
                    const bounded = clampElementWithinBounds(
                      { ...selectedElement, [key]: value },
                      activeLabelSize
                    );
                    updateElement(selectedElement.id, {
                      x: bounded.x,
                      y: bounded.y,
                      width: bounded.width,
                      height: bounded.height
                    });
                  },
                  className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                })
              );
            })
          )
        )
      : React.createElement('p', { className: 'text-sm text-slate-500' }, 'Select an element on the canvas to customize it.');
    return React.createElement(
      'div',
      { className: 'flex w-full flex-col gap-6 text-slate-900' },
      React.createElement(
        'div',
        { className: 'flex flex-col gap-4 lg:flex-row' },
        React.createElement(
          'aside',
          { className: 'w-full max-w-xs rounded-xl border border-slate-200 bg-white/70 p-4 backdrop-blur lg:w-72' },
          React.createElement(
            'div',
            { className: 'space-y-4' },
            React.createElement(
              'div',
              { className: 'space-y-1' },
              React.createElement('h4', { className: 'text-sm font-semibold uppercase tracking-wide text-slate-700' }, 'Available fields'),
              React.createElement('p', { className: 'text-xs text-slate-500' }, 'Drag items into the label canvas to place them.')
            ),
            ...fieldCards,
            React.createElement(
              'button',
              {
                type: 'button',
                onClick: addTextBlock,
                className: 'w-full rounded-lg bg-sky-500 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-sky-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500'
              },
              'Add text block'
            ),
            React.createElement(
              'div',
              { className: 'flex flex-col gap-2 rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-600' },
              React.createElement('p', { className: 'font-semibold uppercase text-slate-500' }, 'Logos & images'),
              React.createElement('p', { className: 'text-slate-500' }, 'Upload brand marks or certification badges.'),
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => fileInputRef.current?.click(),
                  className: 'rounded-md border border-sky-400 px-3 py-1.5 text-xs font-semibold text-sky-600 hover:bg-sky-50'
                },
                'Upload image'
              ),
              React.createElement('input', {
                ref: fileInputRef,
                type: 'file',
                accept: 'image/*',
                className: 'hidden',
                onChange: handleUploadLogo
              })
            )
          )
        ),
        React.createElement(
          'div',
          { className: 'flex-1 space-y-4' },
          React.createElement(
            'div',
            {
              className:
                'space-y-3 rounded-xl border border-slate-200 bg-white/70 p-4 shadow-sm backdrop-blur',
            },
            React.createElement(
              'div',
              { className: 'flex flex-wrap items-start justify-between gap-3' },
              React.createElement(
                'div',
                { className: 'space-y-1 text-sm text-slate-700 max-w-xl' },
                React.createElement(
                  'h4',
                  { className: 'font-semibold text-slate-700' },
                  'Label template',
                ),
                React.createElement(
                  'p',
                  { className: 'text-xs text-slate-500' },
                  activeTemplate?.description
                    ? activeTemplate.description
                    : 'Choose a label to edit or start from a blank canvas.',
                ),
              ),
              React.createElement(
                'div',
                { className: 'flex flex-wrap items-center gap-2' },
                React.createElement(
                  'select',
                  {
                    value: activeTemplateName,
                    onChange: (event) => {
                      const nextName = event.target.value;
                      if (nextName === activeTemplateName) {
                        return;
                      }
                      setActiveTemplateName(nextName);
                    },
                    className:
                      'rounded-md border border-slate-300 bg-white px-3 py-1 text-sm text-slate-700 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100',
                  },
                  LABEL_TEMPLATES.map((template) =>
                    React.createElement(
                      'option',
                      { key: template.name, value: template.name },
                      template.display_name || template.name,
                    ),
                  ),
                ),
                React.createElement(
                  'button',
                  {
                    type: 'button',
                    onClick: handleTemplateReset,
                    className:
                      'rounded-md border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-600 transition hover:border-sky-400 hover:text-sky-600',
                  },
                  'Reset layout',
                ),
              ),
            ),
            templateMetaDetails.length
              ? React.createElement(
                  'div',
                  { className: 'flex flex-wrap items-center gap-3' },
                  ...templateMetaDetails,
                )
              : null,
          ),
          React.createElement(
            'div',
            { className: 'flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white/70 p-4 backdrop-blur' },
            React.createElement(
              'div',
              { className: 'flex flex-wrap items-center gap-3 text-sm text-slate-700' },
              React.createElement('label', { className: 'font-semibold' }, 'Label size'),
              React.createElement(
                'select',
                {
                  value: labelSize.id,
                  onChange: (event) => {
                    const next = LABEL_SIZES.find((size) => size.id === event.target.value) || LABEL_SIZES[0];
                    setLabelSize(next);
                    if (next.id !== 'custom') {
                      setCustomSize({ width: next.width, height: next.height });
                    }
                  },
                  className: 'rounded-md border border-slate-300 bg-white px-3 py-1 text-sm text-slate-700 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                },
                LABEL_SIZES.map((size) => React.createElement('option', { key: size.id, value: size.id }, size.name))
              ),
              labelSize.id === 'custom' &&
                React.createElement(
                  'div',
                  { className: 'flex items-center gap-2 text-xs text-slate-600' },
                  React.createElement('label', { className: 'flex items-center gap-1' },
                    'Width',
                    React.createElement('input', {
                      type: 'number',
                      min: 100,
                      value: round(customSize.width, 0),
                      onChange: (event) => {
                        const value = Math.max(Number(event.target.value) || customSize.width, 50);
                        setCustomSize((prev) => ({ ...prev, width: value }));
                      },
                      className: 'w-20 rounded border border-slate-300 px-2 py-1 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                    })
                  ),
                  React.createElement('label', { className: 'flex items-center gap-1' },
                    'Height',
                    React.createElement('input', {
                      type: 'number',
                      min: 100,
                      value: round(customSize.height, 0),
                      onChange: (event) => {
                        const value = Math.max(Number(event.target.value) || customSize.height, 50);
                        setCustomSize((prev) => ({ ...prev, height: value }));
                      },
                      className: 'w-20 rounded border border-slate-300 px-2 py-1 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                    })
                  )
                )
            ),
            React.createElement(
              'div',
              { className: 'flex items-center gap-2 text-sm text-slate-700' },
              React.createElement('span', null, 'Zoom'),
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => changeZoom(-0.1),
                  className: 'rounded-md border border-slate-300 px-2 py-1 text-xs hover:border-sky-400 hover:text-sky-600'
                },
                '–'
              ),
              React.createElement('span', { className: 'w-12 text-center text-xs font-semibold text-slate-600' }, `${Math.round(zoom * 100)}%`),
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => changeZoom(0.1),
                  className: 'rounded-md border border-slate-300 px-2 py-1 text-xs hover:border-sky-400 hover:text-sky-600'
                },
                '+'
              ),
              React.createElement('span', { className: 'text-xs text-slate-500' }, 'Ctrl + scroll to zoom quicker'),
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => setShowPreview((prev) => !prev),
                  className: `rounded-md border px-3 py-1 text-xs font-semibold transition ${
                    showPreview
                      ? 'border-sky-400 text-sky-600'
                      : 'border-slate-300 text-slate-600 hover:border-sky-400 hover:text-sky-600'
                  }`
                },
                showPreview ? 'Hide preview' : 'Show preview'
              )
            )
          ),
          selectedElements.length > 0 &&
            React.createElement(
              'div',
              {
                className:
                  'rounded-xl border border-dashed border-slate-300 bg-white/60 p-4 text-xs text-slate-600 shadow-sm backdrop-blur'
              },
              React.createElement(
                'div',
                { className: 'flex flex-wrap items-center gap-2' },
                React.createElement(
                  'span',
                  { className: 'font-semibold uppercase tracking-wide text-slate-500' },
                  `Selection (${selectedElements.length})`
                ),
                React.createElement(
                  'button',
                  {
                    type: 'button',
                    onClick: () => toggleLockSelection(),
                    className: `rounded-md border px-2 py-1 font-semibold transition ${
                      selectedElements.some((el) => !el.locked)
                        ? 'border-slate-300 text-slate-600 hover:border-sky-400'
                        : 'border-amber-400 text-amber-600 hover:bg-amber-50'
                    }`
                  },
                  lockToggleLabel
                )
              ),
              React.createElement(
                'div',
                { className: 'mt-3 flex flex-wrap gap-3' },
                React.createElement(
                  'div',
                  { className: 'flex flex-wrap items-center gap-1' },
                  React.createElement('span', { className: 'font-semibold text-slate-500' }, 'Align'),
                  ...arrangeButtons.map((btn) =>
                    React.createElement(
                      'button',
                      {
                        key: btn.key,
                        type: 'button',
                        onClick: btn.action,
                        disabled: !allowAlign,
                        className: `rounded-md border px-2 py-1 text-xs font-semibold transition ${
                          allowAlign
                            ? 'border-slate-300 text-slate-600 hover:border-sky-400 hover:text-sky-600'
                            : 'cursor-not-allowed border-slate-200 text-slate-400'
                        }`
                      },
                      btn.label
                    )
                  )
                ),
                React.createElement(
                  'div',
                  { className: 'flex flex-wrap items-center gap-1' },
                  React.createElement('span', { className: 'font-semibold text-slate-500' }, 'Distribute'),
                  ...distributeButtons.map((btn) =>
                    React.createElement(
                      'button',
                      {
                        key: btn.key,
                        type: 'button',
                        onClick: btn.action,
                        disabled: !allowDistribute,
                        className: `rounded-md border px-2 py-1 text-xs font-semibold transition ${
                          allowDistribute
                            ? 'border-slate-300 text-slate-600 hover:border-sky-400 hover:text-sky-600'
                            : 'cursor-not-allowed border-slate-200 text-slate-400'
                        }`
                      },
                      btn.label
                    )
                  )
                )
              )
            ),
          React.createElement(
            'div',
            { className: 'rounded-xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur' },
            React.createElement(
              'div',
              { className: 'flex items-center justify-between text-sm text-slate-700' },
              React.createElement('h4', { className: 'font-semibold' }, 'Label canvas'),
              React.createElement(
                'span',
                { className: 'text-xs text-slate-500' },
                'Drag, resize, and rotate elements. Hold Ctrl while scrolling to zoom.'
              )
            ),
            React.createElement(
              'div',
              {
                className: 'mt-4 flex justify-center overflow-auto rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6',
                onWheel: handleWheel
              },
              React.createElement(
                'div',
                {
                  ref: canvasRef,
                  className: 'relative rounded-lg bg-white shadow-inner',
                  style: { width: activeLabelSize.width * zoom, height: activeLabelSize.height * zoom },
                  onDrop: handleDrop,
                  onDragOver: handleDragOver,
                  onClick: handleCanvasClick
                },
                React.createElement('div', { className: 'absolute inset-0 rounded-lg border border-slate-200' }),
                ...guideElements,
                ...canvasElements
              )
            )
          ),
          showPreview &&
            React.createElement(
              'div',
              { className: 'rounded-xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur' },
              React.createElement('h4', { className: 'text-sm font-semibold text-slate-700' }, 'Preview'),
              React.createElement(
                'p',
                { className: 'text-xs text-slate-500' },
                'Toggle preview to review the label at print scale.'
              ),
              React.createElement(
                'div',
                { className: 'mt-4 flex justify-center' },
                React.createElement(
                  'div',
                  {
                    className: 'relative rounded-lg border border-slate-200 bg-white shadow-inner',
                    style: {
                      width: activeLabelSize.width * previewScale,
                      height: activeLabelSize.height * previewScale
                    }
                  },
                  ...previewElements
                )
              )
            ),
          React.createElement(
            'div',
            { className: 'rounded-xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur' },
            React.createElement('h4', { className: 'text-sm font-semibold text-slate-700' }, 'Export & import'),
            React.createElement('p', { className: 'text-xs text-slate-500' }, 'Save the current layout or load a saved configuration.'),
            React.createElement(
              'div',
              { className: 'mt-3 flex flex-col gap-3 text-sm' },
              React.createElement(
                'div',
                { className: 'flex flex-wrap items-center gap-3' },
                React.createElement(
                  'button',
                  {
                    type: 'button',
                    onClick: handleTrialPrint,
                    disabled: isPrinting || !canSendTrial,
                    className: `rounded-md px-3 py-2 text-xs font-semibold text-white shadow ${
                      isPrinting || !canSendTrial
                        ? 'cursor-not-allowed bg-amber-400/70'
                        : 'bg-amber-500 hover:bg-amber-600'
                    }`
                  },
                  isPrinting ? 'Sending trial…' : 'Print trial'
                ),
                selectedPrinterName &&
                  React.createElement(
                    'span',
                    { className: 'text-xs text-slate-500' },
                    `Active printer: ${selectedPrinterName}`
                  ),
                !canSendTrial &&
                  React.createElement(
                    'span',
                    { className: 'text-xs font-semibold text-amber-600' },
                    'Select a printer on the settings page to enable trial prints.'
                  ),
                printFeedback &&
                  React.createElement(
                    'span',
                    {
                      className: `text-xs font-semibold ${
                        printFeedback.type === 'success' ? 'text-emerald-600' : 'text-rose-600'
                      }`
                    },
                    printFeedback.message
                  )
              ),
              React.createElement(
                'div',
                { className: 'flex items-center gap-2' },
                React.createElement(
                  'button',
                  {
                    type: 'button',
                    onClick: handleExport,
                    className: 'rounded-md bg-emerald-500 px-3 py-2 text-xs font-semibold text-white shadow hover:bg-emerald-600'
                  },
                  'Export JSON'
                ),
                exportedJSON &&
                  React.createElement(
                    'button',
                    {
                      type: 'button',
                      onClick: () => navigator.clipboard?.writeText(exportedJSON),
                      className: 'rounded-md border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-sky-400'
                    },
                    'Copy'
                  )
              ),
              React.createElement('textarea', {
                value: exportedJSON,
                placeholder: 'Exported layout JSON will appear here…',
                rows: 5,
                readOnly: true,
                className: 'w-full rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-600 focus:outline-none'
              }),
              React.createElement('textarea', {
                value: importValue,
                onChange: (event) => setImportValue(event.target.value),
                placeholder: 'Paste a saved layout JSON to import…',
                rows: 5,
                className: 'w-full rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-700 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
              }),
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: handleImport,
                  className: 'self-start rounded-md bg-sky-500 px-3 py-2 text-xs font-semibold text-white shadow hover:bg-sky-600'
                },
                'Import layout'
              )
            )
          )
        ),
        React.createElement(
          'aside',
          { className: 'w-full max-w-xs rounded-xl border border-slate-200 bg-white/70 p-4 backdrop-blur lg:w-72' },
          customizationContent
        )
      )
    );
  };

  const rootEl = document.getElementById('label-designer-root');
  if (rootEl) {
    const root = ReactDOM.createRoot(rootEl);
    root.render(React.createElement(LabelDesigner));
  }
})();
