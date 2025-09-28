/* global React, ReactDOM, window */

const { useState, useMemo, useEffect, useRef, useCallback } = React;
const h = React.createElement;

const GRID_SIZE = 5;
const SNAP_TOLERANCE = 6;
const ROTATION_SNAP = 5;

const PLACEHOLDER_LABELS = [
  {
    id: 'receiving-label',
    name: 'Receiving Label',
    description: 'Standard receiving label with SKU, description, and lot data.',
    size: { width: 400, height: 250 },
    dataFields: [
      { key: 'sku', label: 'SKU' },
      { key: 'description', label: 'Description' },
      { key: 'lot', label: 'Lot Number' },
      { key: 'received_at', label: 'Received Date' },
      { key: 'quantity', label: 'Quantity' }
    ],
    sampleData: {
      sku: 'SKU-12345',
      description: 'Widget Prime - Stainless',
      lot: 'LOT-00977',
      received_at: '2024-05-21',
      quantity: '12'
    },
    fields: [
      {
        id: 'field-1',
        label: 'SKU',
        bindingKey: 'sku',
        type: 'text',
        x: 24,
        y: 24,
        width: 220,
        height: 42,
        rotation: 0,
        fontSize: 22,
        align: 'left'
      },
      {
        id: 'field-2',
        label: 'Description',
        bindingKey: 'description',
        type: 'text',
        x: 24,
        y: 78,
        width: 320,
        height: 56,
        rotation: 0,
        fontSize: 18,
        align: 'left'
      },
      {
        id: 'field-3',
        label: 'Lot Number',
        bindingKey: 'lot',
        type: 'text',
        x: 24,
        y: 146,
        width: 200,
        height: 36,
        rotation: 0,
        fontSize: 16,
        align: 'left'
      },
      {
        id: 'field-4',
        label: 'Quantity',
        bindingKey: 'quantity',
        type: 'text',
        x: 248,
        y: 146,
        width: 120,
        height: 36,
        rotation: 0,
        fontSize: 18,
        align: 'right'
      }
    ]
  },
  {
    id: 'order-label',
    name: 'Order Completion Label',
    description: 'Label used when orders are fulfilled with destination information.',
    size: { width: 420, height: 300 },
    dataFields: [
      { key: 'order_number', label: 'Order Number' },
      { key: 'customer_name', label: 'Customer Name' },
      { key: 'address_line', label: 'Address' },
      { key: 'city_state', label: 'City / State' },
      { key: 'due_date', label: 'Due Date' }
    ],
    sampleData: {
      order_number: 'PO-8821',
      customer_name: 'Hyperion Labs',
      address_line: '991 Market Street',
      city_state: 'San Francisco, CA',
      due_date: 'Due 06/01'
    },
    fields: [
      {
        id: 'field-5',
        label: 'Order Number',
        bindingKey: 'order_number',
        type: 'text',
        x: 32,
        y: 32,
        width: 280,
        height: 48,
        rotation: 0,
        fontSize: 24,
        align: 'left'
      },
      {
        id: 'field-6',
        label: 'Customer Name',
        bindingKey: 'customer_name',
        type: 'text',
        x: 32,
        y: 92,
        width: 320,
        height: 44,
        rotation: 0,
        fontSize: 20,
        align: 'left'
      },
      {
        id: 'field-7',
        label: 'Address',
        bindingKey: 'address_line',
        type: 'text',
        x: 32,
        y: 148,
        width: 320,
        height: 40,
        rotation: 0,
        fontSize: 16,
        align: 'left'
      },
      {
        id: 'field-8',
        label: 'City / State',
        bindingKey: 'city_state',
        type: 'text',
        x: 32,
        y: 196,
        width: 320,
        height: 36,
        rotation: 0,
        fontSize: 16,
        align: 'left'
      },
      {
        id: 'field-9',
        label: 'Due Date',
        bindingKey: 'due_date',
        type: 'text',
        x: 32,
        y: 244,
        width: 160,
        height: 36,
        rotation: 0,
        fontSize: 18,
        align: 'left'
      }
    ]
  },
  {
    id: 'inventory-label',
    name: 'Inventory Bin Label',
    description: 'High-contrast bin location label with optional rotation.',
    size: { width: 320, height: 160 },
    dataFields: [
      { key: 'bin_code', label: 'Bin Code' },
      { key: 'zone', label: 'Zone' },
      { key: 'category', label: 'Category' }
    ],
    sampleData: {
      bin_code: 'A-15-04',
      zone: 'Zone A',
      category: 'Fast Movers'
    },
    fields: [
      {
        id: 'field-10',
        label: 'Bin Code',
        bindingKey: 'bin_code',
        type: 'text',
        x: 20,
        y: 20,
        width: 280,
        height: 60,
        rotation: 0,
        fontSize: 28,
        align: 'center'
      },
      {
        id: 'field-11',
        label: 'Zone',
        bindingKey: 'zone',
        type: 'text',
        x: 20,
        y: 92,
        width: 140,
        height: 44,
        rotation: 0,
        fontSize: 20,
        align: 'left'
      },
      {
        id: 'field-12',
        label: 'Category',
        bindingKey: 'category',
        type: 'text',
        x: 160,
        y: 92,
        width: 140,
        height: 44,
        rotation: 0,
        fontSize: 18,
        align: 'right'
      }
    ]
  }
];

