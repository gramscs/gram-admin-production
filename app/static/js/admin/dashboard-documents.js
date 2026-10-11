(function () {
    'use strict';
    const source = document.getElementById('missing-document-data');
    if (!source) return;
    const rows = JSON.parse(source.textContent);
    const body = document.getElementById('missing-document-rows');
    const previous = document.getElementById('missing-document-prev');
    const next = document.getElementById('missing-document-next');
    const buttons = document.querySelectorAll('[data-missing-filter]');
    let mode = 'any', page = 1;
    const size = 25;
    function cell(text) {
        const td = document.createElement('td');
        td.textContent = text;
        return td;
    }
    function render() {
        const matching = rows.filter(row => mode === 'pod' ? row.missing_pod : mode === 'invoice' ? row.missing_invoice : mode === 'both' ? row.missing_pod && row.missing_invoice : true);
        const pages = Math.max(1, Math.ceil(matching.length / size));
        page = Math.min(page, pages);
        body.replaceChildren();
        matching.slice((page - 1) * size, page * size).forEach(row => {
            const tr = document.createElement('tr');
            const identifier = cell('');
            const strong = document.createElement('strong'); strong.textContent = row.identifier;
            identifier.append(strong);
            const documents = cell('');
            [['POD', row.missing_pod], ['Invoice', row.missing_invoice]].forEach(([name, missing]) => {
                if (!missing) return;
                const badge = document.createElement('span'); badge.className = 'missing-doc-badge'; badge.textContent = name;
                documents.append(badge);
            });
            const action = cell('');
            const link = document.createElement('a');
            const url = new URL(body.dataset.documentsUrl, window.location.origin);
            url.search = new URLSearchParams({search: row.identifier, identifier_type: row.identifier_type, company: row.company_id}).toString();
            link.href = url.toString(); link.textContent = 'Open Documents'; action.append(link);
            tr.append(identifier, cell(row.identifier_type), cell(row.company), cell(row.status), documents, action);
            body.append(tr);
        });
        if (!matching.length) {
            const tr = document.createElement('tr'), td = cell(mode === 'any' ? 'All PODs and invoices are uploaded in this view.' : 'No shipments match this missing-document filter.');
            td.colSpan = 6; td.className = 'followup-empty'; tr.append(td); body.append(tr);
        }
        document.getElementById('missing-document-count').textContent = matching.length ? `${(page - 1) * size + 1}–${Math.min(page * size, matching.length)} of ${matching.length} shipments` : '0 shipments';
        document.getElementById('missing-document-page').textContent = `Page ${page} of ${pages}`;
        previous.disabled = page === 1;
        next.disabled = page === pages;
        buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.missingFilter === mode)));
    }
    buttons.forEach(button => button.addEventListener('click', () => { mode = button.dataset.missingFilter; page = 1; render(); }));
    previous.addEventListener('click', () => { page--; render(); });
    next.addEventListener('click', () => { page++; render(); });
    render();
})();
