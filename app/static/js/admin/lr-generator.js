document.addEventListener('DOMContentLoaded', () => {
    'use strict';
    const part = id => document.getElementById(id), workspace = part('lr-workspace');
    if (!workspace) return;
    const pageRows = JSON.parse(part('lr-page-data').textContent);
    const schema = JSON.parse(part('lr-field-data').textContent);
    const key = 'gram-lr-selection:' + workspace.dataset.scope + ':' + workspace.dataset.idFormat;
    const idPattern = workspace.dataset.idFormat === 'uuid' ? /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i : /^[1-9][0-9]*$/;
    const checkboxes = Array.from(document.querySelectorAll('[name=lr_shipment]'));
    let selected = new Map(), drafts = {}, review = [], busy = false;
    try {
        const saved = JSON.parse(sessionStorage.getItem(key) || '{}');
        if (Array.isArray(saved.selected)) selected = new Map(saved.selected.filter(row => row && typeof row.id === 'string' && idPattern.test(row.id)).map(row => [row.id, row]));
        if (saved.drafts && typeof saved.drafts === 'object') drafts = saved.drafts;
    } catch (_) { /* Selection remains usable without browser storage. */ }
    pageRows.forEach(row => {if (selected.has(row.id)) selected.set(row.id, row);});
    function node(tag, className, text) {
        const element = document.createElement(tag); element.className = className || '';
        if (text !== undefined) element.textContent = text;
        return element;
    }
    function persist() {
        try {sessionStorage.setItem(key, JSON.stringify({selected: Array.from(selected.values()), drafts}));} catch (_) {}
    }
    function error(id, message) {
        part(id).textContent = message; part(id).classList.toggle('d-none', !message);
    }
    function update() {
        checkboxes.forEach(input => {input.checked = selected.has(input.value);});
        const checked = checkboxes.filter(input => input.checked).length;
        part('lr-select-all').checked = checkboxes.length > 0 && checked === checkboxes.length;
        part('lr-select-all').indeterminate = checked > 0 && checked < checkboxes.length;
        part('lr-selected-count').textContent = selected.size;
        part('lr-review').disabled = busy || !selected.size || selected.size > 100;
        const list = part('lr-selected-list'); list.replaceChildren();
        selected.forEach(row => {
            const item = node('li'), remove = node('button', 'btn btn-sm btn-outline-secondary', '×');
            remove.type = 'button'; remove.setAttribute('aria-label', 'Remove ' + row.consignment_number);
            remove.addEventListener('click', () => {selected.delete(row.id); delete drafts[row.id]; update();});
            item.append(node('span', '', row.consignment_number), remove); list.append(item);
        });
        if (selected.size > 100) error('lr-error', 'Select at most 100 shipments per batch.');
        else error('lr-error', '');
        persist();
    }
    checkboxes.forEach(input => input.addEventListener('change', () => {
        if (input.checked) selected.set(input.value, pageRows.find(row => row.id === input.value));
        else selected.delete(input.value);
        update();
    }));
    part('lr-select-all').addEventListener('change', event => {
        checkboxes.forEach(input => {
            if (event.target.checked) selected.set(input.value, pageRows.find(row => row.id === input.value));
            else selected.delete(input.value);
        }); update();
    });
    part('lr-clear').addEventListener('click', () => {selected.clear(); drafts = {}; update();});
    function values(card) {
        const item = {id: card.dataset.id};
        card.querySelectorAll('[data-field]').forEach(input => {item[input.dataset.field] = input.value;});
        return item;
    }
    function render() {
        const root = part('lr-review-rows'); root.replaceChildren();
        review.forEach((row, index) => {
            const card = node('section', 'label-review-card'); card.dataset.id = row.id;
            card.append(node('h3', '', row.consignment_number));
            if (row.volume_ft3) card.append(node('p', 'small text-muted mt-2', 'Shipment volume: ' + row.volume_ft3 + ' ft³. Enter volume weight in kg separately if applicable.'));
            const data = {...row.values, ...(drafts[row.id] || {})};
            let groupName = '', group;
            schema.forEach(field => {
                if (groupName !== field.group) {
                    groupName = field.group;
                    const fieldset = node('fieldset', 'lr-review-group'); fieldset.append(node('legend', '', groupName));
                    group = node('div', 'lr-review-fields'); fieldset.append(group); card.append(fieldset);
                }
                const wrapper = node('div', 'lr-field');
                const input = node(field.kind === 'textarea' ? 'textarea' : field.kind === 'package' ? 'select' : 'input', field.kind === 'package' ? 'form-select' : 'form-control');
                input.id = 'lr-' + index + '-' + field.name; input.dataset.field = field.name; input.required = field.required;
                if (field.kind === 'package') {
                    ['BOX', 'PALLET', 'BAG', 'BUNDLE', 'DRUM', 'CARTON', 'PACKAGE'].forEach(value => {
                        const option = node('option', '', value); option.value = value; input.append(option);
                    });
                } else if (['money', 'weight', 'integer'].includes(field.kind)) {
                    input.type = 'number'; input.min = field.kind === 'integer' ? '1' : '0';
                    input.max = field.kind === 'integer' ? '1000000' : '999999999.99';
                    input.step = field.kind === 'money' ? '.01' : field.kind === 'weight' ? '.001' : '1';
                } else if (['date', 'time'].includes(field.kind)) input.type = field.kind;
                else {if (field.kind !== 'textarea') input.type = 'text'; input.maxLength = field.limit;}
                input.value = data[field.name] || '';
                const label = node('label', 'form-label', field.label + (field.required ? ' *' : '')); label.htmlFor = input.id;
                wrapper.append(label, input);
                if (row.sources.includes(field.name)) wrapper.append(node('span', 'lr-field-source', 'From shipment · review before generating'));
                group.append(wrapper);
            });
            card.addEventListener('input', () => {drafts[row.id] = values(card); persist();});
            card.addEventListener('change', () => {drafts[row.id] = values(card); persist();});
            root.append(card);
        });
        part('lr-review-summary').textContent = review.length + ' shipments · ' + review.length * 2 + ' A4 pages';
        error('lr-review-error', '');
    }
    part('lr-review').addEventListener('click', async () => {
        busy = true; update();
        try {
            const response = await fetch(workspace.dataset.reviewUrl, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({consignment_ids: Array.from(selected.keys())})});
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || result.error || 'Could not load shipments.');
            review = result.shipments; render();
            bootstrap.Modal.getOrCreateInstance(part('lrReviewModal')).show();
        } catch (failure) {error('lr-error', failure.message);}
        finally {busy = false; part('lr-review').disabled = !selected.size || selected.size > 100;}
    });
    part('lr-details-form').addEventListener('submit', event => {event.preventDefault(); generate('download');});
    part('lrReviewModal').addEventListener('hide.bs.modal', event => {if (busy) event.preventDefault();});
    async function generate(delivery) {
        if (busy || !part('lr-details-form').reportValidity()) return;
        let previewWindow = null;
        if (delivery === 'preview') {previewWindow = window.open('', '_blank'); if (previewWindow) {previewWindow.opener = null; previewWindow.document.title = 'Generating lorry receipts';}}
        const cards = Array.from(part('lr-review-rows').children), details = cards.map(values);
        details.forEach(item => {drafts[item.id] = item;}); persist();
        busy = true; error('lr-review-error', '');
        part('lrReviewModal').querySelectorAll('button, input, select, textarea').forEach(input => {input.disabled = true;});
        part('lr-download').textContent = 'Generating…';
        try {
            const response = await fetch(workspace.dataset.generateUrl, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/pdf'}, body: JSON.stringify({consignment_ids: review.map(row => row.id), lr_details: details, delivery})});
            if (!response.ok) {
                const result = await response.json(); throw new Error(result.message || result.error || 'Could not generate LRs.');
            }
            const blob = await response.blob();
            if (!blob.type.includes('application/pdf')) throw new Error('A PDF was not returned. Try again.');
            const url = URL.createObjectURL(blob);
            if (delivery === 'preview' && previewWindow) previewWindow.location.replace(url);
            else {const link = node('a'); link.href = url; link.download = 'lorry-receipts.pdf'; document.body.append(link); link.click(); link.remove();}
            setTimeout(() => URL.revokeObjectURL(url), 300000);
        } catch (failure) {if (previewWindow) previewWindow.close(); error('lr-review-error', failure.message);}
        finally {
            busy = false;
            part('lrReviewModal').querySelectorAll('button, input, select, textarea').forEach(input => {input.disabled = false;});
            part('lr-download').textContent = 'Download PDF';
        }
    }
    part('lr-preview').addEventListener('click', () => generate('preview'));
    part('lr-download').addEventListener('click', () => generate('download'));
    update();
});
