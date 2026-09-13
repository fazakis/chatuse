import { existsSync } from 'node:fs';
import { mkdir, writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

export function visualPoint(args, snapshots, images, now = Date.now()) {
  if (args.elementId) {
    const s = snapshots.get(args.snapshotId), b = s?.elements.get(args.elementId);
    if (!s || now-s.at>120000 || !b || b.width<=0 || b.height<=0) return null;
    return { x:b.x+b.width/2, y:b.y+b.height/2, pid:s.pid, accessibility:true };
  }
  if (!Number.isFinite(args.x) || !Number.isFinite(args.y)) return null;
  if (args.screenshotId) {
    const s=images.get(args.screenshotId);
    if (!s || now-s.at>120000 || args.x<0 || args.y<0 || args.x>=s.width || args.y>=s.height) return null;
    return { x:s.frame.x+args.x*s.frame.width/s.width, y:s.frame.y+args.y*s.frame.height/s.height, pid:s.pid };
  }
  return {x:args.x,y:args.y,pid:args.pid};
}

const inputs=new Set(['click','move_pointer','scroll','drag']);
export class VisualController {
  constructor(root,pointer,raw) {this.root=root;this.pointer=pointer;this.raw=raw;this.snapshots=new Map();this.images=new Map();}
  enabled() {return process.env.CHATUSE_VISUAL_POINTER!=='0' && !existsSync(join(this.root,'runtime/pointer-disabled'));}
  available() {return existsSync(join(this.root,'runtime/chatuse-pointer'));}
  cache(method,result) {
    if(method==='inspect' && result.snapshotId) {
      this.snapshots.set(result.snapshotId,{at:Date.now(),pid:result.app?.pid,elements:new Map(result.elements.filter(e=>e.bounds).map(e=>[e.id,e.bounds]))});
      if(this.snapshots.size>8)this.snapshots.delete(this.snapshots.keys().next().value);
    }
    if(method==='screenshot' && result.screenshotId) {
      this.images.set(result.screenshotId,{at:Date.now(),pid:result.pid,frame:result.screenBounds,width:result.width,height:result.height});
      if(this.images.size>8)this.images.delete(this.images.keys().next().value);
    }
  }
  async visual(method,args={}) {
    try {return await this.pointer.request(method,args);} catch {return null;}
  }
  async request(method,args,options) {
    if(method==='status')return{...await this.raw(method,args,options),visualPointer:this.enabled(),visualPointerAvailable:this.available()};
    let marker;
    if(method==='screenshot' && this.available()) {
      const dir=join(this.root,'runtime/pointer-captures');await mkdir(dir,{recursive:true});
      marker=join(dir,`${process.pid}-${randomUUID()}`);await writeFile(marker,'',{mode:0o600});
      await this.visual('hide');await delay(60);
    }
    try {
      let point=null,end=null,animation;
      if(inputs.has(method) && this.available() && this.enabled()) {
        point=visualPoint(method==='drag'?{...args,x:args.fromX,y:args.fromY}:args,this.snapshots,this.images);
        if(method==='drag')end=visualPoint({...args,x:args.toX,y:args.toY},this.snapshots,this.images);
        if(point) {
          const s=await this.raw('status',{},options);
          if(!s.accessibility||s.locked||s.secureInput||s.stopped)point=null;
        }
        if(point) {
          // Match the backend's eventual activation for pointer events, without changing AX background actions.
          if(!point.accessibility && (point.pid || args.app)) await this.raw('window',{pid:point.pid,app:args.app,action:'focus'},options);
          await this.visual('move',{x:point.x,y:point.y,durationMs:320});
          if(method==='drag' && end) animation=this.visual('drag',{x:end.x,y:end.y,durationMs:args.durationMs??600});
        }
      }
      try {
        const result=await this.raw(method,args,options);this.cache(method,result);
        if(animation)await animation;
        if(point && (method==='click'||method==='scroll')) await this.visual('pulse',{x:result.x??point.x,y:result.y??point.y,label:method==='scroll'?'Chatuse · scroll':`Chatuse · ${args.button==='right'?'right click':'click'}`});
        return result;
      } catch(error) {
        if(animation)await animation;
        if(point)await this.visual('hide');
        throw error;
      }
    } finally {
      if(marker){await unlink(marker).catch(()=>{});await this.visual('restore');}
    }
  }
  close(){this.pointer.close();}
}
