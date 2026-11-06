// A serialized save queue that acknowledges only the exact row version sent.
(function (root) {
    'use strict';
    root.createShipmentAutosave = function (options) {
        let timer = null, running = null, changedWhileSaving = false;
        const delay = options.delay ?? 800;
        function schedule() {
            if (running) changedWhileSaving = true;
            clearTimeout(timer);
            timer = setTimeout(() => flush(false), delay);
            options.pendingChanged?.();
        }
        function flush(manual) {
            clearTimeout(timer); timer = null;
            if (running) return running;
            if (options.paused?.()) return Promise.resolve(false);
            const rows = [], invalid = [];
            for (const row of options.pending.values()) {
                const error = options.validate(row);
                if (error) invalid.push(error);
                else rows.push(JSON.parse(JSON.stringify(row)));
            }
            if (!rows.length) {
                if (invalid.length) options.invalid?.(invalid, manual);
                else if (manual) options.empty?.();
                return Promise.resolve(!invalid.length);
            }
            rows.sort((a,b) => String(a.id).localeCompare(String(b.id)));
            rows.splice(options.batchSize || 100);
            const versions = new Map(rows.map(row => [String(row.id), options.version?.(String(row.id)) || 0]));
            changedWhileSaving = false;
            options.start?.(manual);
            let success = false;
            // Defer send until running is assigned, including when send throws.
            running = Promise.resolve().then(() => options.send(rows)).then(result => {
                if (!result || !result.success) throw new Error(result?.message || 'Save failed.');
                for (const sent of rows) {
                    const key = String(sent.id), current = options.pending.get(key);
                    if (current && (options.version?.(key) || 0) === versions.get(key) && JSON.stringify(current) === JSON.stringify(sent)) {
                        options.pending.delete(key);
                        options.acknowledged?.(key);
                    }
                }
                success = true;
                options.success?.(result, options.pending.size, manual);
                if (invalid.length) options.invalid?.(invalid, manual);
                return true;
            }).catch(error => {
                options.error?.(error, manual);
                return false; // Pending edits remain available for a manual retry.
            }).finally(() => {
                running = null;
                options.finish?.();
                if (changedWhileSaving || (success && options.pending.size)) schedule();
            });
            return running;
        }
        return {schedule, flush, idle: () => running || Promise.resolve(),
            busy: () => !!running, cancelTimer: () => { clearTimeout(timer); timer = null; }};
    };
})(typeof window !== 'undefined' ? window : globalThis);
