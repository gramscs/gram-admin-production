// Run with: node --test tests/unit/lr-generator-upload.cjs
// Exercise the generator's real event handlers with an in-memory DOM and transport.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const path = require('node:path');

class Element {
    constructor() {
        this.children = []; this.dataset = {}; this.listeners = {}; this.textContent = '';
        const classes = new Set();
        this.classList = {add: value => classes.add(value), remove: value => classes.delete(value),
            toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
            contains: value => classes.has(value)};
    }
    addEventListener(type, handler) {this.listeners[type] = handler;}
    append(...items) {this.children.push(...items);}
    replaceChildren() {this.children = [];}
    setAttribute() {}
    querySelectorAll(selector) {
        const children = this.children.flatMap(child => [child, ...child.querySelectorAll(selector)]);
        return selector === '[data-field]' ? children.filter(child => child.dataset.field) : children;
    }
    reportValidity() {return true;}
}

function setup() {
    const elements = new Map(), requests = [], events = [], previews = [];
    const part = id => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    const rows = ['1', '2'].map(id => ({id, consignment_number: 'SHIP-' + id,
        values: {}, sources: [], lr_file: id === '1' ? 'supabase:lr-uploads/previous.pdf' : null}));
    part('lr-workspace').dataset = {scope: 'admin', idFormat: 'integer', reviewUrl: '/review', generateUrl: '/generate'};
    part('lr-page-data').textContent = JSON.stringify(rows);
    part('lr-field-data').textContent = '[]';
    const content = id => Buffer.from('%PDF-' + id).toString('base64');
    let failSecond = true;
    const document = {
        getElementById: part, querySelectorAll: () => [], createElement: () => new Element(),
        addEventListener: (type, handler) => {if (type === 'DOMContentLoaded') handler();},
        dispatchEvent: event => events.push(event),
    };
    runInNewContext(readFileSync(path.join(__dirname, '../../app/static/js/admin/lr-generator.js'), 'utf8'), {
        document, Blob, FormData, Uint8Array, atob,
        sessionStorage: {getItem: () => JSON.stringify({selected: rows}), setItem() {}},
        bootstrap: {Modal: {getOrCreateInstance: () => ({show() {}})}},
        window: {open: () => ({document: {}, location: {replace: url => previews.push(url)}, close() {}})},
        URL: {createObjectURL: () => 'blob:preview', revokeObjectURL() {}}, setTimeout() {},
        CustomEvent: class {constructor(type, init) {this.type = type; this.detail = init.detail;}},
        fetch: async (url, options) => {
            requests.push({url, ...options});
            if (url === '/review') return {ok: true, json: async () => ({success: true, shipments: rows})};
            if (url === '/generate') return {ok: true, json: async () => ({success: true,
                preview_pdf: content('batch'), documents: rows.map(row => ({id: row.id,
                    filename: row.consignment_number + '_lr.pdf', pdf: content(row.id)}))})};
            if (url === '/admin/consignments/2/lr' && failSecond) {
                failSecond = false;
                return {ok: false, json: async () => ({success: false, message: 'Storage unavailable'})};
            }
            return {ok: true, json: async () => ({success: true, lr_file: 'supabase:lr-uploads/' + url.split('/')[3] + '.pdf'})};
        },
    });
    return {part, requests, events, previews, content};
}

test('Upload LR uses standard multipart uploads; retry skips completed shipments', async () => {
    const {part, requests, events, previews, content} = setup();
    await part('lr-review').listeners.click();
    assert.equal(part('lr-upload').disabled, true);
    await part('lr-preview').listeners.click();
    assert.deepEqual(previews, ['blob:preview']);
    assert.equal(part('lr-upload').disabled, false);
    assert.equal(requests.filter(item => item.url.includes('/consignments/')).length, 0);

    await part('lr-upload').listeners.click();
    assert.match(part('lr-review-error').textContent, /1 of 2.*Storage unavailable/);
    assert.equal(part('lr-upload').disabled, false);
    await part('lr-upload').listeners.click();
    const uploads = requests.filter(item => item.url.includes('/consignments/'));
    assert.deepEqual(uploads.map(item => item.url), ['/admin/consignments/1/lr', '/admin/consignments/2/lr', '/admin/consignments/2/lr']);
    for (const upload of uploads) {
        const id = upload.url.split('/')[3], file = upload.body.get('file');
        assert.equal(upload.method, 'POST');
        assert.equal(upload.body.get('expected_reference'), id === '1' ? 'supabase:lr-uploads/previous.pdf' : '');
        assert.equal(file.name, 'SHIP-' + id + '_lr.pdf');
        assert.equal(file.type, 'application/pdf');
        assert.equal(Buffer.from(await file.arrayBuffer()).toString('base64'), content(id));
    }
    assert.equal(events.length, 2);
    assert.equal(events[0].type, 'shipment-documents-updated');
    assert.equal(events[0].detail[0].kind, 'lr');
    assert.equal(events[1].detail[0].reference, 'supabase:lr-uploads/2.pdf');
    assert.equal(part('lr-upload').disabled, true);
    assert.match(part('lr-upload-status').textContent, /2 LR document\(s\) uploaded/);
});

test('Editing LR details requires another preview before upload', async () => {
    const {part, requests} = setup();
    await part('lr-review').listeners.click();
    await part('lr-preview').listeners.click();
    assert.equal(part('lr-upload').disabled, false);
    part('lr-review-rows').children[0].listeners.input();
    assert.equal(part('lr-upload').disabled, true);
    await part('lr-upload').listeners.click();
    assert.equal(requests.filter(item => item.url.includes('/consignments/')).length, 0);
});
