(function () {
    'use strict';
    const body = document.getElementById('sheet-body');
    const count = document.getElementById('document-count');
    const search = document.getElementById('search-input');
    const perPage = document.getElementById('per-page-select');
    const previous = document.getElementById('prev-page-btn'), next = document.getElementById('next-page-btn');
    let page = 1, totalPages = 1, requestNumber = 0, searchTimer;
    function node(tag, className, text) {
        const element = document.createElement(tag);
        element.className = className || '';
        if (text !== undefined) element.textContent = text;
        return element;
    }
    function feedback(message, tone = 'danger') {
        const error = document.getElementById('status-msg');
        error.textContent = message;
        error.className = message ? 'alert alert-' + tone : 'alert d-none';
    }
    function openEditor(row, kind) {
        window.shipmentDocumentEditor.open(row, kind, async message => {
            if (await load()) feedback(message, 'success');
        });
    }
    function documentCell(row, kind) {
        const cell = node('td');
        const reference = row[kind === 'pod' ? 'pod_image' : 'invoice_file'];
        if (!reference) {
            const missing = node('span', 'document-missing', 'Not uploaded');
            cell.append(missing);
            const upload = node('button', 'btn btn-outline-primary btn-sm document-upload-action', 'Upload');
            upload.type = 'button'; upload.setAttribute('aria-label', 'Upload ' + kind.toUpperCase() + ' for ' + row.consignment_number);
            upload.addEventListener('click', () => openEditor(row, kind)); cell.append(upload);
            return cell;
        }
        const card = node('div', 'document-card');
        const url = '/admin/consignments/' + encodeURIComponent(row.id) + '/' + kind;
        const details = node('div', 'document-details');
        details.append(node('span', 'document-name', row[kind + '_original_name'] || 'Attached ' + kind.toUpperCase()));
        if (/\.(png|jpe?g|webp)$/i.test(reference)) {
            const image = node('img', 'document-thumb');
            image.alt = kind.toUpperCase() + ' preview for ' + row.consignment_number;
            image.loading = 'lazy'; image.src = url + '?preview=1';
            image.addEventListener('error', () => {
                image.hidden = true;
                details.append(node('span', 'document-preview-error', 'Preview unavailable. Try downloading the document.'));
            }, {once: true});
            card.append(image);
        } else {
            const icon = node('span', 'document-file-icon');
            icon.setAttribute('aria-hidden', 'true'); icon.append(node('i', 'fa fa-file-pdf-o')); card.append(icon);
        }
        const download = node('a', 'btn btn-outline-primary btn-sm', 'Download');
        download.href = url;
        download.setAttribute('aria-label', 'Download ' + kind.toUpperCase() + ' for ' + row.consignment_number);
        download.addEventListener('click', async event => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            if (download.getAttribute('aria-busy') === 'true') return;
            download.setAttribute('aria-busy', 'true');
            try { await window.adminReportDownloads.download(url); }
            catch (error) { feedback(error.message); }
            finally { download.removeAttribute('aria-busy'); }
        });
        const actions = node('div', 'document-actions');
        const manage = node('button', 'btn btn-outline-secondary document-icon-action');
        manage.type = 'button'; manage.title = 'Manage ' + kind.toUpperCase();
        manage.setAttribute('aria-label', 'Manage ' + kind.toUpperCase() + ' for ' + row.consignment_number);
        const pencil = node('i', 'fa fa-pencil'); pencil.setAttribute('aria-hidden', 'true'); manage.append(pencil);
        manage.addEventListener('click', () => openEditor(row, kind));
        actions.append(download, manage); details.append(actions); card.append(details); cell.append(card);
        return cell;
    }
    function render(row) {
        const tr = node('tr'); tr.dataset.id = String(row.id);
        const selection = node('td'), box = node('input', 'form-check-input select-shipment');
        box.type = 'checkbox'; box.setAttribute('aria-label', 'Select ' + row.consignment_number); selection.append(box); tr.append(selection);
        const identity = node('td', 'shipment-identity');
        identity.append(node('strong', '', row.consignment_number), node('span', 'd-block small text-muted mt-1', row.status || 'No status'));
        tr.append(identity, node('td', '', row.identifier_type || 'LRN'), node('td', 'document-company', row.company_name || 'Unassigned company'));
        tr.append(documentCell(row, 'pod'), documentCell(row, 'invoice'));
        const action = node('td'), shipment = node('a', 'btn btn-outline-secondary document-icon-action');
        shipment.title = 'View shipment'; shipment.setAttribute('aria-label', 'View shipment');
        const eye = node('i', 'fa fa-eye'); eye.setAttribute('aria-hidden', 'true'); shipment.append(eye);
        shipment.href = window.shipmentFilters.url(body.dataset.shipmentUrl, {search: row.consignment_number, identifier_type: row.identifier_type || 'LRN', company: row.company_id || 'unassigned', shipment_id: row.id});
        action.append(shipment); tr.append(action); return tr;
    }
    async function load() {
        const sequence = ++requestNumber;
        const filters = window.shipmentFilters.read();
        window.shipmentFilters.remember(filters);
        previous.disabled = true; next.disabled = true;
        count.textContent = 'Loading shipments…'; feedback('');
        const data = await adminAPI.fetchList(body.dataset.listUrl, {...filters, page, per_page: perPage.value});
        if (sequence !== requestNumber) return;
        if (!data.success) { feedback(data.error || data.message || 'Could not load shipment documents.'); count.textContent = 'Documents unavailable'; return; }
        totalPages = Math.max(1, data.pages);
        if (page > totalPages) { page = totalPages; return load(); }
        body.replaceChildren();
        data.rows.forEach(row => body.append(render(row)));
        if (!data.rows.length) {
            const tr = node('tr'), cell = node('td', 'empty-panel'); cell.colSpan = 7;
            cell.append(node('strong', '', 'No matching shipments'), node('span', '', 'Change your filters, or create and save a shipment first.')); tr.append(cell); body.append(tr);
        }
        body.querySelectorAll('tr[data-id]').forEach(tr => window.shipmentBulkDocuments.restore(tr));
        // Also update the master checkbox when the result is empty.
        window.shipmentBulkDocuments.refreshSelection();
        const start = data.total ? (page - 1) * Number(perPage.value) + 1 : 0;
        count.textContent = 'Showing ' + start + '–' + Math.min(page * Number(perPage.value), data.total) + ' of ' + data.total + ' shipments';
        document.getElementById('document-page').textContent = 'Page ' + page + ' of ' + totalPages;
        previous.disabled = page <= 1; next.disabled = page >= totalPages;
        return true;
    }
    function resetPage() { clearTimeout(searchTimer); page = 1; load(); }
    for (const id of ['company-filter', 'identifier-type-filter', 'per-page-select']) document.getElementById(id).addEventListener('change', resetPage);
    search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(resetPage, 400); });
    document.getElementById('clear-filters-btn').addEventListener('click', () => { window.shipmentFilters.clear(); resetPage(); });
    previous.addEventListener('click', () => { if (page > 1) { page--; load(); } });
    next.addEventListener('click', () => { if (page < totalPages) { page++; load(); } });
    document.addEventListener('shipment-documents-updated', load);
    load();
})();
