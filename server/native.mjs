import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { join } from 'node:path';
import { VisualController } from './visual.mjs';
import { nativeCommand } from './platform.mjs';

export class NativeError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

/** A persistent, serialized connection. Never retry actions after an ambiguous failure. */
export class NativeClient {
  constructor(root, { command, args = [], timeoutMs = 45000 } = {}) {
    this.root = root;
    this.command = command ?? nativeCommand(root);
    this.args = args; this.timeoutMs = timeoutMs; this.counter = 0;
    this.child = null; this.pending = null; this.queue = Promise.resolve();
    this.visual = command ? null : new VisualController(root,
      new NativeClient(root,{command:join(root,'runtime/chatuse-pointer'),timeoutMs:5000}),
      (method,params,options)=>this.perform(method,params,options?.signal,options?.timeoutMs));
  }
  start() {
    if (this.child) return;
    const child = spawn(this.command, this.args, {
      env: { ...process.env, CHATUSE_ROOT: this.root }, stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child = child;
    // Never mirror native stderr: operating-system diagnostics may include app text.
    child.stderr.resume();
    const lines = createInterface({ input: child.stdout });
    lines.on('line', line => {
      if (this.child !== child) return;
      try {
        const message = JSON.parse(line);
        if (!this.pending || message.id !== this.pending.id) return;
        if (message.error) this.pending.finish(new NativeError(message.error.code, message.error.message));
        else this.pending.finish(null, message.result);
      } catch { this.reset(new NativeError('PROTOCOL_ERROR', 'Native helper returned an invalid response.')); }
    });
    child.stdin.on('error', () => this.reset(new NativeError('HELPER_DISCONNECTED', 'Helper input closed. Check status before retrying any action.')));
    child.on('error', err => {
      if (this.child === child) this.reset(new NativeError('HELPER_START_FAILED', `Cannot start helper (${err.code}). Run npm run build.`));
    });
    child.on('exit', () => {
      if (this.child === child) this.reset(new NativeError('HELPER_EXITED', 'Helper exited. An action may have partially completed; inspect before retrying.'));
    });
  }
  request(method, params = {}, { signal, timeoutMs } = {}) {
    const operation = this.queue.then(() => this.visual ? this.visual.request(method, params, {signal,timeoutMs}) : this.perform(method, params, signal, timeoutMs));
    this.queue = operation.catch(() => {});
    return operation;
  }
  perform(method, params, signal, timeoutMs) {
    if (signal?.aborted) return Promise.reject(new NativeError('CANCELLED', 'Request cancelled before execution.'));
    return new Promise((resolve, reject) => {
      this.start();
      const id = ++this.counter;
      const abort = () => this.reset(new NativeError('CANCELLED', 'Cancelled. The action may have partially completed; inspect before retrying.'));
      const budget = timeoutMs === undefined ? this.timeoutMs : Math.max(1, Math.min(timeoutMs, this.timeoutMs));
      const timer = setTimeout(() => this.reset(new NativeError('HELPER_TIMEOUT', 'Helper timed out. The action may have partially completed; inspect before retrying.')), budget);
      this.pending = { id, finish: (err, result) => {
        clearTimeout(timer); signal?.removeEventListener('abort', abort);
        this.pending = null; err ? reject(err) : resolve(result);
      }};
      signal?.addEventListener('abort', abort, { once: true });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }
  reset(error = new NativeError('CLOSED', 'Helper closed.')) {
    const child = this.child; this.child = null;
    this.pending?.finish(error);
    if (child) { child.stdin.destroy(); child.kill('SIGTERM'); }
  }
  close() { this.visual?.close(); this.reset(); }
}
