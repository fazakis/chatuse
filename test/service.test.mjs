import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, access } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Service } from '../server/service.mjs';
import { tools, validate, mcpContent } from '../server/tools.mjs';
const temp = async t => { const p=await mkdtemp(join(tmpdir(),'chatuse-test-'));t.after(()=>rm(p,{recursive:true,force:true}));return p; };
test('audit does not persist typed or clipboard text', async t => {
  const root = await temp(t); const s = new Service(root,{request:async()=>({ok:true})});
  await s.call('type_text',{text:'secret-never-log',app:'private-app'});
  const audit=await readFile(join(root,'runtime/audit.jsonl'),'utf8');
  assert.ok(audit.includes('type_text'));assert.ok(!audit.includes('secret-never-log'));assert.ok(!audit.includes('private-app'));
});
test('stop is not blocked by pending operations', async t => {
  const root = await temp(t); let finish;
  const s = new Service(root,{request:()=>new Promise(r=>{finish=r;})});
  const pending=s.call('type_text',{});await new Promise(r=>setImmediate(r));
  const result=await s.call('emergency_stop');assert.equal(result.stopped,true);await access(join(root,'STOP'));
  finish({});await pending;
});
test('observe preserves accessibility result when screenshot permission is absent', async t => {
  const s=new Service(await temp(t),{request:async method=>{if(method==='screenshot')throw Object.assign(Error('Denied'),{code:'SCREEN_RECORDING_REQUIRED'});return{elements:[{id:'e1'}]};}});
  const r=await s.call('observe',{screenshot:true});assert.equal(r.accessibility.elements[0].id,'e1');assert.equal(r.screenshotError.code,'SCREEN_RECORDING_REQUIRED');
});
test('input schemas reject oversized input and unknown arguments',()=>{
  assert.throws(()=>validate(tools.find(t=>t.name==='type_text'),{text:'a'.repeat(50001)}));
  assert.throws(()=>validate(tools.find(t=>t.name==='press_key'),{key:'a',modifiers:['bogus']}));
  assert.throws(()=>validate(tools.find(t=>t.name==='status'),{surprise:true}));
});
test('MCP image content separates image bytes from metadata',()=>{
  const r=mcpContent({width:2,imageBase64:'abcd',mimeType:'image/png'});
  assert.deepEqual(JSON.parse(r.content[0].text),{width:2});assert.equal(r.content[1].type,'image');
});
test('wait_for propagates its deadline and reports timed-out read without stale references',async t=>{
  let budget;
  const s=new Service(await temp(t),{request:async(method,args,options)=>{budget=options.timeoutMs;throw Object.assign(Error('timeout'),{code:'HELPER_TIMEOUT'});}});
  const result=await s.call('wait_for',{query:'missing',timeoutMs:100});
  assert.equal(result.found,false);assert.equal(result.timedOut,true);assert.ok(budget>0&&budget<=100);assert.equal(result.snapshotId,undefined);
});
