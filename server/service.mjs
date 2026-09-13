import { appendFile, mkdir, open, stat, rename, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { NativeError } from './native.mjs';

export class Service {
  constructor(root, native) { this.root = root; this.native = native; this.queue = Promise.resolve(); }
  async audit(method, started, code) {
    // No arguments, screenshot contents, app text, clipboard values, or typed text.
    const dir = join(this.root, 'runtime');
    await mkdir(dir, { recursive: true, mode: 0o700 });
    const path = join(dir, 'audit.jsonl');
    if ((await stat(path).catch(() => null))?.size > 2_000_000) {
      await unlink(path + '.1').catch(() => {}); await rename(path, path + '.1');
    }
    await appendFile(path, JSON.stringify({ at: new Date().toISOString(), tool: method, durationMs: Date.now()-started, status: code })+'\n', { mode: 0o600 });
  }
  call(method, args = {}, options = {}) {
    // Stop bypasses serialization so a long-running call cannot postpone it.
    if (method === 'emergency_stop') return this.stop();
    const call = this.queue.then(() => this.execute(method, args, options));
    this.queue = call.catch(() => {}); return call;
  }
  async stop() {
    const f = await open(join(this.root, 'STOP'), 'w', 0o600); await f.close();
    return { stopped: true, resume: 'Run ./chatuse resume locally. No remote resume tool exists.' };
  }
  async execute(method, args, options) {
    const started = Date.now(); let code = 'ok';
    try {
      if (method === 'observe') {
        const result = await this.native.request('inspect', args, options);
        if (!args.screenshot) return result;
        try { return { ...await this.native.request('screenshot', args, options), accessibility: result }; }
        catch (error) { return { accessibility: result, screenshotError: { code: error.code, message: error.message } }; }
      }
      if (method === 'wait_for') {
        const deadline = Date.now() + args.timeoutMs;
        do {
          let result;
          try { result = await this.native.request('inspect', { ...args, maxNodes: 500, maxDepth: 12 }, { ...options, timeoutMs: Math.max(1, deadline-Date.now()) }); }
          catch (error) {
            if (error.code === 'HELPER_TIMEOUT') return { found: false, timedOut: true, message: 'No matching element confirmed before the deadline. The read-only helper was reset; previous snapshot IDs are invalid.' };
            throw error;
          }
          if (result.elements.length) return { found: true, ...result };
          if (Date.now() >= deadline) return { found: false, ...result };
          await delay(Math.min(250, deadline-Date.now()), undefined, { signal: options.signal });
        } while (true);
      }
      return await this.native.request(method, args, options);
    } catch (error) { code = error.code ?? 'ERROR'; throw error; }
    finally { await this.audit(method, started, code).catch(() => {}); }
  }
}
