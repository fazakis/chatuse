import { createInterface } from 'node:readline';
const lines = createInterface({ input: process.stdin });
lines.on('line', line => {
  const { id, method, params } = JSON.parse(line);
  if (method === 'exit') return process.exit(2);
  if (method === 'hang') return;
  if (method === 'bad') return console.log('not json');
  if (method === 'error') return console.log(JSON.stringify({ id, error: { code: 'NATIVE_TEST', message: 'error' } }));
  setTimeout(() => console.log(JSON.stringify({ id, result: { method, params } })), params.delay ?? 0);
});
