const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/admin/autosave.js','utf8');
function setup(overrides={}) {
    let callback;
    const context={setTimeout:fn=>{callback=fn;return 1;},clearTimeout:()=>{callback=null;}};
    vm.createContext(context); vm.runInContext(source,context);
    const pending=new Map(), events=[], sent=[];
    const options={pending,validate:()=>'',send:async rows=>{sent.push(rows);return {success:true};},
        error:e=>events.push(e.message),success:()=>events.push('saved'),...overrides};
    const queue=context.createShipmentAutosave(options);
    return {pending,events,sent,queue,tick:async()=>{const cb=callback;callback=null;if(cb) await cb();}};
}
test('rapid edits coalesce and manual save flushes immediately',async()=>{
    const s=setup();
    s.pending.set('1',{id:'1',pieces:2});s.queue.schedule();
    s.pending.set('1',{id:'1',pieces:3});s.queue.schedule();
    assert.equal(s.sent.length,0);await s.queue.flush(true);
    assert.equal(s.sent.length,1);assert.equal(s.sent[0][0].pieces,3);assert.equal(s.pending.size,0);
});
test('newer edits survive a response for an older version and are saved next',async()=>{
    let resolve;
    const s=setup({send:rows=>new Promise(r=>{resolve=r;})});
    s.pending.set('1',{id:'1',pieces:2});const first=s.queue.flush(false);
    await Promise.resolve();
    s.pending.set('1',{id:'1',pieces:5});s.queue.schedule();
    resolve({success:true});await first;
    assert.equal(s.pending.get('1').pieces,5);
    const second=s.queue.flush(false);await Promise.resolve();resolve({success:true});await second;
    assert.equal(s.pending.size,0);
});
test('same serialized value with a newer invalid edit is not acknowledged',async()=>{
    let version=1, resolve;
    const s=setup({version:()=>version,send:()=>new Promise(r=>{resolve=r;})});
    s.pending.set('1',{id:'1',weight:''});const first=s.queue.flush(false);await Promise.resolve();
    version=2;s.queue.schedule();resolve({success:true});await first;
    assert.equal(s.pending.size,1);
});
test('failed saves retain pending data, do not loop, and can be retried',async()=>{
    let calls=0;
    const s=setup({send:async()=>{calls++;return calls===1?{success:false,message:'Offline'}:{success:true};}});
    s.pending.set('1',{id:'1',pieces:2});assert.equal(await s.queue.flush(false),false);
    assert.equal(s.pending.size,1);await s.tick();assert.equal(calls,1);
    assert.equal(await s.queue.flush(true),true);assert.equal(s.pending.size,0);
});
test('invalid rows remain pending while valid rows save',async()=>{
    const s=setup({validate:r=>r.pieces<1?'Invalid pieces':''});
    s.pending.set('1',{id:'1',pieces:0});s.pending.set('2',{id:'2',pieces:4});
    await s.queue.flush(false);
    assert.equal(s.sent[0].length,1);assert.equal(s.sent[0][0].id,'2');assert.equal(s.pending.size,1);
});
test('in-flight flushes share one request and modal pause keeps edits pending',async()=>{
    let paused=true, resolve, calls=0;
    const s=setup({paused:()=>paused,send:()=>{calls++;return new Promise(r=>{resolve=r;});}});
    s.pending.set('1',{id:'1'});await s.queue.flush(false);assert.equal(calls,0);
    paused=false;const first=s.queue.flush(false),second=s.queue.flush(true);
    assert.equal(first,second);await Promise.resolve();assert.equal(calls,1);resolve({success:true});await first;
});
test('pending batches respect the server row limit',async()=>{
    const s=setup({batchSize:2});
    for(let n=1;n<=5;n++)s.pending.set(String(n),{id:String(n)});
    await s.queue.flush(true);assert.equal(s.sent[0].length,2);assert.equal(s.pending.size,3);
    await s.tick();assert.equal(s.pending.size,1);await s.tick();assert.equal(s.pending.size,0);
});