let fieldIdCounter = 1000;

function nextFieldId() {
  fieldIdCounter += 1;
  return `field-${fieldIdCounter}`;
}

function cloneLabel(label) {
  return {
    ...label,
    size: { ...label.size },
    dataFields: label.dataFields.map((field) => ({ ...field })),
    sampleData: { ...label.sampleData },
    fields: label.fields.map((field) => ({ ...field }))
  };
}

function toSerializableLayout(label) {
  if (!label) {
    return null;
  }
  return {
    id: label.id,
    name: label.name,
    description: label.description,
    size: { ...label.size },
    dataFields: label.dataFields.map((field) => ({ ...field })),
    fields: label.fields.map((field) => ({
      id: field.id,
      label: field.label,
      bindingKey: field.bindingKey,
      type: field.type,
      x: Math.round(field.x),
      y: Math.round(field.y),
      width: Math.round(field.width),
      height: Math.round(field.height),
      rotation: Math.round(field.rotation || 0),
      fontSize: field.fontSize,
      align: field.align
    }))
  };
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json'
    },
    credentials: 'same-origin',
    body: JSON.stringify(payload)
  });

  const raw = await response.text();
  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch (error) {
      data = raw;
    }
  }

  if (!response.ok) {
    const message =
      data && typeof data === 'object' && data !== null && 'message' in data
        ? data.message
        : typeof data === 'string' && data
        ? data
        : 'Request failed.';
    throw new Error(message);
  }

  return data;
}

