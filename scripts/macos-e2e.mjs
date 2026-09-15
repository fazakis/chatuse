import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile, unlink, access } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import assert from 'node:assert/strict';
import { NativeClient } from '../server/native.mjs';

const root = resolve(import.meta.dirname,'..');
const native = new NativeClient(root), checks = [];
let fixture, ownedStop = false;
const record = async (name, run) => { await run(); checks.push(name); console.log('PASS '+name); };
try {
  const status = await native.request('status');
  if (status.locked || status.secureInput || !status.accessibility || !status.screenRecording || status.stopped) {
    const report = { status: 'blocked', reason: 'Unlock the Mac, finish Secure Input, enable Chatuse Accessibility and Screen Recording, and ensure emergency stop is inactive.', permissions: status, checks };
    await writeFile(join(root,'artifacts/e2e-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2)); process.exitCode=2;
  } else {
    const bundle = join(root,'runtime/Chatuse Fixture.app');
    await mkdir(join(bundle,'Contents/MacOS'),{recursive:true});
    execFileSync('xcrun',['swiftc',join(root,'scripts/TestApp.swift'),'-o',join(bundle,'Contents/MacOS/Fixture')]);
    await writeFile(join(bundle,'Contents/Info.plist'),'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>local.chatuse.fixture</string><key>CFBundleExecutable</key><string>Fixture</string><key>CFBundleName</key><string>Chatuse Fixture</string><key>CFBundlePackageType</key><string>APPL</string></dict></plist>');
    execFileSync('codesign',['--force','--sign','-',bundle],{stdio:'pipe'});
    fixture=spawn(join(bundle,'Contents/MacOS/Fixture'),[],{env:{...process.env,CHATUSE_FIXTURE_OUTPUT:join(root,'artifacts/fixture-events.json')},stdio:'ignore'});
    fixture.on('error',()=>{});
    const target={pid:fixture.pid}; await delay(1000);
    let snapshot;
    const inspect=async()=> { snapshot=await native.request('inspect',target);return snapshot; };
    const ref=identifier=> {const el=snapshot.elements.find(e=>e.identifier===identifier);assert.ok(el,identifier);return{snapshotId:snapshot.snapshotId,elementId:el.id};};
    await record('discover and inspect fixture',async()=>{await inspect();ref('chatuse-text');ref('chatuse-increment');});
    await record('accessibility button action',async()=>{await native.request('click',ref('chatuse-increment'));await delay(100);assert.equal(JSON.parse(await readFile(join(root,'artifacts/fixture-events.json'))).clicks,1);});
    await record('set value and read back',async()=>{await inspect();await native.request('set_value',{...ref('chatuse-text'),value:'AX direct value'});await inspect();assert.equal(snapshot.elements.find(e=>e.identifier==='chatuse-text').value,'AX direct value');});
    await record('Unicode keyboard text and shortcut',async()=>{
      await native.request('window',{...target,action:'focus'});await inspect();
      await native.request('set_value',{...ref('chatuse-text'),attribute:'AXFocused',value:true});
      await native.request('press_key',{...target,key:'a',modifiers:['cmd']});
      await delay(100);
      await native.request('type_text',{...target,text:'Chatuse 😀 Ελληνικά 你好'});await delay(150);await inspect();
      assert.equal(snapshot.elements.find(e=>e.identifier==='chatuse-text').value,'Chatuse 😀 Ελληνικά 你好');
    });
    let screenshot;
    await record('window screenshot and OCR',async()=>{
      screenshot=await native.request('screenshot',{...target,ocr:true,maxWidth:900});
      const bytes=Buffer.from(screenshot.imageBase64,'base64');assert.ok(bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])));
      assert.ok(screenshot.text.some(t=>t.text.includes('Increment')));
      await writeFile(join(root,'artifacts/fixture-screenshot.png'),bytes);
    });
    await record('coordinate button click with screenshot mapping',async()=>{
      await inspect();const e=snapshot.elements.find(e=>e.identifier==='chatuse-increment');const b=e.bounds,s=screenshot.screenBounds;
      await native.request('click',{screenshotId:screenshot.screenshotId,x:(b.x+b.width/2-s.x)*screenshot.width/s.width,y:(b.y+b.height/2-s.y)*screenshot.height/s.height});
      await delay(100);assert.equal(JSON.parse(await readFile(join(root,'artifacts/fixture-events.json'))).clicks,2);
    });
    await record('fresh window captures preserve scaling and updated content',async()=>{
      for (const maxWidth of [320,900]) {
        const shot=await native.request('screenshot',{windowId:screenshot.windowId,maxWidth,ocr:true});
        const bytes=Buffer.from(shot.imageBase64,'base64');
        assert.equal(shot.width,maxWidth);
        assert.equal(bytes.readUInt32BE(16),shot.width);assert.equal(bytes.readUInt32BE(20),shot.height);
        assert.deepEqual(shot.screenBounds,screenshot.screenBounds);
        assert.notEqual(shot.screenshotId,screenshot.screenshotId);
        assert.ok(shot.text.some(t=>/Clicks:\s*2/.test(t.text)),JSON.stringify(shot.text));
      }
    });
    await record('display screenshot with cursor and correct geometry',async()=>{
      const {displays}=await native.request('displays');const display=displays.find(d=>d.main);
      const shot=await native.request('screenshot',{displayId:display.id,maxWidth:640,showCursor:true});
      const bytes=Buffer.from(shot.imageBase64,'base64');
      assert.equal(shot.width,640);assert.equal(bytes.readUInt32BE(16),shot.width);assert.equal(bytes.readUInt32BE(20),shot.height);
      assert.deepEqual(shot.screenBounds,display.bounds);
    });
    await record('window screenshot includes the system cursor only when requested',async()=>{
      // The coordinate click above left the system cursor over the fixture button.
      const options={windowId:screenshot.windowId,maxWidth:900};
      const hidden=await native.request('screenshot',{...options,showCursor:false});
      const visible=await native.request('screenshot',{...options,showCursor:true});
      assert.notEqual(visible.imageBase64,hidden.imageBase64);
    });
    await record('window move invalidates screenshot coordinates',async()=>{
      const b=screenshot.screenBounds;await native.request('window',{...target,action:'move',x:b.x+25,y:b.y+25});
      await delay(100);await assert.rejects(native.request('click',{screenshotId:screenshot.screenshotId,x:10,y:10}),{code:'WINDOW_MOVED'});
    });
    await record('resize and restore window',async()=>{
      await native.request('window',{...target,action:'resize',width:600,height:400});
      await native.request('window',{...target,action:'minimize'});await native.request('window',{...target,action:'restore'});await native.request('window',{...target,action:'raise'});
    });
    await record('emergency stop blocks input',async()=>{
      try { await access(join(root,'STOP'));throw Error('Existing stop must not be changed by test'); } catch(e){if(e.code!=='ENOENT')throw e;}
      await writeFile(join(root,'STOP'),'');ownedStop=true;
      await assert.rejects(native.request('type_text',{...target,text:'must-not-appear'}),{code:'STOPPED'});
      await unlink(join(root,'STOP'));ownedStop=false;
    });
    await record('invalid element reference rejected',async()=>{await assert.rejects(native.request('click',{snapshotId:'invalid',elementId:'e1'}),{code:'UNKNOWN_ELEMENT'});});
    await writeFile(join(root,'artifacts/e2e-report.json'),JSON.stringify({status:'passed',at:new Date().toISOString(),checks},null,2));
  }
} catch(e) {
  await writeFile(join(root,'artifacts/e2e-report.json'),JSON.stringify({status:'failed',checks,error:{code:e.code,message:e.message}},null,2));
  console.error(e);process.exitCode=1;
} finally {
  if(ownedStop)await unlink(join(root,'STOP')).catch(()=>{});
  fixture?.kill('SIGTERM');native.close();
}
