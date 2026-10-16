// Opt-in: run from an unlocked GNOME Wayland session after npm run build.
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../', import.meta.url));
const client = new Client({name:'chatuse-wayland-smoke',version:'1'});
try {
  await client.connect(new StdioClientTransport({command:'node',args:[root+'server/index.mjs'],cwd:root,env:{...process.env,CHATUSE_ROOT:root},stderr:'pipe'}));
  async function call(name,args={}) {
    const result=await client.callTool({name:'chatuse_'+name,arguments:args});
    assert.ok(!result.isError,JSON.stringify(result));
    return result;
  }
  const json=result=>JSON.parse(result.content.find(c=>c.type==='text').text);
  assert.equal(json(await call('status')).backend,'atspi-wayland-portal');
  assert.equal(json(await call('request_permissions')).inputAvailable,true);
  const screenshot=await call('screenshot',{maxWidth:1280});
  assert.ok(screenshot.content.some(c=>c.type==='image' && c.mimeType==='image/png'));
  const frame=json(screenshot);
  await call('move_pointer',{screenshotId:frame.screenshotId,x:frame.width/2,y:frame.height/2});
  const after=json(await call('screenshot',{maxWidth:1280}));
  assert.notEqual(after.screenshotId,frame.screenshotId);
  console.log('PASS: MCP permission, screenshot image, cursor move, fresh screenshot');
} finally {await client.close();}
