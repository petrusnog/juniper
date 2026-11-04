/** The real network, disk and PowerShell behind `UpdatePorts` (ADR-033). */
import { execFile, spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { isTrustedSignature } from '../../core/update/UpdateController';
import type { UpdateInfo, UpdatePorts } from '../../core/update/UpdateController';

const TIMEOUT_MS = 15_000;
const DOWNLOAD_TIMEOUT_MS = 10 * 60_000;

/** `ws://host:8765/v1/ws` -> `http://host:8765` (and `wss` -> `https`). */
export function httpBaseOf(wsUrl: string): string {
  const url = new URL(wsUrl);
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
  url.pathname = '';
  url.search = '';
  return url.toString().replace(/\/$/, '');
}

const SCRIPT =
  '$s = Get-AuthenticodeSignature -LiteralPath $env:JUNIPER_FILE;' +
  '$subject = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { "" };' +
  '@{ status = $s.Status.ToString(); subject = $subject } | ConvertTo-Json -Compress';

export interface PortOptions {
  wsUrl: string;
  bearer: string;
  tmpDir: string;
  quit: () => void;
}

export function createUpdatePorts(o: PortOptions): UpdatePorts {
  const base = httpBaseOf(o.wsUrl);
  const headers = { Authorization: `Bearer ${o.bearer}` };
  return {
    async fetchInfo(): Promise<UpdateInfo | null> {
      const res = await fetch(`${base}/desktop/update`, {
        headers,
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      return res.ok ? ((await res.json()) as UpdateInfo) : null;
    },

    async download(maxBytes: number): Promise<string> {
      const res = await fetch(`${base}/desktop/update/installer`, {
        headers,
        signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS),
      });
      if (!res.ok || res.body === null) throw new Error('download refused');
      const file = path.join(o.tmpDir, `Juniper-Setup-${crypto.randomBytes(6).toString('hex')}.exe`);
      let received = 0;
      const limited = Readable.fromWeb(res.body as never).on('data', (chunk: Buffer) => {
        received += chunk.length;
        if (received > maxBytes) limited.destroy(new Error('installer larger than announced'));
      });
      await pipeline(limited, fs.createWriteStream(file, { mode: 0o600 }));
      return file;
    },

    fileSize: async file => (await fsp.stat(file)).size,

    async sha256(file: string): Promise<string> {
      const hash = crypto.createHash('sha256');
      await pipeline(fs.createReadStream(file), hash);
      return hash.digest('hex');
    },

    signedBy(file: string, publisher: string): Promise<boolean> {
      return new Promise(resolve => {
        execFile(
          'powershell.exe',
          ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', SCRIPT],
          { env: { ...process.env, JUNIPER_FILE: file }, timeout: 30_000, windowsHide: true },
          (error, stdout) => resolve(error === null && isTrustedSignature(String(stdout), publisher)),
        );
      });
    },

    remove: file => fsp.rm(file, { force: true }),

    async install(file: string): Promise<void> {
      // NSIS silent install; the installer replaces the app and starts it again.
      spawn(file, ['/S'], { detached: true, stdio: 'ignore' }).unref();
      setTimeout(o.quit, 500);
    },
  };
}
