import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import assert from 'node:assert/strict';

const root=resolve(import.meta.dirname,'../..'), client=new Client({name:'chatuse-ubuntu-live-test',version:'1'});
const directory=join(root,'artifacts'), output=join(directory,`mcp-fixture-${process.pid}.json`);
const desktopFile=join(directory,`mcp-fixture-${process.pid}.desktop`);
const quote=value=>'"'+value.replace(/[\\"`$]/g,'\\$&')+'"';
let pid;
const call=async(name,args={})=>{
  const result=await client.callTool({name:'chatuse_'+name,arguments:args});
  assert.ok(!result.isError,JSON.stringify(result));
  return {data:JSON.parse(result.content.find(c=>c.type==='text').text),content:result.content};
};
try {
  await mkdir(directory,{recursive:true});
  await client.connect(new StdioClientTransport({command:process.execPath,args:[join(root,'server/index.mjs')],cwd:root,env:{...process.env},stderr:'pipe'}));
  const {data:status}=await call('status');
  assert.equal(status.platform,'Linux');assert.equal(status.locked,false);assert.equal(status.sessionType,'x11');
  await writeFile(desktopFile,`[Desktop Entry]\nType=Application\nName=Chatuse MCP Fixture\nExec=/usr/bin/env ${quote('CHATUSE_FIXTURE_OUTPUT='+output)} /usr/bin/python3 ${quote(join(root,'scripts/linux/fixture.py'))}\nTerminal=false\n`,{mode:0o600});
  await call('launch',{app:desktopFile});
  for(let i=0;i<60;i++){
    try{pid=JSON.parse(await readFile(output,'utf8')).pid;break;}catch{await delay(100);}
  }
  assert.ok(pid,'Desktop launcher must create the fixture');
  assert.equal((await call('wait_for',{pid,query:'chatuse-increment',timeoutMs:5000})).data.found,true);
  const observed=(await call('observe',{pid,screenshot:true,maxWidth:900})).data.accessibility;
  const text=observed.elements.find(e=>e.description==='chatuse-text');
  await call('set_value',{snapshotId:observed.snapshotId,elementId:text.id,attribute:'AXFocused',value:true});
  await call('type_text',{pid,text:'Ubuntu MCP works 😀 Ελληνικά'});
  const button=observed.elements.find(e=>e.description==='chatuse-increment');
  await call('click',{snapshotId:observed.snapshotId,elementId:button.id});
  assert.equal((await call('wait_for',{pid,query:'Clicks: 1',timeoutMs:5000})).data.found,true);
  const result=await call('observe',{pid,screenshot:true,ocr:true,maxWidth:900});
  assert.ok(result.data.accessibility.elements.some(e=>e.value==='Ubuntu MCP works 😀 Ελληνικά'));
  assert.ok(result.data.text.some(t=>/Clicks:\s*1/.test(t.text)));
  const image=result.content.find(c=>c.type==='image');assert.ok(image);
  await writeFile(join(directory,'ubuntu-mcp-demo.png'),Buffer.from(image.data,'base64'),{mode:0o600});
  await writeFile(join(directory,'ubuntu-mcp-report.json'),JSON.stringify({status:'passed',at:new Date().toISOString(),checks:['MCP launch of owned desktop fixture','MCP wait/observe/type/accessibility click','AT-SPI and OCR verify resulting state','MCP returns PNG image content']},null,2),{mode:0o600});
  await call('window',{pid,action:'close'});pid=null;
  console.log('PASS real MCP launch, observe, Unicode typing, click, wait, and screenshot/OCR verification');
}finally{
  if(pid)try{process.kill(pid,'SIGTERM');}catch{}
  await client.close();
}
