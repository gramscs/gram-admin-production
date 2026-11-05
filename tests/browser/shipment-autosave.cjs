// Uses only the disposable PostgreSQL app in tests/browser/serve.py.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
    const base='http://127.0.0.1:10003';
    const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
    const page=await browser.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    try {
        await page.goto(base+'/admin/login');
        await page.locator('[name=email]').fill('admin@example.test');
        await page.locator('[name=password]').fill('system-test-only');
        await page.locator('button[type=submit]').click();await page.waitForURL('**/admin/dashboard');
        await page.goto(base+'/admin/consignments');await page.locator('#add-row-btn').click();
        await page.waitForFunction(()=>document.querySelector('#modal-location-id').options.length>1);
        await page.locator('#modal-location-id').selectOption({label:'HQ — Test HQ'});
        await page.locator('#modal-status').selectOption('In Transit');
        const saveResponse=()=>{const promise=page.waitForResponse(r=>r.url().endsWith('/consignments/save')&&r.request().method()==='POST',{timeout:10000});promise.catch(()=>{});return promise;};
        const created=saveResponse();await page.locator('#modal-save-btn').click();
        const creation=await created;assert.equal(creation.status(),200);const shipment=(await creation.json()).shipments[0];
        await page.locator('#editConsignmentModal').waitFor({state:'hidden'});
        await page.locator('#search-input').fill(shipment.lr_number);
        await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='1');
        const pieces=page.locator('#sheet-body .pieces');
        const stored=async()=> (await (await page.request.get(base+'/admin/consignments/list?search='+shipment.lr_number)).json()).rows[0];
        const saved=async()=>page.waitForFunction(()=>document.querySelector('#autosave-status').textContent==='Saved');
        let response=saveResponse();await pieces.fill('4');assert.equal((await response).status(),200);await saved();
        assert.equal((await stored()).pieces,4);
        assert.equal(await pieces.evaluate(el=>el===document.activeElement),true,'Autosave should retain input focus');
        let release,seen;
        const gate=new Promise(r=>{release=r;}),seenRequest=new Promise(r=>{seen=r;});
        await page.route('**/admin/consignments/save',async route=>{
            const backend=await route.fetch();seen();await gate;await route.fulfill({response:backend});
        },{times:1});
        await pieces.fill('5');await seenRequest;
        await pieces.fill('6');release();
        await page.waitForFunction(()=>window.adminState.pendingRows.size===0&&document.querySelector('#autosave-status').textContent==='Saved');
        assert.equal((await stored()).pieces,6,'An older response must not consume newer edits');
        await pieces.fill('0');
        await page.waitForFunction(()=>document.querySelector('#autosave-status').textContent.startsWith('Not saved:'));
        assert.equal((await stored()).pieces,6);
        response=saveResponse();await pieces.fill('7');await page.locator('#save-btn').click();
        assert.equal((await response).status(),200);await saved();assert.equal((await stored()).pieces,7);
        let retryKey;
        await page.route('**/admin/consignments/save',async route=>{retryKey=route.request().postDataJSON().idempotency_key;await route.abort();},{times:1});
        await pieces.fill('8');
        await page.waitForFunction(()=>document.querySelector('#autosave-status').textContent.startsWith('Not saved:'));
        assert.equal(await pieces.inputValue(),'8');assert.equal((await stored()).pieces,7);
        response=saveResponse();await page.locator('#save-btn').click();const retry=await response;
        assert.equal(retry.request().postDataJSON().idempotency_key,retryKey);await saved();
        assert.equal((await stored()).pieces,8);assert.equal((await stored()).consignment_number,shipment.lr_number);
        await page.reload();await page.waitForFunction(()=>document.querySelector('#total-count').textContent==='1');
        assert.equal(await page.locator('#sheet-body .pieces').inputValue(),'8');
        assert.deepEqual(errors,[]);
        console.log('PASS: immediate modal save, inline autosave, retained focus, edits during an in-flight save, validation, manual flush and safe retry.');
    } catch (error) { console.error('Browser diagnostics:',errors,await page.locator('#autosave-status').textContent(),await page.evaluate(()=>({pending:window.adminState.pendingRows.size,saving:document.querySelector('#editConsignmentModal').dataset.saving,modal:document.querySelector('#editConsignmentModal').className})));throw error; } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
