(function () {
    'use strict';
    const selected = new Set();
    const input = document.getElementById('bulk-document-files');
    const review = document.getElementById('bulk-document-review');
    const feedback = document.getElementById('bulk-documents-feedback');
    const applyButton = document.getElementById('bulk-upload-btn');
    let busy = false;
    function show(message, error = false) {
        feedback.textContent = message;
        feedback.className = 'alert ' + (error ? 'alert-danger' : 'alert-success');
    }
    function updateSelection() {
        document.getElementById('bulk-selected-count').textContent = selected.size;
        const boxes = [...document.querySelectorAll('.select-shipment:not(:disabled)')];
        const all = document.getElementById('select-visible-shipments');
        const count = boxes.filter(box => box.checked).length;
        all.checked = boxes.length > 0 && count === boxes.length;
        all.indeterminate = count > 0 && count < boxes.length;
    }
    function restore(row) {
        const box = row.querySelector('.select-shipment');
        if (box) box.checked = selected.has(row.dataset.id);
        updateSelection();
    }
    function clearSelection() {
        selected.clear();
        document.querySelectorAll('.select-shipment').forEach(box => box.checked = false);
        updateSelection();
    }
    document.getElementById('sheet-body').addEventListener('change', event => {
        if (!event.target.matches('.select-shipment')) return;
        const id = event.target.closest('tr').dataset.id;
        if (event.target.checked) selected.add(id); else selected.delete(id);
        updateSelection();
    });
    document.getElementById('select-visible-shipments').addEventListener('change', event => {
        document.querySelectorAll('.select-shipment:not(:disabled)').forEach(box => {
            const id = box.closest('tr').dataset.id;
            box.checked = event.target.checked;
            if (box.checked) selected.add(id); else selected.delete(id);
        });
        updateSelection();
    });
    document.getElementById('bulk-clear-selection').addEventListener('click', clearSelection);
    function resetReview() { review.replaceChildren(); applyButton.disabled = true; }
    input.addEventListener('change', resetReview);
    async function upload(apply) {
        if (busy) return;
        resetReview();
        const files = [...input.files];
        if (!files.length || files.length > 50 || files.some(file => !file.size || file.size > 5 * 1024 * 1024) || files.reduce((size, file) => size + file.size, 0) > 50 * 1024 * 1024) {
            show('Choose 1–50 files, no more than 5 MB each or 50 MB combined.', true); return;
        }
        busy = true;
        const check = document.getElementById('bulk-review-btn');
        check.disabled = true; input.disabled = true;
        show(apply ? 'Uploading checked documents…' : 'Checking document contents and consignment matches…');
        try {
            const body = new FormData();
            files.forEach(file => body.append('files', file, file.name));
            body.append('apply', String(apply));
            const response = await fetch(input.dataset.url, {method: 'POST', body, credentials: 'same-origin'});
            const data = await response.json().catch(() => { throw new Error('Upload response was unavailable. Check your session and file-size limit.'); });
            if (!response.ok || !data.success) {
                const errors = (data.errors || []).map(item => item.filename + ': ' + item.message);
                throw new Error([data.message || 'Upload failed.', ...errors].join('\n'));
            }
            const list = document.createElement('ul');
            data.review.forEach(item => {
                const row = document.createElement('li');
                row.textContent = item.filename + ' → ' + item.consignment_number + ' · ' + item.kind.toUpperCase() + (item.replaces_existing ? ' · replaces existing document' : ' · new document');
                list.append(row);
            });
            review.append(list);
            if (apply) {
                input.value = '';
                document.dispatchEvent(new CustomEvent('shipment-documents-updated', {detail: data.documents}));
                show(data.count + ' documents uploaded and saved.');
            } else {
                applyButton.disabled = false;
                show(data.count + ' files matched. Review the list, then upload.');
            }
        } catch (error) { show(error.message, true); }
        finally { busy = false; check.disabled = false; input.disabled = false; }
    }
    document.getElementById('bulk-review-btn').addEventListener('click', () => upload(false));
    applyButton.addEventListener('click', () => upload(true));
    document.getElementById('bulk-download-btn').addEventListener('click', async event => {
        if (busy) return;
        const scope = document.getElementById('bulk-download-scope').value;
        if (scope === 'selected' && !selected.size) { show('Select saved shipments using the table checkboxes.', true); return; }
        busy = true; event.target.disabled = true;
        show('Preparing your document ZIP…');
        try {
            const response = await fetch(event.target.dataset.url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...window.shipmentFilters.read(), ids: scope === 'selected' ? [...selected] : [], kind: document.getElementById('bulk-download-kind').value})});
            if (!response.ok || !(response.headers.get('Content-Type') || '').includes('application/zip')) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.message || 'ZIP could not be downloaded. Check your session.');
            }
            const content = await response.blob();
            if (!content.size) throw new Error('ZIP was empty. Try again.');
            const url = URL.createObjectURL(content), link = document.createElement('a');
            link.href = url; link.download = 'consignment-' + document.getElementById('bulk-download-kind').value + '-documents.zip';
            document.body.append(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 30000);
            const omitted = Number(response.headers.get('X-Documents-Omitted') || 0);
            show('ZIP downloaded.' + (omitted ? ' ' + omitted + ' documents were not attached or could not be retrieved.' : '') + ' See document-manifest.json for details.');
        } catch (error) { show(error.message, true); }
        finally { busy = false; event.target.disabled = false; }
    });
    window.shipmentBulkDocuments = {restore, clearSelection, refreshSelection: updateSelection, forget(id) { selected.delete(String(id)); updateSelection(); }};
})();
