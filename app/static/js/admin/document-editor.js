(function () {
    'use strict';
    const modal = document.getElementById('documentEditorModal');
    if (!modal) return;
    const part = name => document.getElementById('document-editor-' + name);
    const fileInput = part('file'), save = part('save'), image = part('image'), icon = part('icon');
    let editor = null, previewURL = null;
    function revoke() { if (previewURL) URL.revokeObjectURL(previewURL); previewURL = null; }
    function render() {
        if (!editor) return;
        revoke();
        const attached = !editor.remove && (editor.file || editor.reference);
        part('status').textContent = editor.remove ? 'Removal staged. Save changes to delete this document and its saved versions.' : editor.file ? 'Selected: ' + editor.file.name + ' · awaiting save' : editor.reference ? 'Attached: ' + editor.name : 'No document attached.';
        part('error').textContent = editor.error;
        part('error').classList.toggle('d-none', !editor.error);
        part('download').classList.toggle('d-none', !editor.reference);
        part('remove').classList.toggle('d-none', !attached);
        part('undo').classList.toggle('d-none', !editor.changed && !editor.error);
        image.classList.add('d-none'); image.removeAttribute('src'); icon.className = 'fa fa-file-o';
        if (attached && (editor.file ? editor.mime.startsWith('image/') : /\.(png|jpe?g|webp)$/i.test(editor.reference))) {
            image.src = editor.file ? (previewURL = URL.createObjectURL(editor.file)) : editor.url + '?preview=1';
            image.classList.remove('d-none'); icon.classList.add('d-none');
        } else if (attached) { icon.className = 'fa fa-file-pdf-o'; }
        fileInput.disabled = editor.busy;
        modal.querySelectorAll('[data-bs-dismiss], #document-editor-remove, #document-editor-undo').forEach(button => {button.disabled = editor.busy;});
        save.disabled = editor.busy || editor.pending || !editor.changed || !!editor.error;
        save.textContent = editor.busy ? 'Saving…' : 'Save changes';
        if (editor.error && modal.classList.contains('show')) part('error').scrollIntoView({block: 'nearest'});
    }
    function reset() {
        editor.sequence++; editor.file = null; editor.remove = false; editor.changed = false;
        editor.error = ''; editor.pending = false; fileInput.value = ''; render();
    }
    async function choose() {
        const current = editor, sequence = ++current.sequence, file = fileInput.files[0];
        if (!file) return;
        current.pending = true; current.error = ''; render();
        try {
            if (!file.size || file.size > 5 * 1024 * 1024) throw new Error('Choose a non-empty document of at most 5 MB.');
            const extension = file.name.split('.').pop().toLowerCase();
            const types = {pdf: 'application/pdf', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp'};
            if (!types[extension]) throw new Error('Use PDF, JPG, PNG or WebP.');
            const bytes = new Uint8Array(await file.slice(0, 16).arrayBuffer());
            const text = String.fromCharCode(...bytes);
            const mime = text.startsWith('%PDF-') ? 'application/pdf' : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 ? 'image/jpeg' : bytes[0] === 137 && text.slice(1, 4) === 'PNG' ? 'image/png' : text.startsWith('RIFF') && text.slice(8, 12) === 'WEBP' ? 'image/webp' : '';
            if (mime !== types[extension]) throw new Error('The filename does not match the document contents.');
            if (editor !== current || sequence !== current.sequence) return;
            current.file = file; current.mime = mime; current.remove = false; current.changed = true;
        } catch (error) {
            if (editor !== current || sequence !== current.sequence) return;
            current.error = error.message;
        }
        if (editor === current && sequence === current.sequence) {current.pending = false; render();}
    }
    fileInput.addEventListener('change', choose);
    image.addEventListener('error', () => {image.classList.add('d-none'); icon.classList.remove('d-none');});
    part('remove').addEventListener('click', () => {
        editor.sequence++; editor.file = null; editor.remove = true; editor.changed = true;
        editor.pending = false; editor.error = ''; fileInput.value = ''; render();
    });
    part('undo').addEventListener('click', reset);
    part('download').addEventListener('click', async () => {
        try {await window.adminReportDownloads.download(editor.url);}
        catch (error) {editor.error = error.message; render();}
    });
    modal.addEventListener('hide.bs.modal', event => {if (editor && editor.busy) event.preventDefault();});
    modal.addEventListener('hidden.bs.modal', () => {revoke(); if (editor) editor.sequence++; editor = null; fileInput.value = '';});
    save.addEventListener('click', async () => {
        if (!editor || save.disabled) return;
        const current = editor, controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 60000);
        current.busy = true; current.error = ''; render();
        try {
            const headers = {Accept: 'application/json'};
            let body;
            if (current.remove) {
                headers['Content-Type'] = 'application/json'; body = JSON.stringify({expected_reference: current.reference});
            } else {
                body = new FormData(); body.append('file', current.file);
                body.append('expected_reference', current.reference || '');
            }
            const response = await fetch(current.url, {method: current.remove ? 'DELETE' : 'POST', headers, body, signal: controller.signal});
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || result.error || 'Document could not be saved.');
            current.busy = false; render(); bootstrap.Modal.getOrCreateInstance(modal).hide();
            await current.onSaved(current.kind.toUpperCase() + (current.remove ? ' removed.' : ' saved.'));
        } catch (error) {
            current.error = error.name === 'AbortError' ? 'The request timed out. Reload Documents to check whether it saved before trying again.' : error.message;
        } finally {
            clearTimeout(timeout); current.busy = false; if (editor === current) render();
        }
    });
    window.shipmentDocumentEditor = {
        open(row, kind, onSaved) {
            if (editor && editor.busy) return;
            editor = {kind, reference: row[kind === 'pod' ? 'pod_image' : 'invoice_file'] || null,
                name: row[kind + '_original_name'] || 'Attached ' + kind.toUpperCase(),
                url: '/admin/consignments/' + encodeURIComponent(row.id) + '/' + kind,
                file: null, remove: false, changed: false, error: '', pending: false, busy: false, sequence: 0, onSaved};
            fileInput.value = '';
            part('title').textContent = 'Manage ' + kind.toUpperCase();
            part('shipment').textContent = row.consignment_number + ' · ' + (row.identifier_type || 'LRN');
            part('file-label').textContent = editor.reference ? 'Replace document' : 'Upload document';
            render(); bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    };
})();
