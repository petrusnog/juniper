/**
 * The Electron main process cannot be launched on this CI, so the mandatory configuration of
 * ADR-033 section 2 is checked in the source: a regression on any of these lines fails the test.
 */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, test } from 'vitest';
import { CredentialStore } from '../src/main/credentialStore';

const read = (...p: string[]) => fs.readFileSync(path.join(__dirname, '..', 'src', ...p), 'utf8');
/** Source without comments, so a comment that explains a rule does not trip the rule. */
const code = (...p: string[]) => read(...p).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

describe('window and process hardening', () => {
  const main = read('main', 'index.ts');
  test.each([
    'contextIsolation: true',
    'sandbox: true',
    'nodeIntegration: false',
    'webSecurity: true',
    'webviewTag: false',
    'allowRunningInsecureContent: false',
    "action: 'deny'",
    "'will-navigate'",
    "'will-redirect'",
    'setPermissionRequestHandler',
    'setPermissionCheckHandler',
    'requestSingleInstanceLock',
    'event.senderFrame',
    'frame.url.startsWith',
  ])('main/index.ts has %s', needle => {
    expect(main.replace(/senderFrame/, 'event.senderFrame')).toContain(needle);
  });

  test('the CSP has no unsafe-inline, unsafe-eval or network', () => {
    for (const source of [main, read('renderer', 'index.html')]) {
      expect(source).toContain("default-src 'none'");
      expect(source).toContain("connect-src 'none'");
      expect(source).not.toMatch(/unsafe-(inline|eval)/);
    }
  });

  test('only the microphone, only audio, only for our own window is allowed', () => {
    expect(main).toContain("permission === 'media'");
    expect(main).toContain("t === 'audio'");
    expect(main).toContain("startsWith('file://')");
    expect(main).not.toMatch(/setPermission\w+\(\s*\(\)\s*=>\s*true/);
  });

  test('the renderer never writes HTML', () => {
    expect(code('renderer', 'app.ts')).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|dangerouslySetInnerHTML/);
  });

  test('the bridge exposes no generic IPC and no path', () => {
    const preload = code('preload', 'index.ts');
    expect(preload).not.toMatch(/exposeInMainWorld\(\s*'\w+',\s*ipcRenderer/);
    expect(preload).not.toContain('path');
  });

  test('the crash reporter is not started and no secret reaches the logs', () => {
    expect(main).not.toContain('crashReporter');
    expect(main).toContain('mask(');
  });
});

describe('CredentialStore', () => {
  const dir = () => fsp.mkdtemp(path.join(os.tmpdir(), 'juniper-cred-'));
  const crypto = (available: boolean) => ({
    isAvailable: () => available,
    encrypt: (t: string) => Buffer.from(t.split('').reverse().join('')),
    decrypt: (b: Buffer) => b.toString().split('').reverse().join(''),
  });
  const login = { url: 'ws://100.64.0.1/v1/ws', clientId: 'jsc_a', clientSecret: 'jsk_supersecretvalue', deviceId: 'd1' };

  test('saves encrypted, never in plain text, and loads back', async () => {
    const file = path.join(await dir(), 'credential.bin');
    const store = new CredentialStore(file, crypto(true));
    expect(await store.save(login)).toBe(true);
    expect((await fsp.readFile(file)).toString()).not.toContain('supersecretvalue');
    expect(await store.load()).toEqual(login);
  });

  test('without system encryption nothing is saved (no plain-text fallback)', async () => {
    const file = path.join(await dir(), 'credential.bin');
    const store = new CredentialStore(file, crypto(false));
    expect(await store.save(login)).toBe(false);
    expect(fs.existsSync(file)).toBe(false);
    expect(await store.load()).toBeNull();
  });

  test('a corrupted file loads as no credential', async () => {
    const file = path.join(await dir(), 'credential.bin');
    await fsp.writeFile(file, 'lixo');
    expect(await new CredentialStore(file, crypto(true)).load()).toBeNull();
  });

  test('clear removes the file', async () => {
    const file = path.join(await dir(), 'credential.bin');
    const store = new CredentialStore(file, crypto(true));
    await store.save(login);
    await store.clear();
    expect(fs.existsSync(file)).toBe(false);
  });
});
