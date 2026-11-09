// Run against tests/browser/serve.py only; never a remote app.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
    const base = 'http://127.0.0.1:10003';
    const browser = await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
    const page = await browser.newPage();
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    try {
        await page.goto(base+'/admin/login');
        await page.locator('[name=email]').fill('admin@example.test');
        await page.locator('[name=password]').fill('system-test-only');
        await page.locator('button[type=submit]').click();
        await page.waitForURL('**/admin/dashboard');
        await page.goto(base+'/admin/consignments');
        await page.locator('#add-row-btn').click();
        await page.locator('#editConsignmentModal.show').waitFor();
        await page.waitForFunction(()=>document.querySelector('#modal-location-id').options.length>1);
        assert.equal(await page.locator('#modal-consignment-number').getAttribute('readonly'),'');
        await page.locator('#modal-location-id').selectOption({label:'HQ — Test HQ'});
        await page.locator('#modal-status').selectOption('In Transit');
        await page.locator('#modal-pickup-tag').fill('LR browser test');
        // Lose the response after commit, then verify retry recovers the same LR.
        let committed;
        await page.route('**/admin/consignments/save', async route => {
            const backend = await route.fetch();
            committed = (await backend.json()).shipments[0];
            await route.abort();
        }, {times:1});
        await page.locator('#modal-save-btn').click();
        await page.locator('#shipment-form-error:not(.d-none)').waitFor();
        assert.equal(await page.locator('#editConsignmentModal.show').count(),1);
        const saved=page.waitForResponse(r=>r.url().endsWith('/consignments/save')&&r.request().method()==='POST');
        await page.locator('#modal-save-btn').click();
        await page.locator('#editConsignmentModal').waitFor({state:'hidden'});
        const response=await saved;
        assert.equal(response.status(),200,await response.text());
        const data=await response.json();
        assert.match(data.shipments[0].lr_number,/^HQ\d{10}$/);
        assert.equal(data.shipments[0].lr_number,committed.lr_number);
        await page.locator('#search-input').fill(data.shipments[0].lr_number);
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='1');
        assert.equal(await page.locator('#sheet-body .consignment_number').getAttribute('readonly'),'');
        page.on('dialog',async d=>{assert.equal(d.type(),'confirm','Cancellation must not prompt for a reason');await d.accept();});
        const cancelled=page.waitForResponse(r=>r.url().endsWith('/cancel'));
        await page.locator('.delete-row').click();
        assert.equal((await cancelled).status(),200);
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='0');
        await page.locator('#archive-filter').selectOption('archived');
        await page.locator('#sheet-body .shipment-cancelled').waitFor();
        assert.equal(await page.locator('.archive-row').count(),0);
        assert.equal(await page.locator('#sheet-body strong.text-danger').count(),0);
        assert.equal(await page.locator('#sheet-body .consignment_number').inputValue(),data.shipments[0].lr_number);
        await page.locator('.lr-history-row').click();
        await page.locator('dialog[open]').waitFor();
        assert.match(await page.locator('dialog').innerText(),/Cancelled by administrator/);
        await page.locator('dialog button').click();
        // A separate, non-cancelled shipment can still be archived and restored.
        const restorable = await page.evaluate(async () => {
            const locations = await window.lrControl.locations;
            const response = await window.lrControl.post('/admin/consignments/save', {
                idempotency_key: crypto.randomUUID(), rows: [{location_id: locations[0].id, status: 'In Transit'}],
            });
            return response.shipments[0];
        });
        await page.goto(base+'/admin/consignments?search='+restorable.lr_number);
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='1');
        const archived=page.waitForResponse(r=>r.url().endsWith('/archive'));
        await page.locator('.archive-row').click();
        assert.equal((await archived).status(),200);
        for (const path of ['/admin/labels', '/admin/lr-generator']) {
            const response = await page.request.get(base+path);
            assert.equal(response.status(),200);
            const html = await response.text();
            assert.ok(!html.includes(data.shipments[0].lr_number), 'Cancelled shipments must be hidden');
            assert.ok(!html.includes(restorable.lr_number), 'Archived shipments must be hidden');
        }
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='0');
        await page.locator('#archive-filter').selectOption('archived');
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='1');
        assert.equal(await page.locator('.archive-row').textContent(),'Restore');
        const restored=page.waitForResponse(r=>r.url().endsWith('/restore'));
        await page.locator('.archive-row').click();
        assert.equal((await restored).status(),200);
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='0');
        await page.locator('#archive-filter').selectOption('active');
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='1');

        await page.locator('#data-tools-btn').click();
        await page.locator('[data-bs-target="#importConsignmentsModal"]').click();
        await page.locator('#import-location-code').selectOption('HQ');
        await page.locator('#import-file').setInputFiles({name:'shipments.csv',mimeType:'text/csv',buffer:Buffer.from('status,boxes_pallets,pickup_tag\nIn Transit,2,Browser-'+Date.now()+'\nDelivered,1,Browser-'+Date.now())});
        await page.locator('#importConsignmentsModal button[type=submit]').click();
        await page.getByRole('heading',{name:'Review shipment import'}).waitFor();
        await page.getByRole('button',{name:'Confirm and issue LRs'}).click();
        await page.getByRole('heading',{name:'Import results'}).waitFor();
        const numbers=await page.locator('main table tbody tr td:nth-child(2)').allTextContents();
        assert.equal(numbers.length,2); assert.notEqual(numbers[0],numbers[1]);
        assert.ok(numbers.every(n=>/^HQ\d{10}$/.test(n)));
        assert.deepEqual(errors,[]);
        console.log('PASS: mandatory location, read-only LR, immediate modal issuance, cancellation disappears from Active and remains in Archived, labels/LR exclude inactive shipments, archive/restore filtering, retained history and confirmed CSV import.');
    } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
