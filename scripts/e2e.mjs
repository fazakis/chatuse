if(process.platform==='linux') await import('./linux/e2e.mjs');
else if(process.platform==='darwin') await import('./macos-e2e.mjs');
else throw new Error('Desktop tests require macOS or Linux/X11.');
