import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { visualPoint,VisualController } from '../server/visual.mjs';
test('visual pointer uses scaled screenshot pixels including negative display origin',()=>{
  const images=new Map([['s',{at:100,width:800,height:400,frame:{x:-1600,y:100,width:1600,height:800},pid:10}]]);
  assert.deepEqual(visualPoint({screenshotId:'s',x:400,y:200},new Map(),images,100),{x:-800,y:500,pid:10});
  assert.equal(visualPoint({screenshotId:'s',x:800,y:200},new Map(),images,100),null);
  assert.equal(visualPoint({screenshotId:'s',x:1,y:1},new Map(),images,120101),null);
});
test('AX visual targets use observed bounds; invisible zero-sized items produce no pointer',()=>{
  const snapshots=new Map([['s',{at:0,pid:12,elements:new Map([['e1',{x:100,y:200,width:80,height:20}],['e2',{x:0,y:0,width:0,height:0}]])}]]);
  assert.deepEqual(visualPoint({snapshotId:'s',elementId:'e1'},snapshots,new Map(),1),{x:140,y:210,pid:12,accessibility:true});
  assert.equal(visualPoint({snapshotId:'s',elementId:'e2'},snapshots,new Map(),1),null);
});
async function setup(t,raw,pointer={request:async()=>({}),close(){}}){
  const root=await mkdtemp(join(tmpdir(),'chatuse-visual-'));t.after(()=>rm(root,{recursive:true,force:true}));await mkdir(join(root,'runtime'));await writeFile(join(root,'runtime/chatuse-pointer'),'');return new VisualController(root,pointer,raw);
}
test('overlay failure does not replay or block the actual click',async t=>{
  let clicks=0;
  const v=await setup(t,async method=>{if(method==='status')return{accessibility:true};if(method==='click')clicks++;return{};},{request:async()=>{throw Error('visual crashed');},close(){}});
  await v.request('click',{x:10,y:20},{});assert.equal(clicks,1);
});
test('screenshot hides overlay and restores it even on capture failure',async t=>{
  const calls=[];
  const v=await setup(t,async()=>{throw Error('capture denied');},{request:async m=>{calls.push(m);return{};},close(){}});
  await assert.rejects(v.request('screenshot',{},{}),/capture denied/);assert.deepEqual(calls,['hide','restore']);
});
test('no visual feedback is issued when input readiness fails',async t=>{
  const calls=[];
  const v=await setup(t,async m=>m==='status'?{accessibility:true,locked:true}:{},{request:async m=>{calls.push(m);},close(){}});
  await v.request('click',{x:10,y:20},{});assert.deepEqual(calls,[]);
});
