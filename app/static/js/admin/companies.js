document.addEventListener('DOMContentLoaded', function () {
    const modalEl = document.getElementById('companyModal'), modal = new bootstrap.Modal(modalEl), form = document.getElementById('company-form');
    let editingId = null, viewOnly = false;
    function addLocation(kind, value) {
        const item = document.getElementById('location-template').content.firstElementChild.cloneNode(true);
        const container = document.getElementById('company-' + kind + '-locations');
        item.dataset.kind = kind; item.dataset.id = value?.id || '';
        item.querySelector('.location-label').value = value?.label || '';
        item.querySelector('.location-address').value = value?.address || '';
        item.querySelector('.location-pincode').value = value?.pincode || '';
        item.querySelector('.location-default').checked = value ? value.is_default : container.children.length === 0;
        item.querySelector('.location-default').addEventListener('change', function () {
            if (this.checked) container.querySelectorAll('.location-default').forEach(input => { if (input !== this) input.checked = false; });
        });
        item.querySelector('.remove-location').addEventListener('click', () => item.remove());
        container.append(item);
    }
    function open(company, restore, readOnly = false) {
        viewOnly = readOnly;
        editingId = company?.id || null; form.reset(); adminUI.alert('company-form-error', '');
        document.getElementById('company-modal-title').textContent = viewOnly ? 'View Company' : editingId ? 'Edit Company' : 'Add Company';
        ['name', 'address', 'email', 'phone'].forEach(key => document.getElementById('company-' + key).value = company?.[key] || '');
        document.getElementById('company-active').checked = restore || !company || company.active;
        ['pickup', 'drop'].forEach(kind => document.getElementById('company-' + kind + '-locations').replaceChildren());
        (company?.locations || []).forEach(location => addLocation(location.kind, location));
        form.querySelectorAll('input, textarea').forEach(input => {
            if (input.type === 'checkbox') input.disabled = viewOnly;
            else input.readOnly = viewOnly;
        });
        form.querySelectorAll('.add-location, .remove-location').forEach(button => {button.hidden = viewOnly;});
        const save = document.getElementById('company-save-btn'); save.hidden = viewOnly; save.disabled = viewOnly;
        document.getElementById('company-cancel-btn').textContent = viewOnly ? 'Close' : 'Cancel';
        modal.show();
    }
    document.getElementById('add-company-btn').addEventListener('click', () => open(null));
    modalEl.addEventListener('shown.bs.modal', () => document.getElementById('company-name').focus());
    document.querySelectorAll('.add-location').forEach(button => button.addEventListener('click', () => addLocation(button.dataset.kind)));
    document.querySelectorAll('[data-company]').forEach(row => {
        const company = JSON.parse(row.dataset.company);
        row.querySelector('.view-company').addEventListener('click', () => open(company, false, true));
        row.querySelector('.edit-company').addEventListener('click', () => open(company));
        row.querySelector('.restore-company')?.addEventListener('click', () => open(company, true));
        row.querySelector('.archive-company')?.addEventListener('click', async () => {
            if (!confirm('Archive ' + company.name + '? Existing shipments and their addresses will be kept.')) return;
            try { await adminUI.request('/admin/companies/' + company.id + '/archive', {}); location.reload(); }
            catch (error) { adminUI.alert('companies-error', error.message); }
        });
    });
    form.addEventListener('submit', async event => {
        event.preventDefault(); if (viewOnly) return;
        const button = document.getElementById('company-save-btn'); button.disabled = true;
        const data = Object.fromEntries(['name', 'address', 'email', 'phone'].map(key => [key, document.getElementById('company-' + key).value]));
        data.active = document.getElementById('company-active').checked;
        data.locations = Array.from(form.querySelectorAll('.location-editor')).map(item => ({id: item.dataset.id || null, kind: item.dataset.kind, label: item.querySelector('.location-label').value,
            address: item.querySelector('.location-address').value, pincode: item.querySelector('.location-pincode').value, is_default: item.querySelector('.location-default').checked}));
        try { await adminUI.request('/admin/companies' + (editingId ? '/' + editingId : ''), data, editingId ? 'PUT' : 'POST'); location.reload(); }
        catch (error) { adminUI.alert('company-form-error', error.message); button.disabled = false; }
    });
});
