document.addEventListener('DOMContentLoaded', function () {
    'use strict';
    const form = document.getElementById('labels-form');
    if (!form) return;
    const part = id => document.getElementById(id);
    const inputs = Array.from(document.querySelectorAll('[name="consignment_ids"]'));
    const pageRows = JSON.parse(part('label-page-data').textContent);
    const key = 'gram-label-selection:' + form.dataset.scope;
    const idPattern = form.dataset.idFormat === 'uuid' ? /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i : /^[1-9][0-9]*$/;
    const validId = value => typeof value === 'string' && idPattern.test(value);
    const reviewFields = new WeakMap();
    let selected = new Map(), drafts = {}, review = [], job = null, busy = false;
    try {
        const saved = JSON.parse(sessionStorage.getItem(key) || '{}');
        if (Array.isArray(saved.selected)) selected = new Map(saved.selected.filter(row => row && validId(row.id) && Number.isInteger(row.pieces) && row.pieces > 0).map(row => [row.id, row]));
        drafts = saved.drafts && typeof saved.drafts === 'object' ? saved.drafts : {};
        review = Array.isArray(saved.review) ? saved.review : [];
        job = typeof saved.job === 'string' ? saved.job : null;
        if (Array.isArray(saved.selected) && selected.size < saved.selected.length) {
            review = []; job = null;
            const notice = part('label-selection-notice');
            notice.textContent = 'Removed outdated shipment selections. Your current selections and entered details have been kept.';
            notice.classList.remove('d-none');
        }
    } catch (_) { /* In-memory selection still works when storage is disabled. */ }
    pageRows.forEach(row => {if (selected.has(row.id)) selected.set(row.id, row);});
    function persist() {
        try {sessionStorage.setItem(key, JSON.stringify({selected: Array.from(selected.values()), drafts, review, job}));} catch (_) {}
    }
    function update() {
        inputs.forEach(input => {input.checked = selected.has(input.value);});
        const pieces = Array.from(selected.values()).reduce((total, row) => total + row.pieces, 0);
        part('label-selected-count').textContent = selected.size;
        part('label-piece-count').textContent = pieces;
        part('label-limit-error').classList.toggle('d-none', pieces <= 500);
        part('review-labels-btn').disabled = busy || !pieces || pieces > 500;
        const checked = inputs.filter(input => input.checked).length;
        part('select-all-labels').checked = inputs.length > 0 && checked === inputs.length;
        part('select-all-labels').indeterminate = checked > 0 && checked < inputs.length;
        persist();
    }
    function choose(input) {
        if (input.checked) selected.set(input.value, pageRows.find(row => row.id === input.value));
        else selected.delete(input.value);
        job = null; update();
    }
    inputs.forEach(input => input.addEventListener('change', () => choose(input)));
    part('select-all-labels').addEventListener('change', function () {const checked = this.checked; inputs.forEach(input => {input.checked = checked; choose(input);});});
    part('clear-label-selection').addEventListener('click', () => {selected.clear(); drafts = {}; job = null; update();});
    form.addEventListener('submit', event => event.preventDefault());
    function error(id, message) {
        part(id).textContent = message;
        part(id).classList.toggle('d-none', !message);
    }
    function node(tag, className, text) {
        const item = document.createElement(tag); item.className = className || '';
        if (text !== undefined) item.textContent = text;
        return item;
    }
    function field(container, title, input) {
        const wrapper = node('div'), label = node('label', 'form-label', title);
        label.htmlFor = input.id; wrapper.append(label, input); container.append(wrapper);
    }
    function payment(id, value) {
        const input = node('select', 'form-select'); input.id = id; input.dataset.field = 'payment_mode';
        [['pre-paid', 'Pre-paid'], ['to-pay', 'To pay'], ['cash-on-delivery', 'Cash on delivery']].forEach(([code, title]) => {
            const option = node('option', '', title); option.value = code; input.append(option);
        });
        input.value = value; input.required = true; return input;
    }
    function cardValues(card) {
        const fields = reviewFields.get(card);
        const value = {id: fields.id};
        Object.entries(fields.inputs).forEach(([name, input]) => {value[name] = input.type === 'checkbox' ? input.checked : input.value;});
        if (!value.eway_bill_exists) value.eway_bill_number = '';
        return value;
    }
    function cardReady(card) {return Object.values(reviewFields.get(card).inputs).every(input => input.disabled || input.checkValidity());}
    function changed(card) {
        const value = cardValues(card);
        drafts[value.id] = value; job = null; persist();
        const cards = Array.from(part('label-review-rows').children);
        part('label-review-ready').textContent = cards.filter(cardReady).length + ' / ' + cards.length + ' ready';
    }
    function renderReview() {
        const root = part('label-review-rows'); root.replaceChildren();
        review.forEach((row, index) => {
            const data = drafts[row.id] || {invoice_amount: '', payment_mode: 'pre-paid', goods_description: '', eway_bill_exists: false, eway_bill_number: ''};
            const card = node('section', 'label-review-card'); card.dataset.id = row.id; card.dataset.identifier = row.consignment_number;
            card.append(node('h3', '', row.identifier_type + ' · ' + row.consignment_number));
            const snapshot = node('div', 'label-snapshot');
            const volumeMode = row.measurement_mode === 'volume';
            const measurement = (volumeMode ? row.chargeable_volume : row.chargeable_weight) ?? '—';
            [row.pieces + ' boxes', 'Pickup: ' + row.pickup_date, measurement + (volumeMode ? ' m³' : ' kg'), row.company].filter(Boolean).forEach(text => snapshot.append(node('span', '', text)));
            card.append(snapshot);
            const fields = node('div', 'label-review-fields');
            const amount = node('input', 'form-control'); amount.id = 'label-amount-' + index; amount.type = 'number'; amount.min = '0'; amount.max = '999999999.99'; amount.step = '0.01'; amount.required = true; amount.value = data.invoice_amount; amount.dataset.field = 'invoice_amount';
            field(fields, 'Invoice amount (Rs) *', amount);
            const paymentInput = payment('label-payment-' + index, data.payment_mode);
            field(fields, 'Payment mode *', paymentInput);
            const description = node('input', 'form-control'); description.id = 'label-goods-' + index; description.maxLength = 160; description.required = true; description.value = data.goods_description; description.dataset.field = 'goods_description';
            field(fields, 'Goods description *', description); card.append(fields);
            const names = node('div', 'label-review-names'), nameInputs = {};
            [['sender_name', 'Sender name (optional)'], ['receiver_name', 'Receiver name (optional)']].forEach(([key, title]) => {
                const input = node('input', 'form-control'); input.type = 'text'; input.id = 'label-' + key + '-' + index;
                input.maxLength = 120; input.value = data[key] || ''; input.dataset.field = key;
                input.placeholder = 'Leave blank to print the address only';
                field(names, title, input); nameInputs[key] = input;
            });
            card.append(names);
            const eway = node('div', 'label-eway'), label = node('label', 'd-flex align-items-center gap-2 small');
            const exists = node('input'); exists.type = 'checkbox'; exists.checked = data.eway_bill_exists; exists.dataset.field = 'eway_bill_exists';
            label.append(exists, document.createTextNode('EWBN exists'));
            const number = node('input', 'form-control'); number.id = 'label-eway-' + index; number.type = 'text'; number.inputMode = 'numeric'; number.pattern = '[0-9]{12}'; number.maxLength = 12; number.value = data.eway_bill_number; number.dataset.field = 'eway_bill_number'; number.setAttribute('aria-label', 'EWBN for ' + row.consignment_number); number.placeholder = '12-digit EWBN';
            function toggleEway() {number.disabled = !exists.checked; number.required = exists.checked; number.classList.toggle('d-none', !exists.checked);}
            toggleEway(); exists.addEventListener('change', toggleEway);
            eway.append(label, number); card.append(eway); root.append(card);
            reviewFields.set(card, {id: row.id, inputs: {invoice_amount: amount, payment_mode: paymentInput,
                goods_description: description, eway_bill_exists: exists, eway_bill_number: number, ...nameInputs}});
            card.addEventListener('input', () => changed(card)); card.addEventListener('change', () => changed(card));
        });
        const pieces = review.reduce((sum, row) => sum + row.pieces, 0);
        part('label-review-summary').textContent = review.length + ' shipments · ' + pieces + ' piece labels';
        const cards = Array.from(root.children);
        part('label-review-ready').textContent = cards.filter(cardReady).length + ' / ' + cards.length + ' ready';
        part('label-review-search').value = ''; error('label-review-error', '');
    }
    part('label-review-search').addEventListener('input', function () {
        const query = this.value.trim().toLowerCase();
        Array.from(part('label-review-rows').children).forEach(card => {card.hidden = !card.dataset.identifier.toLowerCase().includes(query);});
    });
    part('apply-label-common').addEventListener('click', () => {
        const description = part('label-common-description').value.trim();
        Array.from(part('label-review-rows').children).forEach(card => {
            card.querySelector('[data-field=payment_mode]').value = part('label-common-payment').value;
            if (description) card.querySelector('[data-field=goods_description]').value = description;
            changed(card);
        });
    });
    part('review-labels-btn').addEventListener('click', async () => {
        busy = true; update(); error('labels-error', '');
        try {
            const response = await fetch(form.dataset.reviewUrl, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({consignment_ids: Array.from(selected.keys())})});
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || 'Could not load selected shipments.');
            if (JSON.stringify(review) !== JSON.stringify(result.shipments)) job = null;
            review = result.shipments; review.forEach(row => selected.set(row.id, row)); renderReview();
            bootstrap.Modal.getOrCreateInstance(part('labelReviewModal')).show();
        } catch (failure) {error('labels-error', failure.message);}
        finally {busy = false; update();}
    });
    function setBusy(value) {
        busy = value;
        part('labelReviewModal').querySelectorAll('button, input, select').forEach(input => {
            if (input.dataset.field === 'eway_bill_number' && !input.closest('.label-review-card').querySelector('[data-field=eway_bill_exists]').checked) return;
            input.disabled = value;
        });
        part('download-labels-btn').textContent = value ? 'Generating…' : 'Download PDF'; update();
    }
    part('labelReviewModal').addEventListener('hide.bs.modal', event => {if (busy) event.preventDefault();});
    async function generate(delivery) {
        if (busy) return;
        const cards = Array.from(part('label-review-rows').children);
        const invalid = cards.find(card => !cardReady(card));
        if (invalid) {
            part('label-review-search').value = ''; cards.forEach(card => {card.hidden = false;});
            const field = Object.values(reviewFields.get(invalid).inputs).find(input => !input.disabled && !input.checkValidity());
            error('label-review-error', 'Complete the details for ' + invalid.dataset.identifier + ' before generating.');
            field.focus(); field.reportValidity(); return;
        }
        let previewWindow = null;
        if (delivery === 'preview') {previewWindow = window.open('', '_blank'); if (previewWindow) {previewWindow.opener = null; previewWindow.document.title = 'Generating GRAM SCS labels';}}
        job = job || crypto.randomUUID();
        persist();
        const payload = {job_id: job, consignment_ids: review.map(row => row.id), label_details: cards.map(cardValues), barcode: part('include-label-barcode').checked ? '1' : '0', delivery};
        setBusy(true); error('label-review-error', '');
        try {
            const response = await fetch(form.action, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/pdf'}, body: JSON.stringify(payload)});
            if (!response.ok) {
                const result = await response.json();
                if (response.status === 409) job = null;
                throw new Error(result.message || 'Labels could not be generated.');
            }
            const blob = await response.blob();
            if (!blob.type.includes('application/pdf') || blob.size < 100) throw new Error('The PDF response was incomplete. Try again.');
            const url = URL.createObjectURL(blob);
            if (delivery === 'preview' && previewWindow) previewWindow.location.replace(url);
            else {const link = node('a'); link.href = url; link.download = 'b2b-shipping-labels.pdf'; document.body.append(link); link.click(); link.remove();}
            setTimeout(() => URL.revokeObjectURL(url), 300000);
        } catch (failure) {if (previewWindow) previewWindow.close(); error('label-review-error', failure.message);}
        finally {setBusy(false);}
    }
    part('preview-labels-btn').addEventListener('click', () => generate('preview'));
    part('download-labels-btn').addEventListener('click', () => generate('download'));
    update();
});