function roundToGrid(value) {
  return Math.round(value / GRID_SIZE) * GRID_SIZE;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function formatZoom(zoom) {
  return `${Math.round(zoom * 100)}%`;
}

function getFieldDisplayValue(field, label) {
  if (field.bindingKey) {
    const sample = label.sampleData?.[field.bindingKey];
    if (sample) {
      return sample;
    }
    const bindingLabel = label.dataFields.find((item) => item.key === field.bindingKey)?.label;
    return bindingLabel ? `{{ ${bindingLabel} }}` : `{{ ${field.bindingKey} }}`;
  }
  return field.label || 'Text';
}

const SectionHeading = ({ title, subtitle }) =>
  h(
    'div',
    { className: 'mb-4' },
    h('h3', { className: 'text-lg font-semibold text-slate-900' }, title),
    subtitle ? h('p', { className: 'text-sm text-slate-500' }, subtitle) : null
  );

const FeedbackMessage = ({ feedback }) => {
  if (!feedback) {
    return null;
  }
  const isError = feedback.type === 'error';
  const style = isError
    ? 'border-red-200 bg-red-50 text-red-700'
    : 'border-emerald-200 bg-emerald-50 text-emerald-700';
  return h(
    'div',
    { className: `rounded-md border px-3 py-2 text-sm font-medium ${style}` },
    feedback.message
  );
};

const ActionPanel = ({
  label,
  saveUrl,
  trialPrintUrl,
  printerName,
  onSave,
  onTrialPrint,
  saving,
  printing,
  feedback
}) => {
  const hasLabel = Boolean(label);
  const canSave = Boolean(hasLabel && saveUrl);
  const canTrialPrint = Boolean(hasLabel && trialPrintUrl && printerName);
  const infoText = !hasLabel
    ? 'Select a label to enable saving or printing actions.'
    : printerName
    ? `Trial prints will send to ${printerName}.`
    : 'Select an active printer to enable trial prints.';

  return h(
    'div',
    { className: 'space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm' },
    SectionHeading({ title: 'Label Actions', subtitle: 'Save changes or send a trial print.' }),
    h('p', { className: 'text-sm text-slate-600' }, infoText),
    h(
      'div',
      { className: 'grid gap-3 sm:grid-cols-2' },
      h(
        'button',
        {
          type: 'button',
          onClick: canSave ? onSave : undefined,
          disabled: !canSave || saving,
          className:
            'inline-flex items-center justify-center rounded-md border border-indigo-200 bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-200 disabled:text-slate-500'
        },
        saving ? 'Saving…' : 'Save Label'
      ),
      h(
        'button',
        {
          type: 'button',
          onClick: canTrialPrint ? onTrialPrint : undefined,
          disabled: !canTrialPrint || printing,
          className:
            'inline-flex items-center justify-center rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-indigo-300 hover:text-indigo-700 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400'
        },
        printing ? 'Printing…' : 'Trial Print'
      )
    ),
    !saveUrl
      ? h(
          'p',
          { className: 'text-xs font-medium text-amber-700' },
          'Saving is currently disabled because no save endpoint is configured.'
        )
      : null,
    hasLabel && !printerName
      ? h(
          'p',
          { className: 'text-xs font-medium text-amber-700' },
          'Choose an active printer from printer settings to enable trial prints.'
        )
      : null,
    !trialPrintUrl
      ? h(
          'p',
          { className: 'text-xs font-medium text-amber-700' },
          'Trial printing is unavailable because no endpoint is configured.'
        )
      : null,
    FeedbackMessage({ feedback })
  );
};

const LabelList = ({ labels, selectedId, onSelect }) =>
  h(
    'div',
    { className: 'space-y-3' },
    SectionHeading({
      title: 'Label Templates',
      subtitle: 'Select a label to begin editing.'
    }),
    h(
      'div',
      { className: 'space-y-2' },
      ...labels.map((label) =>
        h(
          'button',
          {
            key: label.id,
            onClick: () => onSelect(label.id),
            className: `w-full rounded-md border px-4 py-3 text-left transition focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
              label.id === selectedId
                ? 'border-indigo-500 bg-indigo-50 text-indigo-900 shadow'
                : 'border-slate-200 hover:border-indigo-300 hover:bg-slate-50'
            }`
          },
          h('div', { className: 'font-medium' }, label.name),
          h('p', { className: 'text-sm text-slate-500' }, label.description)
        )
      )
    )
  );

const FieldToolbox = ({ label, onAddField }) => {
  if (!label) {
    return h(
      'div',
      { className: 'rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500' },
      'Select a label to add new fields.'
    );
  }

  const addDataField = (binding) => {
    const offset = label.fields.length * 12;
    const width = Math.max(Math.min(220, label.size.width - 40), 60);
    onAddField({
      id: nextFieldId(),
      label: binding.label,
      bindingKey: binding.key,
      type: 'text',
      x: 20 + (offset % 40),
      y: 20 + (offset % 80),
      width,
      height: 40,
      rotation: 0,
      fontSize: 18,
      align: 'left'
    });
  };

  const addStaticText = () => {
    const offset = label.fields.length * 12;
    const width = Math.max(Math.min(200, label.size.width - 40), 60);
    onAddField({
      id: nextFieldId(),
      label: 'Static Text',
      bindingKey: null,
      type: 'text',
      x: 20 + (offset % 40),
      y: 20 + (offset % 80),
      width,
      height: 40,
      rotation: 0,
      fontSize: 16,
      align: 'left'
    });
  };

  return h(
    'div',
    { className: 'space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm' },
    SectionHeading({
      title: 'Field Toolbox',
      subtitle: 'Add dynamic or static fields to this label.'
    }),
    h(
      'button',
      {
        type: 'button',
        onClick: addStaticText,
        className:
          'w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-700'
      },
      'Add Static Text'
    ),
    h(
      'div',
      { className: 'space-y-2' },
      h('p', { className: 'text-xs font-semibold uppercase tracking-wide text-slate-500' }, 'Data Fields'),
      ...label.dataFields.map((binding) =>
        h(
          'button',
          {
            key: binding.key,
            type: 'button',
            onClick: () => addDataField(binding),
            className:
              'flex w-full items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-700 transition hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-700'
          },
          h('span', { className: 'font-medium' }, binding.label),
          h('span', { className: 'text-xs text-slate-400' }, binding.key)
        )
      )
    )
  );
};

const PropertyInspector = ({ label, selectedField, onFieldChange, onFieldDelete }) => {
  if (!label) {
    return null;
  }

  if (!selectedField) {
    return h(
      'div',
      { className: 'rounded-lg border border-slate-200 bg-white p-4 shadow-sm text-sm text-slate-500' },
      'Select a field in the preview to configure its properties.'
    );
  }

  const handleInputChange = (key, value) => {
    onFieldChange({ [key]: value });
  };

  const handleNumberChange = (key, value) => {
    if (value === '' || Number.isNaN(Number(value))) {
      return;
    }
    onFieldChange({ [key]: Number(value) });
  };

  return h(
    'div',
    { className: 'space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm' },
    SectionHeading({
      title: 'Property Inspector',
      subtitle: 'Adjust content, binding, and layout.'
    }),
    h(
      'div',
      { className: 'space-y-3 text-sm' },
      h(
        'label',
        { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
        'Label',
        h('input', {
          type: 'text',
          value: selectedField.label,
          className:
            'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
          onChange: (event) => handleInputChange('label', event.target.value)
        })
      ),
      h(
        'label',
        { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
        'Binding',
        h(
          'select',
          {
            value: selectedField.bindingKey || '',
            className:
              'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
            onChange: (event) =>
              handleInputChange('bindingKey', event.target.value || null)
          },
          h('option', { value: '' }, 'None (static)'),
          ...label.dataFields.map((binding) =>
            h('option', { key: binding.key, value: binding.key }, binding.label)
          )
        )
      ),
      h(
        'div',
        { className: 'grid grid-cols-2 gap-3' },
        h(
          'label',
          { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
          'X',
          h('input', {
            type: 'number',
            value: Math.round(selectedField.x),
            className:
              'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
            onChange: (event) => handleNumberChange('x', event.target.value)
          })
        ),
        h(
          'label',
          { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
          'Y',
          h('input', {
            type: 'number',
            value: Math.round(selectedField.y),
            className:
              'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
            onChange: (event) => handleNumberChange('y', event.target.value)
          })
        ),
        h(
          'label',
          { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
          'Width',
          h('input', {
            type: 'number',
            value: Math.round(selectedField.width),
            className:
              'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
            onChange: (event) => handleNumberChange('width', event.target.value)
          })
        ),
        h(
          'label',
          { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
          'Height',
          h('input', {
            type: 'number',
            value: Math.round(selectedField.height),
            className:
              'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
            onChange: (event) => handleNumberChange('height', event.target.value)
          })
        )
      ),
      h(
        'label',
        { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
        'Rotation',
        h('input', {
          type: 'number',
          value: Math.round(selectedField.rotation),
          className:
            'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
          onChange: (event) => handleNumberChange('rotation', event.target.value)
        })
      ),
      h(
        'label',
        { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
        'Font Size',
        h('input', {
          type: 'number',
          value: Math.round(selectedField.fontSize || 16),
          className:
            'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
          onChange: (event) => handleNumberChange('fontSize', event.target.value)
        })
      ),
      h(
        'label',
        { className: 'block text-xs font-semibold uppercase tracking-wide text-slate-500' },
        'Alignment',
        h(
          'select',
          {
            value: selectedField.align || 'left',
            className:
              'mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500',
            onChange: (event) => handleInputChange('align', event.target.value)
          },
          h('option', { value: 'left' }, 'Left'),
          h('option', { value: 'center' }, 'Center'),
          h('option', { value: 'right' }, 'Right')
        )
      )
    ),
    h(
      'button',
      {
        type: 'button',
        onClick: onFieldDelete,
        className:
          'w-full rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-600 transition hover:border-red-300 hover:bg-red-100'
      },
      'Remove Field'
    )
  );
};

const FieldElement = ({ field, label, zoom, isSelected, onPointerAction }) => {
  const style = {
    position: 'absolute',
    left: `${field.x}px`,
    top: `${field.y}px`,
    width: `${field.width}px`,
    height: `${field.height}px`,
    transform: `rotate(${field.rotation}deg)`,
    transformOrigin: 'center center'
  };

  const alignmentClass =
    field.align === 'center'
      ? 'text-center'
      : field.align === 'right'
      ? 'text-right'
      : 'text-left';

  const contentStyle = {
    fontSize: `${field.fontSize || 16}px`
  };

  const handlePointerDown = (event, mode) => {
    event.stopPropagation();
    event.preventDefault();
    onPointerAction({ event, mode, fieldId: field.id });
  };

  const handleCanvasPointerDown = (event) => handlePointerDown(event, 'move');

  const handleHandlePointerDown = (event, mode) => handlePointerDown(event, mode);

  return h(
    'div',
    {
      key: field.id,
      className: `group origin-top-left select-none rounded border ${
        isSelected ? 'border-indigo-500 shadow-lg' : 'border-slate-300'
      } bg-white/70 text-slate-900 transition`,
      style,
      onPointerDown: handleCanvasPointerDown
    },
    h(
      'div',
      {
        className: `flex h-full w-full items-center justify-center px-2 ${alignmentClass}`,
        style: contentStyle
      },
      getFieldDisplayValue(field, label)
    ),
    isSelected
      ? h(
          React.Fragment,
          null,
          ...['nw', 'ne', 'se', 'sw'].map((corner) =>
            h('span', {
              key: corner,
              onPointerDown: (event) => handleHandlePointerDown(event, `resize-${corner}`),
              className:
                'absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border border-indigo-500 bg-white shadow',
              style: getHandleStyle(corner)
            })
          ),
          h('span', {
            onPointerDown: (event) => handleHandlePointerDown(event, 'rotate'),
            className:
              'absolute left-1/2 top-0 h-3 w-3 -translate-x-1/2 -translate-y-full rounded-full border border-indigo-500 bg-indigo-100 shadow',
            style: { cursor: 'grab' }
          })
        )
      : null
  );
};

function getHandleStyle(corner) {
  const base = {
    cursor: 'nwse-resize'
  };
  if (corner === 'nw') {
    return { ...base, left: 0, top: 0 };
  }
  if (corner === 'ne') {
    return { ...base, left: '100%', top: 0, cursor: 'nesw-resize' };
  }
  if (corner === 'se') {
    return { ...base, left: '100%', top: '100%' };
  }
  if (corner === 'sw') {
    return { ...base, left: 0, top: '100%', cursor: 'nesw-resize' };
  }
  return base;
}

const LabelPreview = ({
  label,
  selectedFieldId,
  onSelectField,
  onUpdateField,
  onCanvasClick
}) => {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const [zoom, setZoom] = useState(1);
  const dragStateRef = useRef(null);
  const [guides, setGuides] = useState({ vertical: null, horizontal: null });

  useEffect(() => {
    if (!label) {
      return;
    }
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const availableWidth = container.clientWidth - 32;
    if (!availableWidth) {
      return;
    }
    const fit = Math.min(availableWidth / label.size.width, 1.2);
    if (Number.isFinite(fit) && fit > 0) {
      setZoom(Number(fit.toFixed(2)));
    }
  }, [label]);

  useEffect(() => {
    const handlePointerMove = (event) => {
      const dragState = dragStateRef.current;
      if (!dragState || !label) {
        return;
      }
      if (event.pointerId !== dragState.pointerId) {
        return;
      }
      const rect = canvasRef.current.getBoundingClientRect();
      const pointerX = (event.clientX - rect.left) / zoom;
      const pointerY = (event.clientY - rect.top) / zoom;
      const { mode, startField, startPointer } = dragState;
      let updates = {};
      let nextGuides = { vertical: null, horizontal: null };

      if (mode === 'move') {
        const deltaX = pointerX - startPointer.x;
        const deltaY = pointerY - startPointer.y;
        let nextX = roundToGrid(startField.x + deltaX);
        let nextY = roundToGrid(startField.y + deltaY);

        if (Math.abs(nextX) < SNAP_TOLERANCE) {
          nextX = 0;
          nextGuides.vertical = 0;
        }
        const rightEdge = label.size.width - startField.width;
        if (Math.abs(nextX - rightEdge) < SNAP_TOLERANCE) {
          nextX = rightEdge;
          nextGuides.vertical = label.size.width;
        }
        const centerX = label.size.width / 2 - startField.width / 2;
        if (Math.abs(nextX - centerX) < SNAP_TOLERANCE) {
          nextX = centerX;
          nextGuides.vertical = label.size.width / 2;
        }

        if (Math.abs(nextY) < SNAP_TOLERANCE) {
          nextY = 0;
          nextGuides.horizontal = 0;
        }
        const bottomEdge = label.size.height - startField.height;
        if (Math.abs(nextY - bottomEdge) < SNAP_TOLERANCE) {
          nextY = bottomEdge;
          nextGuides.horizontal = label.size.height;
        }
        const centerY = label.size.height / 2 - startField.height / 2;
        if (Math.abs(nextY - centerY) < SNAP_TOLERANCE) {
          nextY = centerY;
          nextGuides.horizontal = label.size.height / 2;
        }

        updates = {
          x: clamp(nextX, 0, label.size.width - startField.width),
          y: clamp(nextY, 0, label.size.height - startField.height)
        };
      } else if (mode.startsWith('resize')) {
        const deltaX = pointerX - startPointer.x;
        const deltaY = pointerY - startPointer.y;
        let { x, y, width, height } = startField;

        if (mode.includes('e')) {
          width = clamp(roundToGrid(startField.width + deltaX), 30, label.size.width - x);
        }
        if (mode.includes('s')) {
          height = clamp(roundToGrid(startField.height + deltaY), 24, label.size.height - y);
        }
        if (mode.includes('w')) {
          const nextWidth = clamp(roundToGrid(startField.width - deltaX), 30, label.size.width);
          const widthDelta = nextWidth - startField.width;
          width = nextWidth;
          x = clamp(roundToGrid(startField.x - widthDelta), 0, startField.x + startField.width - 30);
        }
        if (mode.includes('n')) {
          const nextHeight = clamp(roundToGrid(startField.height - deltaY), 24, label.size.height);
          const heightDelta = nextHeight - startField.height;
          height = nextHeight;
          y = clamp(roundToGrid(startField.y - heightDelta), 0, startField.y + startField.height - 24);
        }

        updates = { x, y, width, height };
      } else if (mode === 'rotate') {
        const centerX = startField.x + startField.width / 2;
        const centerY = startField.y + startField.height / 2;
        const currentAngle = Math.atan2(pointerY - centerY, pointerX - centerX);
        const rotationDelta = (currentAngle - dragState.startAngle) * (180 / Math.PI);
        let nextRotation = startField.rotation + rotationDelta;
        nextRotation = Math.round(nextRotation / ROTATION_SNAP) * ROTATION_SNAP;
        updates = { rotation: nextRotation };
      }

      if (updates && Object.keys(updates).length > 0) {
        onUpdateField(dragState.fieldId, updates, { transient: true });
      }
      setGuides(nextGuides);
    };

    const handlePointerUp = (event) => {
      const dragState = dragStateRef.current;
      if (!dragState || event.pointerId !== dragState.pointerId) {
        return;
      }
      dragStateRef.current = null;
      setGuides({ vertical: null, horizontal: null });
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [label, onUpdateField, zoom]);

  const handlePointerAction = useCallback(
    ({ event, mode, fieldId }) => {
      if (!label) {
        return;
      }
      const field = label.fields.find((item) => item.id === fieldId);
      if (!field) {
        return;
      }

      const rect = canvasRef.current.getBoundingClientRect();
      const pointerX = (event.clientX - rect.left) / zoom;
      const pointerY = (event.clientY - rect.top) / zoom;
      dragStateRef.current = {
        pointerId: event.pointerId,
        fieldId,
        mode,
        startField: { ...field },
        startPointer: { x: pointerX, y: pointerY },
        startAngle:
          mode === 'rotate'
            ? Math.atan2(pointerY - (field.y + field.height / 2), pointerX - (field.x + field.width / 2))
            : null
      };
      setGuides({ vertical: null, horizontal: null });
      onSelectField(fieldId);
    },
    [label, onSelectField, zoom]
  );

  const handleCanvasPointerDown = (event) => {
    if (event.target === canvasRef.current) {
      onCanvasClick();
    }
  };

  if (!label) {
    return h(
      'div',
      { className: 'flex h-full items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50' },
      h('p', { className: 'text-sm text-slate-500' }, 'Select a label from the list to start designing.')
    );
  }

  return h(
    'div',
    { className: 'flex h-full flex-col space-y-4' },
    SectionHeading({
      title: label.name,
      subtitle: `${label.size.width} × ${label.size.height} px`
    }),
    h(
      'div',
      { className: 'flex items-center justify-between text-sm text-slate-500' },
      h('span', null, 'Zoom'),
      h(
        'div',
        { className: 'flex items-center gap-2' },
        h('input', {
          type: 'range',
          min: 0.5,
          max: 2,
          step: 0.05,
          value: zoom,
          onChange: (event) => setZoom(Number(event.target.value))
        }),
        h('span', { className: 'w-12 text-right font-medium text-slate-600' }, formatZoom(zoom))
      )
    ),
    h(
      'div',
      {
        ref: containerRef,
        className: 'relative flex min-h-[400px] flex-1 items-center justify-center overflow-auto rounded-lg border border-slate-200 bg-slate-100 p-4'
      },
      h(
        'div',
        {
          ref: canvasRef,
          onPointerDown: handleCanvasPointerDown,
          className: 'relative bg-white shadow-inner',
          style: {
            width: `${label.size.width}px`,
            height: `${label.size.height}px`,
            transform: `scale(${zoom})`,
            transformOrigin: 'top left'
          }
        },
        guides.vertical !== null
          ? h('div', {
              className: 'absolute top-0 h-full w-px bg-indigo-400/70',
              style: { left: `${guides.vertical}px` }
            })
          : null,
        guides.horizontal !== null
          ? h('div', {
              className: 'absolute left-0 w-full border-t border-indigo-400/70',
              style: { top: `${guides.horizontal}px` }
            })
          : null,
        ...label.fields.map((field) =>
          FieldElement({
            key: field.id,
            field,
            label,
            zoom,
            isSelected: field.id === selectedFieldId,
            onPointerAction: handlePointerAction
          })
        )
      )
    )
  );
};

const LabelDesignerApp = ({ config }) => {
  const [labels, setLabels] = useState(() => PLACEHOLDER_LABELS.map((label) => cloneLabel(label)));
  const [selectedLabelId, setSelectedLabelId] = useState(() => labels[0]?.id || null);
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [actionFeedback, setActionFeedback] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isPrinting, setIsPrinting] = useState(false);

  const saveLayoutUrl = config?.saveLayoutUrl || null;
  const trialPrintUrl = config?.trialPrintUrl || null;
  const selectedPrinterName = config?.selectedPrinterName || null;

  const selectedLabel = useMemo(
    () => labels.find((label) => label.id === selectedLabelId) || null,
    [labels, selectedLabelId]
  );

  useEffect(() => {
    setSelectedFieldId(null);
  }, [selectedLabelId]);

  useEffect(() => {
    if (!actionFeedback) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      setActionFeedback(null);
    }, 4500);
    return () => {
      window.clearTimeout(timer);
    };
  }, [actionFeedback]);

  const updateLabel = useCallback((labelId, updater) => {
    setLabels((current) =>
      current.map((label) => {
        if (label.id !== labelId) {
          return label;
        }
        const next = cloneLabel(label);
        updater(next);
        return next;
      })
    );
  }, []);

  const handleFieldUpdate = useCallback(
    (fieldId, updates) => {
      if (!selectedLabelId) {
        return;
      }
      updateLabel(selectedLabelId, (draft) => {
        draft.fields = draft.fields.map((field) =>
          field.id === fieldId ? { ...field, ...updates } : field
        );
      });
    },
    [selectedLabelId, updateLabel]
  );

  const handleFieldCreate = useCallback(
    (field) => {
      if (!selectedLabelId) {
        return;
      }
      const nextField = { ...field };
      updateLabel(selectedLabelId, (draft) => {
        draft.fields = [...draft.fields, nextField];
      });
      setSelectedFieldId(nextField.id);
    },
    [selectedLabelId, updateLabel]
  );

  const handleFieldDelete = useCallback(() => {
    if (!selectedLabelId || !selectedFieldId) {
      return;
    }
    updateLabel(selectedLabelId, (draft) => {
      draft.fields = draft.fields.filter((field) => field.id !== selectedFieldId);
    });
    setSelectedFieldId(null);
  }, [selectedLabelId, selectedFieldId, updateLabel]);

  const selectedField = useMemo(() => {
    if (!selectedLabel || !selectedFieldId) {
      return null;
    }
    return selectedLabel.fields.find((field) => field.id === selectedFieldId) || null;
  }, [selectedLabel, selectedFieldId]);

  const handleSaveLayout = useCallback(async () => {
    if (!selectedLabel) {
      setActionFeedback({ type: 'error', message: 'Select a label before saving.' });
      return;
    }
    if (!saveLayoutUrl) {
      setActionFeedback({ type: 'error', message: 'Saving is not configured for this environment.' });
      return;
    }
    const layout = toSerializableLayout(selectedLabel);
    setIsSaving(true);
    try {
      const response = await postJson(saveLayoutUrl, { label_id: layout.id, layout });
      const message =
        response && typeof response === 'object' && response !== null && response.message
          ? response.message
          : 'Label layout saved.';
      setActionFeedback({ type: 'success', message });
    } catch (error) {
      setActionFeedback({
        type: 'error',
        message: error.message || 'Failed to save label layout.'
      });
    } finally {
      setIsSaving(false);
    }
  }, [saveLayoutUrl, selectedLabel]);

  const handleTrialPrint = useCallback(async () => {
    if (!selectedLabel) {
      setActionFeedback({ type: 'error', message: 'Select a label before requesting a trial print.' });
      return;
    }
    if (!trialPrintUrl) {
      setActionFeedback({ type: 'error', message: 'Trial printing is not configured for this environment.' });
      return;
    }
    if (!selectedPrinterName) {
      setActionFeedback({ type: 'error', message: 'Choose an active printer before sending a trial print.' });
      return;
    }
    const layout = toSerializableLayout(selectedLabel);
    setIsPrinting(true);
    try {
      const response = await postJson(trialPrintUrl, { label_id: layout.id, layout });
      const message =
        response && typeof response === 'object' && response !== null && response.message
          ? response.message
          : `Trial print queued for ${selectedPrinterName}.`;
      setActionFeedback({ type: 'success', message });
    } catch (error) {
      setActionFeedback({
        type: 'error',
        message: error.message || 'Failed to queue a trial print.'
      });
    } finally {
      setIsPrinting(false);
    }
  }, [selectedLabel, selectedPrinterName, trialPrintUrl]);

  return h(
    'div',
    { className: 'grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)_320px]' },
    h(
      'div',
      { className: 'space-y-6' },
      LabelList({
        labels,
        selectedId: selectedLabelId,
        onSelect: setSelectedLabelId
      }),
      FieldToolbox({ label: selectedLabel, onAddField: handleFieldCreate })
    ),
    h(
      'div',
      { className: 'min-h-[520px]' },
      LabelPreview({
        label: selectedLabel,
        selectedFieldId,
        onSelectField: setSelectedFieldId,
        onUpdateField: handleFieldUpdate,
        onCanvasClick: () => setSelectedFieldId(null)
      })
    ),
    h(
      'div',
      { className: 'space-y-4' },
      ActionPanel({
        label: selectedLabel,
        saveUrl: saveLayoutUrl,
        trialPrintUrl,
        printerName: selectedPrinterName,
        onSave: handleSaveLayout,
        onTrialPrint: handleTrialPrint,
        saving: isSaving,
        printing: isPrinting,
        feedback: actionFeedback
      }),
      PropertyInspector({
        label: selectedLabel,
        selectedField,
        onFieldChange: (updates) => {
          if (selectedFieldId) {
            handleFieldUpdate(selectedFieldId, updates);
          }
        },
        onFieldDelete: handleFieldDelete
      })
    )
  );
};

function bootstrapLabelDesigner() {
  const rootElement = document.getElementById('label-designer-root');
  if (!rootElement) {
    return;
  }
  const config = window.labelDesignerConfig || {};
  const root = ReactDOM.createRoot(rootElement);
  root.render(h(LabelDesignerApp, { config }));
}

bootstrapLabelDesigner();
