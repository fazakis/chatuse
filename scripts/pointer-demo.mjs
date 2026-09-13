import { NativeClient } from '../server/native.mjs';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
const root=resolve(import.meta.dirname,'..');
const pointer=new NativeClient(root,{command:resolve(root,'runtime/chatuse-pointer')});
const native=new NativeClient(root);
try {
  const {displays}=await native.request('displays');const d=displays.find(d=>d.main),b=d.bounds;
  const y=b.y+b.height*0.45;
  console.log('Visual-only demonstration: no real mouse movement or clicks.');
  for(const [fraction,label] of [[0.40,'Chatuse'],[0.50,'Chatuse · click'],[0.60,'Chatuse · click']]) {
    const x=b.x+b.width*fraction;
    await pointer.request('move',{x,y,durationMs:650});
    await pointer.request('pulse',{x,y,label});await delay(800);
  }
  await delay(1000);
} finally {pointer.close();native.close();}
