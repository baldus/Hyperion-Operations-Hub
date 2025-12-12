(function () {
    const table = document.querySelector('#framing-queue-table[data-enhanced="true"]');
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const headers = Array.from(table.querySelectorAll('th[data-column-id]'));
    const filterInput = document.getElementById('framing-table-filter');
    const columnToggles = document.querySelectorAll('[data-column-toggle]');
    let sortState = { columnId: null, direction: 'asc' };

    function parseValue(rawValue, type) {
        if (type === 'number') {
            const numberValue = parseFloat(rawValue);
            return {
                value: numberValue,
                empty: Number.isNaN(numberValue),
            };
        }
        if (type === 'date') {
            const parsed = Date.parse(rawValue);
            return {
                value: parsed,
                empty: Number.isNaN(parsed),
            };
        }
        const stringValue = (rawValue || '').toString().toLowerCase();
        return { value: stringValue, empty: stringValue.trim().length === 0 };
    }

    function getCellValue(row, columnId) {
        const cell = row.querySelector(`[data-column-id="${columnId}"]`);
        if (!cell) return '';
        return cell.dataset.sortValue !== undefined ? cell.dataset.sortValue : cell.textContent;
    }

    function clearSortIndicators() {
        headers.forEach((header) => header.classList.remove('is-sorted-asc', 'is-sorted-desc'));
    }

    function applySort(columnId, type) {
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const direction =
            sortState.columnId === columnId && sortState.direction === 'asc' ? 'desc' : 'asc';

        rows.sort((a, b) => {
            const aValue = parseValue(getCellValue(a, columnId), type);
            const bValue = parseValue(getCellValue(b, columnId), type);

            if (aValue.empty && bValue.empty) return 0;
            if (aValue.empty) return 1;
            if (bValue.empty) return -1;

            if (aValue.value < bValue.value) return direction === 'asc' ? -1 : 1;
            if (aValue.value > bValue.value) return direction === 'asc' ? 1 : -1;
            return 0;
        });

        rows.forEach((row) => tbody.appendChild(row));

        sortState = { columnId, direction };
        clearSortIndicators();
        const activeHeader = headers.find((header) => header.dataset.columnId === columnId);
        if (activeHeader) {
            activeHeader.classList.add(direction === 'asc' ? 'is-sorted-asc' : 'is-sorted-desc');
        }
    }

    function handleHeaderAction(event, header) {
        const type = header.dataset.sortType || 'string';
        applySort(header.dataset.columnId, type);
        event.preventDefault();
    }

    headers.forEach((header) => {
        header.addEventListener('click', (event) => handleHeaderAction(event, header));
        header.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                handleHeaderAction(event, header);
            }
        });
    });

    function filterRows(term) {
        const query = term.trim().toLowerCase();
        const rows = tbody.querySelectorAll('tr');

        rows.forEach((row) => {
            if (!query) {
                row.style.display = '';
                return;
            }

            const textContent = row.textContent.toLowerCase();
            row.style.display = textContent.includes(query) ? '' : 'none';
        });
    }

    if (filterInput) {
        filterInput.addEventListener('input', (event) => {
            filterRows(event.target.value || '');
        });
    }

    function toggleColumnVisibility(columnId, isVisible) {
        const cells = table.querySelectorAll(`[data-column-id="${columnId}"]`);
        cells.forEach((cell) => {
            cell.style.display = isVisible ? '' : 'none';
        });
    }

    columnToggles.forEach((toggle) => {
        toggle.addEventListener('change', (event) => {
            const target = event.target;
            toggleColumnVisibility(target.value, target.checked);
        });
        toggleColumnVisibility(toggle.value, toggle.checked);
    });
})();
