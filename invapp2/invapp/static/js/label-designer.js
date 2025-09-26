(() => {
  const { useState, useMemo, useRef, useEffect } = React;

  const uniqueId = () => `element-${Math.random().toString(36).slice(2)}-${Date.now()}`;

  const LABEL_SIZES = [
    { id: '2x1', name: '2" x 1" (Shipping)', width: 600, height: 300 },
    { id: '4x6', name: '4" x 6" (Parcel)', width: 900, height: 1350 },
    { id: '3x2', name: '3" x 2" (Shelf)', width: 720, height: 480 },
    { id: 'custom', name: 'Custom', width: 700, height: 400 }
  ];

  const DEFAULT_FIELDS = [
    {
      id: 'recipientName',
      label: 'Recipient Name',
      fieldKey: 'recipient_name',
      preview: 'Recipient Name',
      description: 'Customer or contact full name.'
    },
    {
      id: 'address',
      label: 'Address',
      fieldKey: 'address',
      preview: '1234 Elm St.\nSpringfield, IL 62704',
      description: 'Destination street and city.',
      defaultHeight: 90
    },
    {
      id: 'orderNumber',
      label: 'Order Number',
      fieldKey: 'order_number',
      preview: 'Order #102938',
      description: 'Order reference identifier.'
    },
    {
      id: 'barcode',
      label: 'Barcode',
      fieldKey: 'barcode',
      preview: '|| BARCODE ||',
      description: 'Auto-generated barcode placeholder.',
      type: 'barcode',
      defaultHeight: 120
    }
  ];

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

  const createElementFromField = (field, point, labelSize) => {
    const width = field.type === 'barcode' ? 280 : 220;
    const height = field.defaultHeight || (field.type === 'barcode' ? 140 : 64);
    return {
      id: uniqueId(),
      type: field.type === 'barcode' ? 'barcode' : 'field',
      fieldKey: field.fieldKey,
      label: field.label,
      text: field.preview,
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
      background: field.type === 'barcode' ? '#0f172a' : 'rgba(255,255,255,0.85)'
    };
  };

  const createTextElement = (labelSize) => {
    const width = 260;
    const height = 72;
    return {
      id: uniqueId(),
      type: 'text',
      text: 'Custom text',
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
      background: 'rgba(255,255,255,0.85)'
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
      x: (labelSize.width - baseWidth) / 2,
      y: (labelSize.height - height) / 2,
      width: baseWidth,
      height,
      rotation: 0
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
    const [selectedId, setSelectedId] = useState(null);
    const [labelSize, setLabelSize] = useState(LABEL_SIZES[0]);
    const [zoom, setZoom] = useState(1);
    const [guides, setGuides] = useState({ vertical: null, horizontal: null });
    const [exportedJSON, setExportedJSON] = useState('');
    const [importValue, setImportValue] = useState('');
    const [customSize, setCustomSize] = useState({ width: LABEL_SIZES[3].width, height: LABEL_SIZES[3].height });
    const canvasRef = useRef(null);
    const fileInputRef = useRef(null);
    const elementsRef = useRef(elements);

    useEffect(() => {
      elementsRef.current = elements;
    }, [elements]);

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

    const selectedElement = useMemo(
      () => elements.find((el) => el.id === selectedId) || null,
      [elements, selectedId]
    );

    const updateElement = (id, updates) => {
      setElements((prev) => prev.map((el) => (el.id === id ? { ...el, ...updates } : el)));
    };

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
      const [kind, key] = data.split(':');
      if (kind !== 'field') return;
      const field = DEFAULT_FIELDS.find((f) => f.id === key);
      if (!field) return;
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const dropPoint = {
        x: (event.clientX - bounds.left) / zoom,
        y: (event.clientY - bounds.top) / zoom
      };
      const element = createElementFromField(field, dropPoint, activeLabelSize);
      setElements((prev) => [...prev, element]);
      setSelectedId(element.id);
    };

    const handleDragOver = (event) => {
      event.preventDefault();
    };

    const startMove = (event, element) => {
      event.preventDefault();
      event.stopPropagation();
      setSelectedId(element.id);
      const bounds = canvasRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const startX = event.clientX;
      const startY = event.clientY;

      const handlePointerMove = (moveEvent) => {
        const currentElements = elementsRef.current;
        const latest = currentElements.find((el) => el.id === element.id) || element;
        const offsetX = (startX - bounds.left) / zoom - latest.x;
        const offsetY = (startY - bounds.top) / zoom - latest.y;
        const rawX = (moveEvent.clientX - bounds.left) / zoom - offsetX;
        const rawY = (moveEvent.clientY - bounds.top) / zoom - offsetY;
        const draft = { x: rawX, y: rawY, width: latest.width, height: latest.height };
        const { vertical, horizontal, snappedX, snappedY } = computeGuides(
          latest.id,
          draft,
          currentElements,
          activeLabelSize
        );
        const finalX = snappedX !== null ? snappedX : draft.x;
        const finalY = snappedY !== null ? snappedY : draft.y;
        const bounded = clampElementWithinBounds({ ...latest, x: finalX, y: finalY }, activeLabelSize);
        setGuides({ vertical, horizontal });
        updateElement(latest.id, { x: bounded.x, y: bounded.y });
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

        const draft = { x: newX, y: newY, width: newWidth, height: newHeight };
        const { vertical, horizontal, snappedX, snappedY } = computeGuides(
          latest.id,
          draft,
          currentElements,
          activeLabelSize
        );
        setGuides({ vertical, horizontal });
        updateElement(latest.id, {
          x: snappedX !== null ? snappedX : draft.x,
          y: snappedY !== null ? snappedY : draft.y,
          width: draft.width,
          height: draft.height
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

    const addTextBlock = () => {
      const element = createTextElement(activeLabelSize);
      setElements((prev) => [...prev, element]);
      setSelectedId(element.id);
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
          setSelectedId(element.id);
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
      event.target.value = '';
    };

    const handleExport = () => {
      const payload = {
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
          return base;
        })
      };
      setExportedJSON(JSON.stringify(payload, null, 2));
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
              background: el.background || 'rgba(255,255,255,0.85)'
            },
            { width, height }
          )
        );
        setElements(sanitized);
        setSelectedId(sanitized[0]?.id || null);
      } catch (error) {
        console.error(error);
        alert('Unable to import layout. Please ensure the JSON is valid.');
      }
    };

    const handleRemoveElement = (id) => {
      setElements((prev) => prev.filter((el) => el.id !== id));
      if (selectedId === id) {
        setSelectedId(null);
      }
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
      setSelectedId(null);
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
        cursor: 'move'
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
          onPointerDown: (e) => startMove(e, element),
          onClick: (e) => {
            e.stopPropagation();
            setSelectedId(element.id);
          }
        },
        isSelected &&
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
    const fieldCards = DEFAULT_FIELDS.map((field) =>
      React.createElement(
        'div',
        {
          key: field.id,
          draggable: true,
          onDragStart: (event) => {
            event.dataTransfer.setData('text/plain', `field:${field.id}`);
            event.dataTransfer.effectAllowed = 'copy';
          },
          className: 'cursor-grab rounded-lg border border-slate-200 bg-white p-3 text-sm shadow-sm transition hover:border-sky-400 hover:shadow'
        },
        React.createElement('div', { className: 'font-medium text-slate-800' }, field.label),
        React.createElement('p', { className: 'mt-1 text-xs text-slate-500' }, field.description)
      )
    );
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
              'button',
              {
                type: 'button',
                onClick: () => handleRemoveElement(selectedElement.id),
                className: 'rounded-md border border-rose-200 px-2 py-1 text-xs font-semibold text-rose-500 hover:bg-rose-50'
              },
              'Remove'
            )
          ),
          selectedElement.fieldKey &&
            React.createElement(
              'div',
              { className: 'rounded-lg bg-slate-100 p-2 text-xs text-slate-600' },
              'Bound field: ',
              React.createElement('span', { className: 'font-semibold text-slate-700' }, selectedElement.label || selectedElement.fieldKey)
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
              React.createElement('span', { className: 'text-xs text-slate-500' }, 'Ctrl + scroll to zoom quicker')
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
            React.createElement(
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
