(function () {
    'use strict';
    const initial = new URL(location.href).searchParams;
    let shipmentId = initial.get('shipment_id');
    function read() {
        const filters = {
            search: document.getElementById('search-input').value.trim(),
            company: document.getElementById('company-filter').value,
            identifier_type: document.getElementById('identifier-type-filter').value,
        };
        if (shipmentId && Object.entries(filters).some(([key, value]) => value !== (initial.get(key) || ''))) shipmentId = null;
        if (shipmentId) filters.shipment_id = shipmentId;
        return filters;
    }
    function url(base, filters) {
        const target = new URL(base, location.origin);
        if (!filters.shipment_id) target.searchParams.delete('shipment_id');
        for (const [key, value] of Object.entries(filters)) {
            if (value) target.searchParams.set(key, value); else target.searchParams.delete(key);
        }
        return target.href;
    }
    function clear() {
        shipmentId = null;
        for (const id of ['search-input', 'company-filter', 'identifier-type-filter']) document.getElementById(id).value = '';
    }
    window.shipmentFilters = {read, url, clear, remember(filters) { history.replaceState(null, '', url(location.href, filters)); }};
})();
