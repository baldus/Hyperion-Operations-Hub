(() => {
  const { useState, useMemo, useRef, useEffect, useCallback } = React;
  const DESIGNER_CONFIG = window.labelDesignerConfig || {};

  const GRID_SIZE = 8;
  const NUDGE_STEP = 1;
  const FAST_NUDGE_STEP = 8;

  const uniqueId = () => `element-${Math.random().toString(36).slice(2)}-${Date.now()}`;

  const LABEL_SIZES = [
    { id: '2x1', name: '2" x 1" (Shipping)', width: 600, height: 300 },
    { id: '4x6', name: '4" x 6" (Parcel)', width: 900, height: 1350 },
    { id: '3x2', name: '3" x 2" (Shelf)', width: 720, height: 480 },
    { id: 'custom', name: 'Custom', width: 700, height: 400 }
  ];

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

  const clamp = (value, min, max) => {
    if (Number.isNaN(value)) return min;
    return Math.min(Math.max(value, min), max);
  };

  const snapToGrid = (value, size = GRID_SIZE) => {
    if (size <= 0) return value;
    return Math.round(value / size) * size;
  };

  const createElementFromField = (field, point, labelSize) => {
    const width = field.type === 'barcode' ? 280 : 220;
    const height = field.defaultHeight || (field.type === 'barcode' ? 140 : 64);
    const maxX = Math.max(labelSize.width - width, 0);
    const maxY = Math.max(labelSize.height - height, 0);
    const baseX = clamp(point.x - width / 2, 0, maxX);
    const baseY = clamp(point.y - height / 2, 0, maxY);
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
      x: clamp(snapToGrid(baseX), 0, maxX),
      y: clamp(snapToGrid(baseY), 0, maxY),
      width,
      height,
      rotation: 0,
      fontFamily: 'Inter, sans-serif',
      fontSize: field.type === 'barcode' ? 32 : 18,
      fontWeight: '600',
      textAlign: 'left',
      color: '#111827',
      background: field.type === 'barcode' ? '#0f172a' : 'rgba(255,255,255,0.85)',
      isLocked: false
    };
  };

  const createTextElement = (labelSize) => {
    const width = 260;
    const height = 72;
    const maxX = Math.max(labelSize.width - width, 0);
    const maxY = Math.max(labelSize.height - height, 0);
    const baseX = clamp((labelSize.width - width) / 2, 0, maxX);
    const baseY = clamp((labelSize.height - height) / 2, 0, maxY);
    return {
      id: uniqueId(),
      type: 'text',
      text: 'Custom text',
      dataBinding: null,
      fieldKey: null,
      x: clamp(snapToGrid(baseX), 0, maxX),
      y: clamp(snapToGrid(baseY), 0, maxY),
      width,
      height,
      rotation: 0,
      fontFamily: 'Inter, sans-serif',
      fontSize: 20,
      fontWeight: '600',
      textAlign: 'center',
      color: '#0f172a',
      background: 'rgba(255,255,255,0.85)',
      isLocked: false
    };
  };

  const createImageElement = (labelSize, src, naturalSize) => {
    const baseWidth = Math.min(naturalSize?.width || 220, labelSize.width * 0.6);
    const aspectRatio = (naturalSize?.height || 120) / (naturalSize?.width || 220);
    const height = clamp(baseWidth * aspectRatio, MIN_SIZE, labelSize.height * 0.6);
    const maxX = Math.max(labelSize.width - baseWidth, 0);
    const maxY = Math.max(labelSize.height - height, 0);
    return {
      id: uniqueId(),
      type: 'image',
      src,
      dataBinding: null,
      x: clamp(snapToGrid((labelSize.width - baseWidth) / 2), 0, maxX),
      y: clamp(snapToGrid((labelSize.height - height) / 2), 0, maxY),
      width: baseWidth,
      height,
      rotation: 0,
      isLocked: false
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
    const [elements, setElements] = useState([]);
    const [selectedIds, setSelectedIds] = useState([]);
    const [labelSize, setLabelSize] = useState(LABEL_SIZES[0]);
    const [zoom, setZoom] = useState(1);
    const [guides, setGuides] = useState({ vertical: null, horizontal: null });
    const [exportedJSON, setExportedJSON] = useState('');
    const [importValue, setImportValue] = useState('');
    const [customSize, setCustomSize] = useState({ width: LABEL_SIZES[3].width, height: LABEL_SIZES[3].height });
    const [isPrinting, setIsPrinting] = useState(false);
    const [printFeedback, setPrintFeedback] = useState(null);
    const [showPreview, setShowPreview] = useState(false);
    const canvasRef = useRef(null);
    const fileInputRef = useRef(null);
    const elementsRef = useRef(elements);
    const selectionRef = useRef(selectedIds);
    const { trialPrintUrl, selectedPrinterName } = DESIGNER_CONFIG;
    const canSendTrial = Boolean(trialPrintUrl);

    useEffect(() => {
      elementsRef.current = elements;
    }, [elements]);

    useEffect(() => {
      selectionRef.current = selectedIds;
    }, [selectedIds]);

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
      const handleKeyDown = (event) => {
        if (!selectedIds.length) return;
        const target = event.target;
        const tagName = target?.tagName?.toLowerCase();
        if (tagName && ['input', 'textarea', 'select'].includes(tagName)) return;
        if (target?.isContentEditable) return;

        let deltaX = 0;
        let deltaY = 0;
        if (event.key === 'ArrowUp') {
          deltaY = -(event.shiftKey ? FAST_NUDGE_STEP : NUDGE_STEP);
        } else if (event.key === 'ArrowDown') {
          deltaY = event.shiftKey ? FAST_NUDGE_STEP : NUDGE_STEP;
        } else if (event.key === 'ArrowLeft') {
          deltaX = -(event.shiftKey ? FAST_NUDGE_STEP : NUDGE_STEP);
        } else if (event.key === 'ArrowRight') {
          deltaX = event.shiftKey ? FAST_NUDGE_STEP : NUDGE_STEP;
        } else {
          return;
        }

        event.preventDefault();
        const unlockedIds = selectedIds.filter((id) => {
          const el = elementsRef.current.find((item) => item.id === id);
          return el && !el.isLocked;
        });
        if (!unlockedIds.length) return;

        updateElementsByIds(unlockedIds, (el) => {
          const proposed = {
            ...el,
            x: snapToGrid(el.x + deltaX),
            y: snapToGrid(el.y + deltaY)
          };
          const bounded = clampElementWithinBounds(proposed, activeLabelSize);
          return { x: bounded.x, y: bounded.y };
        });
      };

      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }, [selectedIds, activeLabelSize, updateElementsByIds]);

    useEffect(() => {
      setElements((prev) => {
        let changed = false;
        const next = prev.map((el) => {
          if (el.fieldKey && !el.dataBinding) {
            const match = ALL_FIELDS.find((field) => field.fieldKey === el.fieldKey);
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

    const selectedElements = useMemo(
      () => elements.filter((el) => selectedIds.includes(el.id)),
      [elements, selectedIds]
    );

    const primarySelection = selectedElements[0] || null;

    const selectedBinding = useMemo(() => {
      if (!primarySelection) return null;
      if (primarySelection.dataBinding) return primarySelection.dataBinding;
      if (primarySelection.fieldKey) {
        const match = ALL_FIELDS.find((field) => field.fieldKey === primarySelection.fieldKey);
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
    }, [primarySelection]);

    const updateElementsByIds = useCallback((ids, updater) => {
      setElements((prev) =>
        prev.map((el) => {
          if (!ids.includes(el.id)) return el;
          const next = typeof updater === 'function' ? updater(el) : updater;
          return { ...el, ...next };
        })
      );
    }, []);

    const updateElement = useCallback(
      (id, updates) => {
        updateElementsByIds([id], updates);
      },
      [updateElementsByIds]
    );

    const clampElementWithinBounds = (el, size) => {
      const maxX = Math.max(size.width - el.width, 0);
      const maxY = Math.max(size.height - el.height, 0);
      return {
        ...el,
        x: clamp(el.x, 0, maxX),
        y: clamp(el.y, 0, maxY),
        width: clamp(el.width, MIN_SIZE, size.width),
        height: clamp(el.height, MIN_SIZE, size.height)
      };
    };

    const handleDrop = (event) => {
      event.preventDefault();
      const data = event.dataTransfer.getData('text/plain');
      if (!data) return;
      const [kind, groupId, fieldId] = data.split(':');
      if (kind !== 'field') return;
      const field = ALL_FIELDS.find((f) => f.groupId === groupId && f.id === fieldId);
      if (!field) return;
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const dropPoint = {
        x: (event.clientX - bounds.left) / zoom,
        y: (event.clientY - bounds.top) / zoom
      };
      const element = createElementFromField(field, dropPoint, activeLabelSize);
      setElements((prev) => [...prev, element]);
      setSelectedIds([element.id]);
    };

    const handleDragOver = (event) => {
      event.preventDefault();
    };

    const handleElementPointerDown = (event, element) => {
      event.preventDefault();
      event.stopPropagation();
      const isMulti = event.shiftKey || event.metaKey || event.ctrlKey;
      const currentSelection = selectionRef.current;
      let nextSelection;
      if (isMulti) {
        if (currentSelection.includes(element.id)) {
          nextSelection = currentSelection.filter((id) => id !== element.id);
          if (!nextSelection.length) {
            nextSelection = [element.id];
          }
        } else {
          nextSelection = [...currentSelection, element.id];
        }
      } else {
        nextSelection = [element.id];
      }
      setSelectedIds(nextSelection);
      if (!isMulti && !element.isLocked) {
        startMove(event, element, nextSelection);
      }
    };

    const startMove = (event, element, selection) => {
      event.preventDefault();
      event.stopPropagation();
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;

      const pointerStart = {
        x: (event.clientX - bounds.left) / zoom,
        y: (event.clientY - bounds.top) / zoom
      };

      const currentElements = elementsRef.current;
      const movingIds = (selection && selection.length ? selection : [element.id]).filter((id) => {
        const target = currentElements.find((item) => item.id === id);
        return target && !target.isLocked;
      });

      if (!movingIds.includes(element.id)) {
        const latest = currentElements.find((item) => item.id === element.id);
        if (!latest || latest.isLocked) {
          return;
        }
        movingIds.push(element.id);
      }

      if (!movingIds.length) return;

      const initialPositions = movingIds.map((id) => {
        const target = currentElements.find((item) => item.id === id) || element;
        return {
          id,
          x: target.x,
          y: target.y,
          width: target.width,
          height: target.height
        };
      });

      const handlePointerMove = (moveEvent) => {
        const pointerX = (moveEvent.clientX - bounds.left) / zoom;
        const pointerY = (moveEvent.clientY - bounds.top) / zoom;
        const deltaX = pointerX - pointerStart.x;
        const deltaY = pointerY - pointerStart.y;

        const primaryInitial =
          initialPositions.find((pos) => pos.id === element.id) || initialPositions[0];
        if (!primaryInitial) return;

        const draft = {
          x: primaryInitial.x + deltaX,
          y: primaryInitial.y + deltaY,
          width: primaryInitial.width,
          height: primaryInitial.height
        };

        const snappedDraft = {
          ...draft,
          x: snapToGrid(draft.x),
          y: snapToGrid(draft.y)
        };

        const { vertical, horizontal, snappedX, snappedY } = computeGuides(
          element.id,
          snappedDraft,
          elementsRef.current,
          activeLabelSize
        );

        const maxX = Math.max(activeLabelSize.width - draft.width, 0);
        const maxY = Math.max(activeLabelSize.height - draft.height, 0);
        const finalPrimaryX = clamp(
          snappedX !== null ? snappedX : snappedDraft.x,
          0,
          maxX
        );
        const finalPrimaryY = clamp(
          snappedY !== null ? snappedY : snappedDraft.y,
          0,
          maxY
        );

        const appliedDeltaX = finalPrimaryX - primaryInitial.x;
        const appliedDeltaY = finalPrimaryY - primaryInitial.y;

        updateElementsByIds(movingIds, (current) => {
          const original =
            initialPositions.find((pos) => pos.id === current.id) ||
            ({
              x: current.x,
              y: current.y,
              width: current.width,
              height: current.height
            });
          const proposed = {
            ...current,
            x: snapToGrid(original.x + appliedDeltaX),
            y: snapToGrid(original.y + appliedDeltaY)
          };
          const bounded = clampElementWithinBounds(proposed, activeLabelSize);
          return { x: bounded.x, y: bounded.y };
        });

        setGuides({ vertical, horizontal });
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
      event.preventDefault();
      event.stopPropagation();
      if (element.isLocked) return;
      setSelectedIds([element.id]);
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const startX = event.clientX;
      const startY = event.clientY;

      const handlePointerMove = (moveEvent) => {
        const currentElements = elementsRef.current;
        const latest = currentElements.find((el) => el.id === element.id) || element;
        const deltaX = (moveEvent.clientX - startX) / zoom;
        const deltaY = (moveEvent.clientY - startY) / zoom;
        let newWidth = latest.width;
        let newHeight = latest.height;
        let newX = latest.x;
        let newY = latest.y;

        if (direction.includes('e')) {
          newWidth = clamp(latest.width + deltaX, MIN_SIZE, activeLabelSize.width - latest.x);
        }
        if (direction.includes('s')) {
          newHeight = clamp(latest.height + deltaY, MIN_SIZE, activeLabelSize.height - latest.y);
        }
        if (direction.includes('w')) {
          newWidth = clamp(latest.width - deltaX, MIN_SIZE, activeLabelSize.width);
          newX = clamp(latest.x + deltaX, 0, latest.x + latest.width - MIN_SIZE);
        }
        if (direction.includes('n')) {
          newHeight = clamp(latest.height - deltaY, MIN_SIZE, activeLabelSize.height);
          newY = clamp(latest.y + deltaY, 0, latest.y + latest.height - MIN_SIZE);
        }

        const draft = {
          x: newX,
          y: newY,
          width: Math.max(MIN_SIZE, snapToGrid(newWidth)),
          height: Math.max(MIN_SIZE, snapToGrid(newHeight))
        };

        const snappedPosition = {
          ...draft,
          x: snapToGrid(draft.x),
          y: snapToGrid(draft.y)
        };

        const { vertical, horizontal, snappedX, snappedY } = computeGuides(
          element.id,
          snappedPosition,
          elementsRef.current,
          activeLabelSize
        );

        const bounded = clampElementWithinBounds(
          {
            ...latest,
            x: snappedX !== null ? snappedX : snappedPosition.x,
            y: snappedY !== null ? snappedY : snappedPosition.y,
            width: snappedPosition.width,
            height: snappedPosition.height
          },
          activeLabelSize
        );

        setGuides({ vertical, horizontal });
        updateElement(latest.id, {
          x: bounded.x,
          y: bounded.y,
          width: bounded.width,
          height: bounded.height
        });
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

    const getSelectionBounds = useCallback((items) => {
      if (!items.length) return null;
      const left = Math.min(...items.map((item) => item.x));
      const top = Math.min(...items.map((item) => item.y));
      const right = Math.max(...items.map((item) => item.x + item.width));
      const bottom = Math.max(...items.map((item) => item.y + item.height));
      return {
        left,
        top,
        right,
        bottom,
        centerX: left + (right - left) / 2,
        centerY: top + (bottom - top) / 2
      };
    }, []);

    const alignSelection = useCallback(
      (mode) => {
        const selected = elementsRef.current.filter(
          (el) => selectedIds.includes(el.id) && !el.isLocked
        );
        if (selected.length < 2) return;
        const bounds = getSelectionBounds(selected);
        if (!bounds) return;

        updateElementsByIds(
          selected.map((el) => el.id),
          (el) => {
            let nextX = el.x;
            let nextY = el.y;
            if (mode === 'left') nextX = bounds.left;
            if (mode === 'right') nextX = bounds.right - el.width;
            if (mode === 'center') nextX = bounds.centerX - el.width / 2;
            if (mode === 'top') nextY = bounds.top;
            if (mode === 'bottom') nextY = bounds.bottom - el.height;
            if (mode === 'middle') nextY = bounds.centerY - el.height / 2;

            const snapped = {
              ...el,
              x: snapToGrid(nextX),
              y: snapToGrid(nextY)
            };
            const bounded = clampElementWithinBounds(snapped, activeLabelSize);
            return { x: bounded.x, y: bounded.y };
          }
        );
      },
      [selectedIds, getSelectionBounds, updateElementsByIds, activeLabelSize]
    );

    const distributeSelection = useCallback(
      (orientation) => {
        const selected = elementsRef.current
          .filter((el) => selectedIds.includes(el.id) && !el.isLocked)
          .sort((a, b) => (orientation === 'horizontal' ? a.x - b.x : a.y - b.y));

        if (selected.length < 3) return;
        const bounds = getSelectionBounds(selected);
        if (!bounds) return;

        if (orientation === 'horizontal') {
          const totalWidth = selected.reduce((sum, el) => sum + el.width, 0);
          const available = bounds.right - bounds.left - totalWidth;
          if (available < 0) return;
          const gap = available / (selected.length - 1);
          let cursor = bounds.left;
          const positions = {};
          selected.forEach((el, index) => {
            positions[el.id] = cursor;
            cursor += el.width;
            if (index !== selected.length - 1) {
              cursor += gap;
            }
          });
          updateElementsByIds(selected.map((el) => el.id), (el) => {
            const proposed = {
              ...el,
              x: snapToGrid(positions[el.id])
            };
            const bounded = clampElementWithinBounds(proposed, activeLabelSize);
            return { x: bounded.x };
          });
        } else {
          const totalHeight = selected.reduce((sum, el) => sum + el.height, 0);
          const available = bounds.bottom - bounds.top - totalHeight;
          if (available < 0) return;
          const gap = available / (selected.length - 1);
          let cursor = bounds.top;
          const positions = {};
          selected.forEach((el, index) => {
            positions[el.id] = cursor;
            cursor += el.height;
            if (index !== selected.length - 1) {
              cursor += gap;
            }
          });
          updateElementsByIds(selected.map((el) => el.id), (el) => {
            const proposed = {
              ...el,
              y: snapToGrid(positions[el.id])
            };
            const bounded = clampElementWithinBounds(proposed, activeLabelSize);
            return { y: bounded.y };
          });
        }
      },
      [selectedIds, getSelectionBounds, updateElementsByIds, activeLabelSize]
    );

    const addTextBlock = () => {
      const element = createTextElement(activeLabelSize);
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
          const element = createImageElement(activeLabelSize, reader.result, {
            width: img.naturalWidth,
            height: img.naturalHeight
          });
          setElements((prev) => [...prev, element]);
          setSelectedIds([element.id]);
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
      event.target.value = '';
    };

    const buildLayoutPayload = () => ({
      labelSize: {
        width: activeLabelSize.width,
        height: activeLabelSize.height
      },
      elements: elements.map((el) => {
        const base = {
          id: el.id,
          type: el.type,
          x: el.x,
          y: el.y,
          width: el.width,
          height: el.height,
          rotation: el.rotation
        };
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
        }
        if (el.fieldKey) {
          base.fieldKey = el.fieldKey;
          base.label = el.label;
        }
        if (el.dataBinding) {
          base.dataBinding = el.dataBinding;
        }
        return base;
      })
    });

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
        if (!parsed.elements || !Array.isArray(parsed.elements)) {
          throw new Error('Invalid layout format');
        }
        const importedSize = parsed.labelSize || {};
        const width = importedSize.width || activeLabelSize.width;
        const height = importedSize.height || activeLabelSize.height;
        setLabelSize({ id: 'custom', name: 'Custom', width, height });
        setCustomSize({ width, height });
        const sanitized = parsed.elements.map((el) =>
          clampElementWithinBounds(
            {
              ...el,
              fontFamily: el.fontFamily || 'Inter, sans-serif',
              fontSize: el.fontSize || 18,
              fontWeight: el.fontWeight || '600',
              textAlign: el.textAlign || 'left',
              color: el.color || '#111827',
              background: el.background || 'rgba(255,255,255,0.85)',
              isLocked: Boolean(el.isLocked)
            },
            { width, height }
          )
        );
        setElements(sanitized);
        setSelectedIds(sanitized[0] ? [sanitized[0].id] : []);
      } catch (error) {
        console.error(error);
        alert('Unable to import layout. Please ensure the JSON is valid.');
      }
    };

    const handleRemoveElement = (id) => {
      setElements((prev) => prev.filter((el) => el.id !== id));
      setSelectedIds((prev) => prev.filter((selected) => selected !== id));
    };

    const previewScale = useMemo(() => {
      const maxDimension = Math.max(activeLabelSize.width, activeLabelSize.height);
      return 260 / maxDimension;
    }, [activeLabelSize]);

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
      const isSelected = selectedIds.includes(element.id);
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
        cursor: element.isLocked ? 'default' : 'move'
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
          onPointerDown: (e) => handleElementPointerDown(e, element),
          onClick: (e) => {
            e.stopPropagation();
            setSelectedIds([element.id]);
          }
        },
        isSelected && !element.isLocked &&
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
    const fieldCards = DATA_FIELD_GROUPS.map((group) =>
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
    );
    const textAlignmentButtons = primarySelection
      ? ALIGN_OPTIONS.map((option) =>
          React.createElement(
            'button',
            {
              key: option.value,
              type: 'button',
              onClick: () => updateElement(primarySelection.id, { textAlign: option.value }),
              className: `rounded-md border px-2 py-1 text-xs font-semibold transition ${
                primarySelection.textAlign === option.value
                  ? 'border-sky-500 bg-sky-50 text-sky-600'
                  : 'border-slate-300 text-slate-500 hover:border-sky-400'
              }`
            },
            option.label
          )
        )
      : [];

    const unlockedSelectedCount = selectedElements.filter((el) => !el.isLocked).length;
    const canAlignSelection = unlockedSelectedCount >= 2;
    const canDistributeSelection = unlockedSelectedCount >= 3;

    let customizationContent;
    if (selectedElements.length === 0) {
      customizationContent = React.createElement(
        'p',
        { className: 'text-sm text-slate-500' },
        'Select an element on the canvas to customize it.'
      );
    } else if (selectedElements.length > 1) {
      customizationContent = React.createElement(
        'div',
        { className: 'space-y-2 text-sm text-slate-600' },
        React.createElement(
          'p',
          { className: 'font-semibold text-slate-700' },
          `${selectedElements.length} elements selected`
        ),
        React.createElement(
          'p',
          null,
          'Use the alignment and distribution controls above the canvas to tidy this group or click any single element to edit its properties.'
        )
      );
    } else if (primarySelection) {
      customizationContent = React.createElement(
        'div',
        { className: 'space-y-4' },
        React.createElement(
          'div',
          { className: 'flex items-start justify-between gap-3' },
          React.createElement('div', null,
            React.createElement('h4', { className: 'text-sm font-semibold uppercase tracking-wide text-slate-700' }, 'Element settings'),
            React.createElement('p', { className: 'text-xs text-slate-500' }, primarySelection.type === 'image' ? 'Adjust dimensions, position, and rotation.' : 'Update typography, alignment, and copy.')
          ),
          React.createElement(
            'div',
            { className: 'flex items-center gap-2' },
            React.createElement(
              'button',
              {
                type: 'button',
                onClick: () => updateElement(primarySelection.id, { isLocked: !primarySelection.isLocked }),
                className: `rounded-md border px-2 py-1 text-xs font-semibold transition ${
                  primarySelection.isLocked
                    ? 'border-amber-400 text-amber-600 hover:bg-amber-50'
                    : 'border-slate-300 text-slate-500 hover:border-sky-400'
                }`
              },
              primarySelection.isLocked ? 'Unlock' : 'Lock'
            ),
            React.createElement(
              'button',
              {
                type: 'button',
                onClick: () => handleRemoveElement(primarySelection.id),
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
              `${selectedBinding.groupLabel || ''}${selectedBinding.groupLabel ? ' • ' : ''}${primarySelection.label || selectedBinding.label}`
            )
          ),
        primarySelection.type !== 'image' &&
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
                    updateElement(primarySelection.id, {
                      fieldKey: null,
                      label: primarySelection.type === 'barcode' ? primarySelection.label : null,
                      dataBinding: null
                    });
                    return;
                  }
                  const [groupId, fieldId] = value.split(':');
                  const field = ALL_FIELDS.find((f) => f.groupId === groupId && f.id === fieldId);
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
                  if (primarySelection.type !== 'barcode') {
                    updates.text = primarySelection.text || field.preview || field.label;
                  }
                  updateElement(primarySelection.id, updates);
                },
                className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
              },
              React.createElement('option', { value: '__none__' }, 'No binding'),
              ...ALL_FIELDS.map((field) =>
                React.createElement(
                  'option',
                  {
                    key: `${field.groupId}:${field.id}`,
                    value: `${field.groupId}:${field.id}`
                  },
                  `${field.groupLabel} • ${field.label}`
                )
              )
            )
          ),
        primarySelection.type !== 'image' &&
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
                  value: primarySelection.fontFamily,
                  onChange: (event) => updateElement(primarySelection.id, { fontFamily: event.target.value }),
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
                  value: primarySelection.fontWeight,
                  onChange: (event) => updateElement(primarySelection.id, { fontWeight: event.target.value }),
                  className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
                },
                FONT_WEIGHTS.map((option) =>
                  React.createElement('option', { key: option.value, value: option.value }, option.label)
                )
              )
            )
          ),
        primarySelection.type !== 'image' &&
          React.createElement(
            'div',
            { className: 'flex flex-wrap gap-2 text-xs font-semibold text-slate-600' },
            React.createElement('span', { className: 'self-center' }, 'Align'),
            ...textAlignmentButtons
          ),
        primarySelection.type !== 'image' &&
          React.createElement(
            'label',
            { className: 'block space-y-1 text-xs font-semibold text-slate-600' },
            React.createElement('span', null, 'Font size (pt)'),
            React.createElement('input', {
              type: 'number',
              min: 6,
              max: 120,
              value: round(primarySelection.fontSize, 0),
              onChange: (event) => {
                const value = clamp(Number(event.target.value), 6, 120);
                updateElement(primarySelection.id, { fontSize: value });
              },
              className: 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100'
            })
          ),
        primarySelection.type !== 'image' &&
          React.createElement(
            'label',
            { className: 'flex items-center justify-between gap-2 text-xs font-semibold text-slate-600' },
            React.createElement('span', null, 'Color'),
            React.createElement('input', {
              type: 'color',
              value: primarySelection.color || '#111827',
              onChange: (event) => updateElement(primarySelection.id, { color: event.target.value })
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
            value: primarySelection.rotation,
            onChange: (event) => updateElement(primarySelection.id, { rotation: Number(event.target.value) }),
            className: 'w-full accent-sky-500'
          }),
          React.createElement('div', { className: 'text-right text-xs text-slate-500' }, `${primarySelection.rotation}\u00B0`)
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
                value: round(primarySelection[key], 0),
                onChange: (event) => {
                  const value = Number(event.target.value);
                  if (Number.isNaN(value)) return;
                  const bounded = clampElementWithinBounds(
                    { ...primarySelection, [key]: value },
                    activeLabelSize
                  );
                  updateElement(primarySelection.id, {
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
      );
    }
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
          (canAlignSelection || canDistributeSelection)
            ? React.createElement(
                'div',
                { className: 'flex flex-wrap items-center gap-2 text-xs text-slate-600' },
                React.createElement('span', { className: 'font-semibold uppercase text-slate-700' }, 'Arrange'),
                canAlignSelection &&
                  React.createElement(
                    React.Fragment,
                    null,
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => alignSelection('left'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Align left'
                    ),
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => alignSelection('center'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Align center'
                    ),
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => alignSelection('right'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Align right'
                    ),
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => alignSelection('top'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Align top'
                    ),
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => alignSelection('middle'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Align middle'
                    ),
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => alignSelection('bottom'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Align bottom'
                    )
                  ),
                canDistributeSelection &&
                  React.createElement(
                    React.Fragment,
                    null,
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => distributeSelection('horizontal'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Distribute horizontal'
                    ),
                    React.createElement(
                      'button',
                      {
                        type: 'button',
                        onClick: () => distributeSelection('vertical'),
                        className: 'rounded-md border border-slate-300 px-2 py-1 font-semibold hover:border-sky-400'
                      },
                      'Distribute vertical'
                    )
                  )
              )
            : null,
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
                className: 'rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-600 hover:border-sky-400'
              },
              showPreview ? 'Hide preview' : 'Show preview'
            )
          )
        ),
          React.createElement(
            'div',
            { className: 'grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]' },
            React.createElement(
              'div',
              { className: 'rounded-xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur' },
              React.createElement(
                'div',
                { className: 'flex items-center justify-between text-sm text-slate-700' },
                React.createElement('h4', { className: 'font-semibold' }, 'Label canvas'),
                React.createElement('span', { className: 'text-xs text-slate-500' }, 'Drag, resize, and rotate elements. Hold Ctrl while scrolling to zoom.')
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
                    style: {
                      width: activeLabelSize.width * zoom,
                      height: activeLabelSize.height * zoom,
                      backgroundSize: `${GRID_SIZE * zoom}px ${GRID_SIZE * zoom}px`,
                      backgroundImage:
                        'linear-gradient(to right, rgba(226,232,240,0.6) 1px, transparent 1px), ' +
                        'linear-gradient(to bottom, rgba(226,232,240,0.6) 1px, transparent 1px)'
                    },
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
          showPreview
            ? React.createElement(
                'div',
                { className: 'rounded-xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur' },
                React.createElement('h4', { className: 'text-sm font-semibold text-slate-700' }, 'Preview'),
                React.createElement('p', { className: 'text-xs text-slate-500' }, 'Scaled preview for quick checks.'),
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
              )
            : null,
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
