import { NativeClient } from '../server/native.mjs';
import { resolve,join } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import assert from 'node:assert/strict';
import { nativeCommand } from '../server/platform.mjs';
const root=resolve(import.meta.dirname,'..');
const pointer=new NativeClient(root,{command:join(root,'runtime/chatuse-pointer')});
// Capture only the overlay's own window through the authorized legacy helper.
const raw=new NativeClient(root,{command:nativeCommand(root)});
const controller=new NativeClient(root);
const checks=[];
try {
  const before=(await raw.request('list_apps')).apps.find(a=>a.active)?.pid;
  const {displays}=await raw.request('displays'),b=displays.find(d=>d.main).bounds;
  const x=b.x+b.width/2,y=b.y+b.height/2;
  await pointer.request('move',{x,y,durationMs:400});
  const s=await pointer.request('pulse',{x,y});
  assert.equal(s.visible,true);assert.equal(s.ignoresMouseEvents,true);assert.equal(s.canBecomeKey,false);
  checks.push('Visible overlay ignores input and cannot take keyboard focus');
  assert.equal((await raw.request('list_apps')).apps.find(a=>a.active)?.pid,before);
  checks.push('Showing and animating overlay preserves foreground app');
  const shot=await raw.request('screenshot',{windowId:s.windowId,maxWidth:500});
  await writeFile(join(root,'artifacts/pointer-preview.png'),Buffer.from(shot.imageBase64,'base64'));
  checks.push('Captured the rendered overlay window for visual review');
  // A different client hides visible overlays while it captures, using shared capture markers.
  const capture=controller.request('screenshot',{windowId:s.windowId,maxWidth:500},{timeoutMs:15000})
    .catch(e=>assert.notEqual(e.code,'HELPER_TIMEOUT','A hidden window must not hang the native helper'));
  await delay(100);
  assert.equal((await pointer.request('state')).visible,false);
  await capture;
  checks.push('Capture coordination suppresses another client overlay');
  await pointer.request('move',{x,y,durationMs:0});await delay(3400);
  assert.equal((await pointer.request('state')).visible,false);
  checks.push('Overlay disappears after idle timeout');
  await writeFile(join(root,'artifacts/pointer-report.json'),JSON.stringify({status:'passed',at:new Date().toISOString(),checks},null,2));
  console.log(JSON.stringify({status:'passed',checks},null,2));
}finally{pointer.close();raw.close();controller.close();}
