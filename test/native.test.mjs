import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { NativeClient } from '../server/native.mjs';
const make = timeoutMs => new NativeClient(resolve('.'), { command: process.execPath, args: [resolve('test/fake-native.mjs')], timeoutMs: timeoutMs ?? 2000 });
test('native requests are serialized and Unicode JSON is preserved', async t => {
  const n = make(); t.after(() => n.close()); const order = [];
  const one = n.request('one', { delay: 40, text: '你好 😀\nquote "' }).then(r => { order.push(r.method); return r; });
  const two = n.request('two').then(r => order.push(r.method));
  const [r] = await Promise.all([one,two]); assert.deepEqual(order,['one','two']); assert.equal(r.params.text,'你好 😀\nquote "');
});
test('native application error does not poison following requests', async t => {
  const n = make(); t.after(() => n.close());
  await assert.rejects(n.request('error'), { code: 'NATIVE_TEST' });
  assert.equal((await n.request('next')).method,'next');
});
test('exit has no automatic action retry; next call starts a new helper', async t => {
  const n = make(); t.after(() => n.close());
  await assert.rejects(n.request('exit'), { code: 'HELPER_EXITED' });
  assert.equal((await n.request('next')).method,'next');
});
test('timeout resets helper and rejects ambiguous operation', async t => {
  const n = make(); t.after(() => n.close());
  await n.request('ready');
  await assert.rejects(n.request('hang', {}, { timeoutMs: 200 }), { code: 'HELPER_TIMEOUT' });
  assert.equal(n.child, null);
  assert.equal((await n.request('next')).method,'next');
});
test('abort before dispatch never starts helper', async () => {
  const n = make(); const c = new AbortController(); c.abort();
  await assert.rejects(n.request('one',{}, {signal:c.signal}), {code:'CANCELLED'});
  assert.equal(n.child,null);
});
test('malformed protocol closes helper rather than accepting arbitrary output', async t => {
  const n = make(); t.after(() => n.close());
  await assert.rejects(n.request('bad'), {code:'PROTOCOL_ERROR'});
});
