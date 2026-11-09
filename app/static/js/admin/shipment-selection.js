(function () {
    'use strict';
    // A back/forward cache snapshot may predate an archive in another page.
    window.addEventListener('pageshow', event => {if (event.persisted) window.location.reload();});
    window.shipmentSelection = {
        async prune(url, selected) {
            const ids = Array.from(selected.keys());
            const active = new Set();
            for (let offset = 0; offset < ids.length; offset += 500) {
                const response = await fetch(url, {method: 'POST', headers: {
                    'Content-Type': 'application/json', Accept: 'application/json',
                }, body: JSON.stringify({consignment_ids: ids.slice(offset, offset + 500)})});
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || 'Could not refresh shipment selections.');
                result.consignment_ids.forEach(id => active.add(id));
            }
            let removed = false;
            ids.forEach(id => {if (!active.has(id)) removed = selected.delete(id) || removed;});
            return removed;
        },
    };
})();
