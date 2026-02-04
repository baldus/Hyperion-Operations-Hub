(() => {
    const dropdown = document.querySelector('[data-dropdown]');
    if (!dropdown) {
        return;
    }

    const toggle = dropdown.querySelector('[data-dropdown-toggle]');
    const menu = dropdown.querySelector('[data-dropdown-menu]');
    if (toggle && menu) {
        const closeMenu = () => dropdown.classList.remove('is-open');
        toggle.addEventListener('click', () => {
            dropdown.classList.toggle('is-open');
        });
        document.addEventListener('click', (event) => {
            if (!dropdown.contains(event.target)) {
                closeMenu();
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeMenu();
            }
        });
    }

    const table = document.querySelector('[data-shortages-table]');
    const form = dropdown.querySelector('[data-columns-form]');
    if (!table || !form) {
        return;
    }

    const allColumns = JSON.parse(table.dataset.allColumns || '[]');
    const defaultColumns = JSON.parse(table.dataset.defaultColumns || '[]');
    const saveUrl = table.dataset.saveUrl;
    const checkboxes = Array.from(
        form.querySelectorAll('input[type="checkbox"][name="columns"]')
    );

    if (!allColumns.length || !saveUrl || !checkboxes.length) {
        return;
    }

    const columnNodes = new Map();
    allColumns.forEach((key) => {
        columnNodes.set(key, table.querySelectorAll(`[data-col="${key}"]`));
    });

    const normalizeSelection = (keys) => {
        const selected = new Set(keys);
        return allColumns.filter((key) => selected.has(key));
    };

    const setColumnVisibility = (keys) => {
        const visible = new Set(keys);
        allColumns.forEach((key) => {
            const nodes = columnNodes.get(key);
            if (!nodes) {
                return;
            }
            nodes.forEach((node) => {
                node.classList.toggle('is-hidden', !visible.has(key));
            });
        });
    };

    const syncCheckboxes = (keys) => {
        const visible = new Set(keys);
        checkboxes.forEach((checkbox) => {
            checkbox.checked = visible.has(checkbox.value);
        });
    };

    let saveTimeout;
    const scheduleSave = (keys) => {
        window.clearTimeout(saveTimeout);
        saveTimeout = window.setTimeout(() => {
            fetch(saveUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                },
                body: JSON.stringify({
                    action: keys.length ? 'save' : 'reset',
                    visible_columns: keys,
                }),
            })
                .then((response) => response.json())
                .then((payload) => {
                    if (!payload || !Array.isArray(payload.visible_columns)) {
                        return;
                    }
                    const effective = normalizeSelection(payload.visible_columns);
                    const applied = effective.length ? effective : defaultColumns;
                    setColumnVisibility(applied);
                    syncCheckboxes(applied);
                })
                .catch(() => {});
        }, 200);
    };

    const handleChange = () => {
        const checked = normalizeSelection(
            checkboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value)
        );
        if (!checked.length) {
            setColumnVisibility(defaultColumns);
            syncCheckboxes(defaultColumns);
            scheduleSave([]);
            return;
        }
        setColumnVisibility(checked);
        scheduleSave(checked);
    };

    checkboxes.forEach((checkbox) => {
        checkbox.addEventListener('change', handleChange);
    });
})();
