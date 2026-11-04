(function () {
    'use strict';
    async function post(url, data) {
        const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content}, body:JSON.stringify(data)});
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || 'Request failed.');
        return result;
    }
    const locations = fetch('/admin/issuing-locations/options').then(async r => {
        if (!r.ok) throw new Error('Unable to load issuing locations. Check migration 005.');
        return (await r.json()).locations;
    });
    async function history(id) {
        try {
            const response = await fetch('/admin/consignments/' + id + '/lr-history');
            if (!response.ok) throw new Error('Unable to load LR history.');
            const {events} = await response.json();
            const dialog = document.createElement('dialog');
            const title = document.createElement('h4'); title.textContent = 'LR audit history'; dialog.append(title);
            for (const event of events) {
                const entry = document.createElement('p');
                entry.textContent = event.lr_number + ' · ' + event.action + ' · ' + new Date(event.recorded_at).toLocaleString() +
                    ' · ' + event.actor_id + (event.reason ? ' · ' + event.reason : '');
                dialog.append(entry);
            }
            if (!events.length) { const text = document.createElement('p'); text.textContent = 'Historical shipment: no LR events recorded since migration.'; dialog.append(text); }
            const close = document.createElement('button'); close.textContent = 'Close'; close.className = 'btn btn-secondary';
            close.onclick = () => { dialog.close(); dialog.remove(); }; dialog.append(close);
            document.body.append(dialog); dialog.showModal();
        } catch (error) { show(error.message); }
    }
    window.lrControl = {post, locations, history};
    function show(message) {
        const target = document.getElementById('location-message') || document.getElementById('status-msg');
        if (target) { target.textContent = message; target.className = 'alert alert-info'; }
    }
    locations.then(rows => {
        const filter = document.getElementById('issuing-location-filter');
        const importSelect = document.getElementById('import-location-code');
        rows.forEach(row => {
            if (filter) filter.add(new Option(row.location_code + ' — ' + row.location_name, row.id));
            if (importSelect && row.is_active) importSelect.add(new Option(row.location_code + ' — ' + row.location_name, row.location_code));
        });
        if (filter) { filter.value = filter.dataset.selected || ''; filter.dataset.loaded = 'true'; }
        const body = document.getElementById('location-rows'), form = document.getElementById('location-form');
        if (!body || !form) return;
        rows.forEach(row => {
            const tr = document.createElement('tr');
            [row.location_code, row.location_name, row.is_active ? 'Active' : 'Inactive'].forEach(value => {
                const td = document.createElement('td'); td.textContent = value; tr.append(td);
            });
            const td = document.createElement('td'), edit = document.createElement('button');
            edit.textContent = 'Edit'; edit.className = 'btn btn-outline-primary btn-sm';
            edit.onclick = () => {
                for (const key of ['id','location_name','location_code']) form.elements[key].value = row[key];
                form.elements.is_active.checked = row.is_active;
                form.elements.location_code.readOnly = row.code_locked;
            };
            td.append(edit); tr.append(td); body.append(tr);
        });
        form.onreset = () => { form.elements.location_code.readOnly = false; };
        form.onsubmit = async event => {
            event.preventDefault();
            const button = form.querySelector('button'); button.disabled = true;
            try {
                await post('/admin/issuing-locations', {id:form.elements.id.value || null, location_name:form.elements.location_name.value,
                    location_code:form.elements.location_code.value, is_active:form.elements.is_active.checked});
                location.reload();
            } catch (error) { show(error.message); button.disabled = false; }
        };
    }).catch(error => show(error.message));
})();
