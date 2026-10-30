// SPDX-License-Identifier: Apache-2.0
// Runtime seams only. Upstream collector/pipeline sources remain byte-identical.
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';

export const createDebugLogger = (tag: string) => ({
  isEnabled: () => !!process.env.CONTEXT_DEBUG,
  debug: (...a: unknown[]) => { if (process.env.CONTEXT_DEBUG) console.error(tag, ...a); },
  info: (...a: unknown[]) => { if (process.env.CONTEXT_DEBUG) console.error(tag, ...a); },
  warn: (...a: unknown[]) => console.error(tag, ...a),
  error: (...a: unknown[]) => console.error(tag, ...a),
});
export const fileExists = async (p: string) => fs.existsSync(p);
export function isCommandAvailable(command: string) {
  for (const dir of (process.env.PATH ?? '').split(path.delimiter)) {
    try { fs.accessSync(path.join(dir, command), fs.constants.X_OK); return { available: true }; }
    catch { /* try the next PATH entry */ }
  }
  return { available: false };
}
export function execCommand(command: string, args: string[], options: any = {}) {
  return new Promise((resolve, reject) => execFile(command, args,
    { encoding: 'utf8', ...options }, (error, stdout, stderr) => {
      if (error && !options.preserveOutputOnError) reject(error);
      else resolve({ stdout: String(stdout), stderr: String(stderr), code: error ? (error as any).code ?? 1 : 0 });
    }));
}
