#!/usr/bin/env node
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { unlink, writeFile } from 'node:fs/promises';
import { execFileSync, spawn } from 'node:child_process';
import { nativeCommand } from './platform.mjs';
import { NativeClient } from './native.mjs';
import { Service } from './service.mjs';
import { tools, validate } from './tools.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [command='help', json='{}'] = process.argv.slice(2);
if (command === 'mcp') { await import('./index.mjs'); }
else if (command === 'setup') {
  if (process.platform === 'darwin') {
    execFileSync('/usr/bin/open', ['-n',join(root,'runtime/Chatuse.app'),'--args','--setup']);
    console.log('Opened Chatuse Setup. Grant the two macOS permissions, then restart the MCP connection.');
  } else {
    const child = spawn(nativeCommand(root), ['--setup'], {detached:true,stdio:'ignore',env:{...process.env,CHATUSE_ROOT:root}});
    await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});
    child.unref();
    console.log('Started Chatuse Setup. Use an unlocked Ubuntu on Xorg desktop and enable desktop accessibility if needed.');
  }
}
else if (command === 'pointer-demo') {await import('../scripts/pointer-demo.mjs');}
else if (command === 'pointer') {
  const file=join(root,'runtime/pointer-disabled');
  if(json==='off'){await writeFile(file,'',{mode:0o600});console.log('Visual pointer disabled. Computer control remains enabled.');}
  else if(json==='on'){await unlink(file).catch(e=>{if(e.code!=='ENOENT')throw e;});console.log('Visual pointer enabled.');}
  else {console.log('Usage: ./chatuse pointer on | off; ./chatuse pointer-demo');}
}
else if (command === 'resume') {
  await unlink(join(root, 'STOP')).catch(e => { if (e.code !== 'ENOENT') throw e; });
  console.log('Chatuse input resumed.');
} else if (command === 'help') {
  console.log('Chatuse — macOS and Ubuntu/X11 computer use\n\n./chatuse status\n./chatuse permissions\n./chatuse list_apps\n./chatuse screenshot \'{"app":"frontmost"}\'\n./chatuse inspect \'{"app":"frontmost"}\'\n./chatuse mcp\n./chatuse stop | resume\n\nCommands: '+tools.map(t=>t.name).join(', ')+'\n\nCLI calls are one-shot. Element and screenshot IDs persist only in an MCP or native session.');
} else {
  const native = new NativeClient(root), service = new Service(root, native);
  try {
    const method = command === 'stop' ? 'emergency_stop' : command === 'permissions' ? 'request_permissions' : command;
    const def = tools.find(t => t.name === method);
    if (!def && method !== 'request_permissions') throw Error('Unknown command; run ./chatuse help');
    const args = def ? validate(def, JSON.parse(json)) : JSON.parse(json);
    const result = await service.call(method, args);
    if (result.imageBase64) {
      const path = join(root, 'artifacts', `capture-${Date.now()}.png`);
      await writeFile(path, Buffer.from(result.imageBase64, 'base64'), { mode: 0o600 });
      delete result.imageBase64; result.imagePath = path;
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (e) { console.error(JSON.stringify({ code: e.code ?? 'ERROR', message: e.message })); process.exitCode = 1; }
  finally { native.close(); }
}
