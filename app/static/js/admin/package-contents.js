/* Sparse box contents, with paged editing for large B2B shipments. */
window.PackageContentsEditor = class {
    constructor(root) {
        this.root = root;
        this.page = 0;
        root.innerHTML = `<div class="row g-3 mb-3">
          <div class="col-sm-4"><label class="form-label" for="package-kind">Package type</label><select id="package-kind" class="form-select"><option value="box">Boxes</option><option value="pallet">Pallets</option></select></div>
          <div class="col-sm-4"><label class="form-label" for="package-default">Default pieces per box/pallet</label><input id="package-default" class="form-control" type="number" min="1" max="1000000" step="1" placeholder="Unknown" /></div>
          <div class="col-sm-4 d-flex align-items-end"><p class="small text-muted mb-0">The default fills every box. Enter different counts, weights and handling notes below for exceptions.</p></div></div>
          <p class="small text-muted">Empty piece counts use the default. Box weight defaults to the shipment weight; enter an exception only when a box differs. Notes print only on the corresponding label.</p>
          <div class="package-removed alert alert-warning" hidden>Some saved entries exceed the new box count. <button type="button" class="btn btn-sm btn-outline-danger">Remove those entries</button></div>
          <div class="table-responsive"><table class="table align-middle"><thead><tr><th>Box / pallet</th><th>Pieces inside</th><th>Weight exception (kg)</th><th>Exception / handling note</th></tr></thead><tbody></tbody></table></div>
          <div class="d-flex align-items-center gap-3"><button type="button" class="btn btn-outline-secondary package-prev">Previous</button><span class="package-page small" aria-live="polite"></span><button type="button" class="btn btn-outline-secondary package-next">Next</button></div>`;
        this.defaultInput = root.querySelector('#package-default');
        root.querySelector('.package-prev').onclick = () => { this.page--; this.render(); };
        root.querySelector('.package-next').onclick = () => { this.page++; this.render(); };
        root.querySelector('.package-removed button').onclick = () => {
            Object.keys(this.boxes).forEach(key => { if (Number(key) > this.count) delete this.boxes[key]; }); this.render();
        };
        this.defaultInput.oninput = () => this.render();
    }
    set(value, count, defaultWeight) {
        value = value || {};
        this.boxes = JSON.parse(JSON.stringify(value.boxes || {}));
        this.defaultInput.value = value.default_pieces ?? '';
        this.defaultWeight = defaultWeight ?? '';
        this.root.querySelector('#package-kind').value = value.kind || 'box';
        this.page = 0; this.resize(count);
    }
    setDefaultWeight(value) { this.defaultWeight = value ?? ''; this.render(); }
    resize(count) { this.count = Number(count) || 1; this.page = Math.min(this.page, Math.max(0, Math.ceil(this.count / 25) - 1)); this.render(); }
    render() {
        const body = this.root.querySelector('tbody'); body.replaceChildren();
        const start = this.page * 25 + 1, end = Math.min(this.count, start + 24);
        for (let n = start; n <= end; n++) {
            const entry = this.boxes[n] || {}, tr = document.createElement('tr');
            const heading = document.createElement('th'); heading.scope = 'row'; heading.textContent = n + ' of ' + this.count;
            const cell = document.createElement('td'), input = document.createElement('input');
            input.type = 'number'; input.min = '1'; input.max = '1000000'; input.step = '1'; input.className = 'form-control';
            input.value = entry.pieces ?? ''; input.placeholder = Object.hasOwn(entry, 'pieces') && entry.pieces === null ? 'Unknown' : this.defaultInput.value || 'Unknown';
            input.setAttribute('aria-label', 'Pieces in box ' + n);
            input.oninput = () => { this.boxes[n] = {...(this.boxes[n] || {}), pieces: input.validity.badInput ? 'invalid' : input.value || undefined}; };
            const weightCell = document.createElement('td'), weight = document.createElement('input');
            weight.type = 'number'; weight.min = '0'; weight.max = '999999999.999'; weight.step = '0.001'; weight.className = 'form-control';
            weight.value = entry.weight ?? '';
            weight.placeholder = this.defaultWeight !== '' && this.defaultWeight != null ? this.defaultWeight : 'Shipment weight';
            weight.setAttribute('aria-label', 'Weight exception in kg for box ' + n);
            weight.oninput = () => { this.boxes[n] = {...(this.boxes[n] || {}), weight: weight.validity.badInput ? 'invalid' : weight.value || undefined}; };
            const noteCell = document.createElement('td'), note = document.createElement('input');
            note.type = 'text'; note.maxLength = 120; note.className = 'form-control'; note.value = entry.note || '';
            note.placeholder = 'e.g. Fragile — keep upright'; note.setAttribute('aria-label', 'Exception for box ' + n);
            note.oninput = () => { this.boxes[n] = {...(this.boxes[n] || {}), note: note.value}; };
            cell.append(input); weightCell.append(weight); noteCell.append(note); tr.append(heading, cell, weightCell, noteCell); body.append(tr);
        }
        this.root.querySelector('.package-page').textContent = 'Boxes/pallets ' + start + '–' + end + ' of ' + this.count;
        this.root.querySelector('.package-prev').disabled = this.page === 0;
        this.root.querySelector('.package-next').disabled = end >= this.count;
        this.root.querySelector('.package-removed').hidden = !Object.keys(this.boxes).some(key => Number(key) > this.count);
    }
    read() {
        const parse = raw => {
            if (raw === '' || raw == null) return null;
            if (!/^\d+$/.test(String(raw)) || Number(raw) < 1 || Number(raw) > 1000000) throw new Error('Pieces per box/pallet must be a whole number from 1 to 1000000.');
            return Number(raw);
        };
        const defaultPieces = parse(this.defaultInput.value), boxes = {};
        if (!this.defaultInput.checkValidity()) throw new Error('Enter a valid default pieces count.');
        for (const [key, entry] of Object.entries(this.boxes)) {
            if (Number(key) > this.count) throw new Error('Review and remove entries beyond the new box count in Box contents.');
            const pieces = entry.pieces === undefined ? defaultPieces : parse(entry.pieces), note = (entry.note || '').trim();
            const weight = entry.weight === undefined ? null : Number(entry.weight);
            if (entry.weight === 'invalid' || (weight !== null && (!Number.isFinite(weight) || weight < 0 || weight > 999999999.999 || !/^\d+(?:\.\d{0,3})?$/.test(String(entry.weight))))) {
                throw new Error('Box weight must be non-negative with up to 3 decimal places.');
            }
            if (/[\x00-\x1f\x7f]/.test(note)) throw new Error('Box notes must be a single line.');
            if (pieces !== defaultPieces || note || weight !== null) {
                boxes[key] = {pieces, note};
                if (weight !== null) boxes[key].weight = Number(weight.toFixed(3)).toFixed(3);
            }
        }
        return {kind: this.root.querySelector('#package-kind').value, default_pieces: defaultPieces, boxes};
    }
};
