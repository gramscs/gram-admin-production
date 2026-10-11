(function () {
    'use strict';
    function read() {
        return {
            search: document.getElementById('search-input').value.trim(),
            company: document.getElementById('company-filter').value,
            identifier_type: document.getElementById('identifier-type-filter').value,
        };
    }
    function url(base, filters) {
        const target = new URL(base, location.origin);
        for (const [key, value] of Object.entries(filters)) {
            if (value) target.searchParams.set(key, value); else target.searchParams.delete(key);
        }
        return target.href;
    }
    function clear() {
        for (const id of ['search-input', 'company-filter', 'identifier-type-filter']) document.getElementById(id).value = '';
    }
    window.shipmentFilters = {read, url, clear, remember(filters) { history.replaceState(null, '', url(location.href, filters)); }};
})();
