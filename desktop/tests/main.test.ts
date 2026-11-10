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
    'registerSchemesAsPrivileged',
    "protocol.handle('app'",
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
    expect(main).toContain('url === APP_PAGE'); // exactly our page, not any URL of ours
    expect(main).toContain('types.length > 0'); // an empty list of media types is not "audio only"
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

describe('packaging and self-update (ADR-033)', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')) as {
    build: { win: { artifactName: string }; electronFuses: Record<string, boolean>; asar: boolean; nsis: { oneClick: boolean } };
  };

  test('the installer has the name the brain serves', () => {
    expect(pkg.build.win.artifactName).toBe('Juniper-Setup.exe');
    const publish = fs.readFileSync(path.join(__dirname, '..', '..', 'scripts', 'publish_desktop.sh'), 'utf8');
    expect(publish).toContain('Juniper-Setup.exe');
  });

  test('the Electron fuses that matter are set', () => {
    expect(pkg.build.electronFuses).toMatchObject({
      runAsNode: false,
      enableNodeCliInspectArguments: false,
      enableNodeOptionsEnvironmentVariable: false,
      enableEmbeddedAsarIntegrityValidation: true,
      onlyLoadAppFromAsar: true,
      grantFileProtocolExtraPrivileges: false,
    });
    expect(pkg.build.asar).toBe(true);
  });

  test('the update applies itself when idle, and the installed app has no menu bar and the logo as icon', () => {
    const main = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'index.ts'), 'utf8');
    expect(main).toContain('canAutoApply(');
    expect(main).toContain('scheduleAutoUpdate');
    expect(main).toContain('Menu.setApplicationMenu(null)');
    expect(main).toContain('logo.png');
    const win = (pkg.build.win as { icon?: string }).icon;
    expect(win).toBe('build/icon.png');
  });

  test('a build with no publisher bakes in an empty one, so it never self-updates', () => {
    const info = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'buildInfo.ts'), 'utf8');
    expect(info).toContain("typeof __PUBLISHER__ === 'string' ? __PUBLISHER__ : ''");
  });

  test('the release workflow refuses to run without the certificate and checks the signer', () => {
    const wf = fs.readFileSync(path.join(__dirname, '..', '..', '.github', 'workflows', 'desktop-release.yml'), 'utf8');
    expect(wf).toContain('WIN_CSC_LINK');
    expect(wf).toContain('Get-AuthenticodeSignature');
    expect(wf).toContain('JUNIPER_PUBLISHER');
  });

  test('a push without the certificate skips the release instead of turning master red', () => {
    const wf = fs.readFileSync(path.join(__dirname, '..', '..', '.github', 'workflows', 'desktop-release.yml'), 'utf8');
    // The guard job decides; the release job needs it and runs only when it says "ready".
    expect(wf).toContain('guard:');
    expect(wf).toContain("needs.guard.outputs.ready == 'true'");
    // A manual run without the certificate still fails on purpose: nobody thinks it published.
    expect(wf).toMatch(/workflow_dispatch[\s\S]*exit 1/);
    expect(wf).toContain('::notice::');
  });

  test('the downloaded installer is verified before it is run', () => {
    const ports = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'update', 'ports.ts'), 'utf8');
    expect(ports).toContain('Get-AuthenticodeSignature');
    expect(ports).toContain("redirect: 'error'"); // the credential never follows a redirect
    expect(ports).toContain("flags: 'wx'");
    expect(ports).toContain("child.once('error'"); // a failed spawn is reported, not thrown
    expect(ports).toContain("'--updated', '--force-run'"); // so the installer starts the new version
    const controller = fs.readFileSync(path.join(__dirname, '..', 'src', 'core', 'update', 'UpdateController.ts'), 'utf8');
    expect(controller.indexOf('signedVersion(')).toBeLessThan(controller.indexOf('ports.install('));
  });
});
