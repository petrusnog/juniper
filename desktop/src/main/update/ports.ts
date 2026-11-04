/** The real network, disk and PowerShell behind `UpdatePorts` (ADR-033). */
import { execFile, spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { trustedVersion } from '../../core/update/UpdateController';
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

// The signer's simple name comes from Windows (GetNameInfo), not from parsing a subject string;
// the version is read from the same file the signature was checked on.
const SCRIPT =
  '$f = $env:JUNIPER_FILE;' +
  '$s = Get-AuthenticodeSignature -LiteralPath $f;' +
  '$name = if ($s.SignerCertificate) { $s.SignerCertificate.GetNameInfo("SimpleName", $false) } else { "" };' +
  '$v = (Get-Item -LiteralPath $f).VersionInfo.ProductVersion;' +
  '@{ status = $s.Status.ToString(); name = $name; version = $v } | ConvertTo-Json -Compress';

export interface PortOptions {
  wsUrl: string;
  bearer: string;
  /** A private folder of the app (not %TEMP%): the installer waits here between steps. */
  workDir: string;
  quit: () => void;
  /** Where a failed `spawn` is reported. */
  onInstallError?: () => void;
}

export function createUpdatePorts(o: PortOptions): UpdatePorts {
  const base = httpBaseOf(o.wsUrl);
  const headers = { Authorization: `Bearer ${o.bearer}` };
  return {
    async fetchInfo(): Promise<UpdateInfo | null> {
      const res = await fetch(`${base}/desktop/update`, {
        headers,
        redirect: 'error', // the credential never follows a redirect to another host
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      return res.ok ? ((await res.json()) as UpdateInfo) : null;
    },

    async download(maxBytes: number): Promise<string> {
      const res = await fetch(`${base}/desktop/update/installer`, {
        headers,
        redirect: 'error',
        signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS),
      });
      if (!res.ok || res.body === null) throw new Error('download refused');
      const announced = Number(res.headers.get('content-length'));
      if (!Number.isFinite(announced) || announced !== maxBytes) {
        throw new Error('installer size differs from the announced one');
      }
      await fsp.mkdir(o.workDir, { recursive: true });
      const file = path.join(o.workDir, `Juniper-Setup-${crypto.randomBytes(16).toString('hex')}.exe`);
      let received = 0;
      const limited = Readable.fromWeb(res.body as never).on('data', (chunk: Buffer) => {
        received += chunk.length;
        if (received > maxBytes) limited.destroy(new Error('installer larger than announced'));
      });
      try {
        // 'wx': never into a file that already exists (nor through a planted link).
        await pipeline(limited, fs.createWriteStream(file, { flags: 'wx', mode: 0o600 }));
      } catch (e) {
        await fsp.rm(file, { force: true }).catch(() => undefined); // no half installer is left
        throw e;
      }
      return file;
    },

    fileSize: async file => (await fsp.stat(file)).size,

    async sha256(file: string): Promise<string> {
      const hash = crypto.createHash('sha256');
      await pipeline(fs.createReadStream(file), hash);
      return hash.digest('hex');
    },

    signedVersion(file: string, publisher: string): Promise<string | null> {
      return new Promise(resolve => {
        execFile(
          'powershell.exe',
          ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', SCRIPT],
          { env: { ...process.env, JUNIPER_FILE: file }, timeout: 30_000, windowsHide: true },
          (error, stdout) => resolve(error === null ? trustedVersion(String(stdout), publisher) : null),
        );
      });
    },

    remove: file => fsp.rm(file, { force: true }),

    install(file: string): Promise<void> {
      // NSIS silent install; the installer replaces the app and starts it again. The app only
      // closes once the installer really started: a missing or blocked file is an error to show.
      return new Promise((resolve, reject) => {
        const child = spawn(file, ['/S'], { detached: true, stdio: 'ignore' });
        child.once('error', reject);
        child.once('spawn', () => {
          child.unref();
          resolve();
          setTimeout(o.quit, 500);
        });
      });
    },
  };
}
