/* One set of package details applies to every box/pallet in a shipment. */
window.PackageContentsEditor = class {
    constructor(root) {
        this.root = root;
        root.innerHTML = `<div class="row g-3 mb-3">
          <div class="col-sm-6"><label class="form-label" for="package-kind">Package type</label><select id="package-kind" class="form-select"><option value="box">Boxes</option><option value="pallet">Pallets</option></select></div>
          <div class="col-sm-6"><label class="form-label" for="package-default">Pieces per box/pallet</label><input id="package-default" class="form-control" type="number" min="1" max="1000000" step="1" placeholder="Unknown" /></div></div>
          <p class="small text-muted package-summary" aria-live="polite"></p>
          <p class="small text-muted">Weight or volume comes from shipment details. Enter one invoice amount per shipment when generating labels. These values appear on every box/pallet label.</p>`;
        this.defaultInput = root.querySelector('#package-default');
    }
    set(value, count) {
        value = value || {};
        this.defaultInput.value = value.default_pieces ?? '';
        this.root.querySelector('#package-kind').value = value.kind || 'box';
        this.resize(count);
    }
    resize(count) {
        this.root.querySelector('.package-summary').textContent = 'Applies to all ' + (Number(count) || 1) + ' boxes/pallets. Leave pieces blank if unknown; labels will show —.';
    }
    read() {
        const raw = this.defaultInput.value;
        if (!this.defaultInput.checkValidity() || (raw !== '' && (!/^\d+$/.test(raw) || Number(raw) < 1 || Number(raw) > 1000000))) {
            throw new Error('Pieces per box/pallet must be a whole number from 1 to 1000000, or blank if unknown.');
        }
        return {kind: this.root.querySelector('#package-kind').value, default_pieces: raw === '' ? null : Number(raw)};
    }
};
