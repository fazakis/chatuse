import { spawnSync } from 'node:child_process';
const command=process.platform==='linux'?'/usr/bin/python3':'swift';
const args=process.platform==='linux'?['-m','unittest','discover','-s','test/linux','-v']:['test'];
const result=spawnSync(command,args,{stdio:'inherit'});
if(result.error)console.error(result.error.message);
process.exitCode=result.status??1;
