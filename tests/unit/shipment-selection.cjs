const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const path = require('node:path');
const script = name => readFileSync(path.join(__dirname, '../../app/static/js/admin/', name), 'utf8');

function setup(fetch) {
    const events = {};
    let reloads = 0;
    const window = {addEventListener: (event, callback) => {events[event] = callback;},
        location: {reload() {reloads++;}}};
    runInNewContext(script('shipment-selection.js'), {window, fetch});
    return {window, events, reloads: () => reloads};
}

test('back navigation reloads stale pages; ordinary loads do not loop', () => {
    const state = setup();
    state.events.pageshow({persisted: false});
    assert.equal(state.reloads(), 0);
    state.events.pageshow({persisted: true});
    assert.equal(state.reloads(), 1);
});

test('revalidation keeps active selections across pages and removes archived IDs', async () => {
    const {window} = setup(async () => ({ok: true, json: async () => ({success: true, consignment_ids: ['1', '3']})}));
    const selected = new Map(['1', '2', '3'].map(id => [id, {id}]));
    assert.equal(await window.shipmentSelection.prune('/selection', selected), true);
    assert.deepEqual([...selected.keys()], ['1', '3']);
});

test('failed revalidation preserves drafts and selections for retry', async () => {
    const {window} = setup(async () => ({ok: false, json: async () => ({success: false})}));
    const selected = new Map([['1', {id: '1'}]]);
    await assert.rejects(window.shipmentSelection.prune('/selection', selected));
    assert.deepEqual([...selected.keys()], ['1']);
});

for (const kind of ['lr', 'label']) {
    test(kind + ' page removes archived saved selections on load and on focus', async () => {
        const elements = new Map(), store = new Map();
        class Element {
            constructor() {
                this.dataset = {}; this.children = []; this.listeners = {};
                this.classList = {toggle() {}, add() {}, remove() {}};
            }
            addEventListener(event, callback) {this.listeners[event] = callback;}
            append(...children) {this.children.push(...children);}
            replaceChildren() {this.children = [];}
            setAttribute() {}
        }
        const part = id => {
            if (!elements.has(id)) elements.set(id, new Element());
            return elements.get(id);
        };
        const saved = ['1', '2', '3'].map(id => ({id, consignment_number: 'SHIP-' + id, pieces: 1}));
        const root = part(kind === 'lr' ? 'lr-workspace' : 'labels-form');
        root.dataset = {selectionUrl: '/selection', scope: 'admin', idFormat: 'integer'};
        part(kind + '-page-data').textContent = JSON.stringify([saved[0]]);
        part('lr-field-data').textContent = '[]';
        const key = kind === 'lr' ? 'gram-lr-selection:admin:integer' : 'gram-label-selection:admin';
        store.set(key, JSON.stringify({selected: saved, drafts: {'2': {invoice_number: 'stale'}, '3': {invoice_number: 'keep'}}}));
        let active = ['1', '3'], hides = 0;
        const fetch = async () => ({ok: true, json: async () => ({success: true, consignment_ids: active})});
        const {window, events} = setup(fetch);
        runInNewContext(script(kind === 'lr' ? 'lr-generator.js' : 'labels.js'), {
            window, fetch, bootstrap: {Modal: {getOrCreateInstance: () => ({hide() {hides++;}})}},
            sessionStorage: {getItem: key => store.get(key), setItem: (key, value) => store.set(key, value)},
            document: {getElementById: part, querySelectorAll: () => [], createElement: () => new Element(),
                addEventListener: (event, callback) => callback()},
        });
        await new Promise(resolve => setImmediate(resolve));
        let state = JSON.parse(store.get(key));
        assert.deepEqual(state.selected.map(row => row.id), ['1', '3']);
        assert.equal(state.drafts['2'], undefined);
        assert.equal(state.drafts['3'].invoice_number, 'keep');
        assert.equal(hides, 1);
        active = ['1'];
        await events.focus();
        state = JSON.parse(store.get(key));
        assert.deepEqual(state.selected.map(row => row.id), ['1']);
    });
}
