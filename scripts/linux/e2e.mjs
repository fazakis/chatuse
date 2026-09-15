import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, writeFile, access, unlink, mkdir } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import assert from 'node:assert/strict';
import { NativeClient } from '../../server/native.mjs';

const root=resolve(import.meta.dirname,'../..'),native=new NativeClient(root),checks=[];
const output=join(root,'artifacts/linux-fixture-events.json');
let fixture,ownedStop=false,clipboardOriginal,clipboardChanged=false;
const state=async()=>JSON.parse(await readFile(output,'utf8'));
const inputState=async()=>JSON.parse((await promisify(execFile)('/usr/bin/python3',[join(root,'scripts/linux/input-state.py')])).stdout);
const record=async(name,run)=>{await run();checks.push(name);console.log('PASS '+name);};
const until=async predicate=>{const end=Date.now()+4000;while(Date.now()<end){if(await predicate())return;await delay(70);}assert.fail('Desktop state did not reach the expected value');};
await mkdir(join(root,'artifacts'),{recursive:true});
try {
  const status=await native.request('status');
  if(!status.accessibility||!status.screenRecording||status.locked||status.stopped||status.sessionType!=='x11') {
    const report={status:'blocked',reason:'Use an active, unlocked Ubuntu on Xorg session with AT-SPI and XTest available.',permissions:status,checks};
    await writeFile(join(root,'artifacts/linux-e2e-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));process.exitCode=2;
  } else {
    fixture=spawn('/usr/bin/python3',[join(root,'scripts/linux/fixture.py')],{env:{...process.env,CHATUSE_FIXTURE_OUTPUT:output,NO_AT_BRIDGE:'0'},stdio:'ignore'});
    const target={pid:fixture.pid};await delay(1200);
    let snapshot;
    const inspect=async()=>snapshot=await native.request('inspect',target);
    const element=id=>{const e=snapshot.elements.find(e=>e.identifier===id||e.description===id);assert.ok(e,id);return e;};
    const ref=id=>({snapshotId:snapshot.snapshotId,elementId:element(id).id});
    await record('discover fixture and inspect AT-SPI controls',async()=>{
      assert.ok((await native.request('list_apps')).apps.some(a=>a.pid===fixture.pid));
      await inspect();ref('chatuse-text');ref('chatuse-increment');
    });
    await record('secure text values are redacted',async()=>{
      assert.equal(element('chatuse-password').value,'[secure field]');
      assert.ok(!JSON.stringify(snapshot).includes('fixture-private-value'));
    });
    await record('accessibility button action',async()=>{
      await native.request('click',ref('chatuse-increment'));await until(async()=>(await state()).clicks===1);
    });
    await record('set accessible text and read back',async()=>{
      await native.request('set_value',{...ref('chatuse-text'),value:'AT-SPI direct value'});
      await inspect();assert.equal(element('chatuse-text').value,'AT-SPI direct value');
    });
    await record('Unicode keyboard input and Ctrl+A shortcut',async()=>{
      await native.request('window',{...target,action:'focus'});
      await native.request('set_value',{...ref('chatuse-text'),attribute:'AXFocused',value:true});
      await native.request('press_key',{...target,key:'a',modifiers:['ctrl']});
      await native.request('type_text',{...target,text:'Chatuse 😀 Ελληνικά 你好'});
      await until(async()=>(await state()).text==='Chatuse 😀 Ελληνικά 你好');
    });
    let shot;
    await record('window screenshot and OCR',async()=>{
      shot=await native.request('screenshot',{...target,ocr:true,maxWidth:900});
      assert.equal(shot.captureMode,'window-composite');assert.equal(shot.width,900);
      assert.ok(shot.text.some(t=>t.text.includes('Increment')),JSON.stringify(shot.text));
      await writeFile(join(root,'artifacts/ubuntu-fixture-screenshot.png'),Buffer.from(shot.imageBase64,'base64'),{mode:0o600});
    });
    const point=(bounds,x=.5,y=.5)=>({screenshotId:shot.screenshotId,x:(bounds.x+bounds.width*x-shot.screenBounds.x)*shot.width/shot.screenBounds.width,y:(bounds.y+bounds.height*y-shot.screenBounds.y)*shot.height/shot.screenBounds.height});
    await record('screenshot-mapped pointer click',async()=>{
      await inspect();await native.request('click',point(element('chatuse-increment').bounds));
      await until(async()=>(await state()).clicks===2);
    });
    await record('fresh captures retain geometry, scaling and current content',async()=>{
      for(const maxWidth of [320,900]){
        const fresh=await native.request('screenshot',{windowId:shot.windowId,maxWidth,ocr:true});
        assert.equal(fresh.width,maxWidth);assert.deepEqual(fresh.screenBounds,shot.screenBounds);
        assert.notEqual(fresh.screenshotId,shot.screenshotId);
        assert.ok(fresh.text.some(t=>/Clicks:\s*2/.test(t.text)),JSON.stringify(fresh.text));
        for(const {bounds:b} of fresh.text)assert.ok(b.x>=0&&b.y>=0&&b.x+b.width<=fresh.width+.01&&b.y+b.height<=fresh.height+.01);
      }
    });
    await record('system cursor is included only when requested',async()=>{
      const plain=await native.request('screenshot',{windowId:shot.windowId,maxWidth:900});
      const cursor=await native.request('screenshot',{windowId:shot.windowId,maxWidth:900,showCursor:true});
      assert.notEqual(plain.imageBase64,cursor.imageBase64);
    });
    await record('display screenshot geometry',async()=>{
      const {displays}=await native.request('displays'),main=displays.find(d=>d.main);
      const image=await native.request('screenshot',{displayId:main.id,maxWidth:640});
      assert.equal(image.width,640);assert.deepEqual(image.screenBounds,main.bounds);
    });
    await record('scrolling targets the observed scroll area',async()=>{
      await inspect();await native.request('scroll',{...target,...point(element('chatuse-scroll').bounds),dy:400});
      await until(async()=>(await state()).scroll>0);
    });
    await record('drag sends and releases mouse input',async()=>{
      await inspect();const b=element('chatuse-drag').bounds,a=point(b,.15,.5),z=point(b,.8,.5);
      await native.request('drag',{...target,screenshotId:shot.screenshotId,fromX:a.x,fromY:a.y,toX:z.x,toY:z.y,durationMs:400});
      await until(async()=>(await state()).drags===1);
    });
    await record('moving a window invalidates screenshot coordinates',async()=>{
      await native.request('window',{...target,action:'move',x:shot.screenBounds.x+25,y:shot.screenBounds.y+25});
      await delay(200);await assert.rejects(native.request('click',{screenshotId:shot.screenshotId,x:10,y:10}),{code:'WINDOW_MOVED'});
    });
    await record('resize, minimize and restore window',async()=>{
      await native.request('window',{...target,action:'resize',width:620,height:570});
      await until(async()=>{const w=(await native.request('windows',target)).windows[0];return w.bounds.width===620&&w.bounds.height===570;});
      await native.request('window',{...target,action:'minimize'});
      await until(async()=>(await native.request('windows',target)).windows[0].visible===false);
      await native.request('window',{...target,action:'restore'});await native.request('window',{...target,action:'raise'});
      await until(async()=>(await native.request('windows',target)).windows[0].visible===true);
    });
    await record('clipboard round trip with original text restored',async()=>{
      clipboardOriginal=(await native.request('clipboard_read')).text;
      clipboardChanged=true;await native.request('clipboard_write',{text:'Chatuse clipboard Ελληνικά 😀'});
      assert.equal((await native.request('clipboard_read')).text,'Chatuse clipboard Ελληνικά 😀');
      await native.request('clipboard_write',{text:clipboardOriginal});clipboardChanged=false;
    });
    await record('window index and screenshot input select the right window of one app',async()=>{
      const mainId=shot.windowId;
      fixture.kill('SIGUSR1');
      await until(async()=>(await native.request('windows',target)).windows.length===2);
      let windows=(await native.request('windows',target)).windows;
      const secondary=windows.find(w=>w.windowId!==mainId);
      assert.equal(windows[0].windowId,secondary.windowId);
      await native.request('window',{...target,action:'raise',index:windows.find(w=>w.windowId===mainId).index});
      await until(async()=>(await native.request('windows',target)).windows[0].windowId===mainId);
      windows=(await native.request('windows',target)).windows;
      await native.request('window',{...target,action:'raise',index:windows.find(w=>w.windowId===secondary.windowId).index});
      await until(async()=>(await native.request('windows',target)).windows[0].windowId===secondary.windowId);
      shot=await native.request('screenshot',{windowId:mainId,maxWidth:900});await inspect();
      const count=(await state()).clicks;
      await native.request('click',point(element('chatuse-increment').bounds));
      await until(async()=>(await state()).clicks===count+1);
      windows=(await native.request('windows',target)).windows;
      assert.equal(windows[0].windowId,mainId);
      await native.request('window',{...target,action:'close',index:windows.find(w=>w.windowId===secondary.windowId).index});
      await until(async()=>(await native.request('windows',target)).windows.length===1);
    });
    await record('emergency stop blocks input',async()=>{
      try{await access(join(root,'STOP'));assert.fail('Existing stop must not be changed');}catch(e){if(e.code!=='ENOENT')throw e;}
      await writeFile(join(root,'STOP'),'');ownedStop=true;
      await assert.rejects(native.request('type_text',{...target,text:'must-not-appear'}),{code:'STOPPED'});
      await unlink(join(root,'STOP'));ownedStop=false;
    });
    await record('invalid references and unsafe URL schemes are rejected',async()=>{
      await assert.rejects(native.request('click',{snapshotId:'invalid',elementId:'e1'}),{code:'UNKNOWN_ELEMENT'});
      await assert.rejects(native.request('open_url',{url:'file:///tmp/not-allowed'}),{code:'INVALID_URL'});
    });
    await record('stopping partial Unicode input restores keymap and releases keys',async()=>{
      await inspect();await native.request('set_value',{...ref('chatuse-text'),value:''});
      await native.request('set_value',{...ref('chatuse-text'),attribute:'AXFocused',value:true});
      const before=await inputState();assert.equal(before.heldKeys,0);
      const pending=native.request('type_text',{...target,text:'Ω'.repeat(100)});pending.catch(()=>{});
      await until(async()=>(await state()).text.length>=3);
      await writeFile(join(root,'STOP'),'');ownedStop=true;
      await assert.rejects(pending,{code:'STOPPED'});
      const text=(await state()).text;assert.ok(text.length<100);
      await delay(100);assert.equal((await state()).text,text);
      assert.deepEqual(await inputState(),before);
      await unlink(join(root,'STOP'));ownedStop=false;
    });
    await record('stopping a drag releases the held mouse button',async()=>{
      await inspect();shot=await native.request('screenshot',{...target,maxWidth:900});
      const b=element('chatuse-drag').bounds,a=point(b,.15,.5),z=point(b,.8,.5);
      const pending=native.request('drag',{...target,screenshotId:shot.screenshotId,fromX:a.x,fromY:a.y,toX:z.x,toY:z.y,durationMs:2000});pending.catch(()=>{});
      await until(async()=>(await inputState()).mouseButtons!==0);
      await writeFile(join(root,'STOP'),'');ownedStop=true;
      await assert.rejects(pending,{code:'STOPPED'});
      assert.equal((await inputState()).mouseButtons,0);
      await unlink(join(root,'STOP'));ownedStop=false;
    });
    await record('Wayland and headless helpers refuse desktop input and capture',async()=>{
      for(const [settings,code] of [[['XDG_SESSION_TYPE=wayland','WAYLAND_DISPLAY=wayland-test'],'WAYLAND_UNSUPPORTED'],[['DISPLAY='],'DESKTOP_REQUIRED']]){
        const guarded=new NativeClient(root,{command:'/usr/bin/env',args:[...settings,join(root,'runtime/chatuse-native')]});
        try{
          assert.equal((await guarded.request('status')).screenRecording,false);
          await assert.rejects(guarded.request('screenshot',{}),{code});
          await assert.rejects(guarded.request('type_text',{...target,text:'must-not-appear'}),{code});
        }finally{guarded.close();}
      }
    });
    await record('close the fixture window',async()=>{
      await native.request('window',{...target,action:'close'});
      await until(async()=>!(await native.request('list_apps')).apps.some(a=>a.pid===fixture.pid));
    });
    await writeFile(join(root,'artifacts/linux-e2e-report.json'),JSON.stringify({status:'passed',at:new Date().toISOString(),checks},null,2));
  }
} catch(e) {
  await writeFile(join(root,'artifacts/linux-e2e-report.json'),JSON.stringify({status:'failed',checks,error:{code:e.code,message:e.message}},null,2));
  console.error(e);process.exitCode=1;
} finally {
  if(ownedStop)await unlink(join(root,'STOP')).catch(()=>{});
  if(clipboardChanged)await native.request('clipboard_write',{text:clipboardOriginal}).catch(()=>{});
  fixture?.kill('SIGTERM');native.close();
}
