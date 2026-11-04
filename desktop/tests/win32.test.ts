/**
 * Hostile-path corpus for the Windows rules (ADR-033 sections 4.1 and 4.2). The OS answers come
 * from a fake probe, so this runs anywhere; the same cases against a real NTFS volume with a
 * junction and a hard link are in win32.integration.test.ts (windows-latest only).
 */
import { describe, expect, test } from 'vitest';
import { resolvePath } from '../src/core/policy/paths';
import { createWin32Rules } from '../src/main/platform/win32';
import type { Probe } from '../src/main/platform/win32';

const HOME = 'C:\\Users\\petrus';

/** `links`: lexical path -> real target. `existing`: paths that exist (the rest is missing). */
function fakeProbe(existing: string[], links: Record<string, string> = {}): Probe {
  const norm = (p: string) => p.toLowerCase();
  // a path exists if it is listed, a link, or a parent of something that is (C:\Users, D:\ ...)
  const exist = new Set<string>();
  for (const full of [...existing, ...Object.keys(links)]) {
    const parts = full.split('\\');
    for (let i = 1; i <= parts.length; i += 1) exist.add(norm(parts.slice(0, i).join('\\')));
  }
  const link = new Map(Object.entries(links).map(([k, v]) => [norm(k), v]));
  return {
    async lstat(p) {
      if (!exist.has(norm(p)) && !link.has(norm(p))) throw new Error('ENOENT');
      return { isSymbolicLink: () => link.has(norm(p)) };
    },
    async realpath(p) {
      const parts = p.split('\\');
      let out = parts[0] ?? '';
      for (const part of parts.slice(1)) {
        out = `${out}\\${part}`;
        const target = link.get(norm(out));
        if (target) out = target;
      }
      return out;
    },
  };
}

const base = [HOME, `${HOME}\\Documents`, `${HOME}\\Documents\\nota.txt`, `${HOME}\\Desktop`];
const rules = (links: Record<string, string> = {}, extra: string[] = []) =>
  createWin32Rules({ home: HOME, probe: fakeProbe([...base, ...extra], links) });

const verdict = (path: unknown, access: 'read' | 'create' | 'modify', r = rules()) =>
  resolvePath(r, path, access);

describe('syntax refused before touching the disk', () => {
  test.each([
    ['UNC', '\\\\servidor\\share\\a.txt'],
    ['extended prefix', '\\\\?\\C:\\Users\\petrus\\a.txt'],
    ['device prefix', '\\\\.\\C:\\Users\\petrus\\a.txt'],
    ['forward slashes', 'C:/Users/petrus/Documents/nota.txt'],
    ['relative', 'Documents\\nota.txt'],
    ['drive-relative', 'C:Documents\\nota.txt'],
    ['dot-dot', `${HOME}\\Documents\\..\\.ssh\\id_rsa`],
    ['single dot', `${HOME}\\Documents\\.\\nota.txt`],
    ['env variable', '%USERPROFILE%\\Documents\\nota.txt'],
    ['alternate data stream', `${HOME}\\Documents\\nota.txt:secret`],
    ['::$DATA', `${HOME}\\Documents\\nota.txt::$DATA`],
    ['reserved CON', `${HOME}\\Documents\\CON`],
    ['reserved with extension', `${HOME}\\Documents\\con.txt`],
    ['reserved COM1', `${HOME}\\Documents\\COM1.log`],
    ['superscript COM¹', `${HOME}\\Documents\\COM¹`],
    ['CONIN$', `${HOME}\\Documents\\CONIN$`],
    ['trailing dot', `${HOME}\\Documents\\nota.txt.`],
    ['trailing space', `${HOME}\\Documents\\nota.txt `],
    ['8.3 short name', 'C:\\Users\\PETRUS~1\\Documents\\nota.txt'],
    ['double backslash', `${HOME}\\\\Documents\\nota.txt`],
    ['wildcard', `${HOME}\\Documents\\*.txt`],
    ['control char', `${HOME}\\Documents\\a\u0001.txt`],
    ['empty', ''],
    ['not a string', 42],
  ])('%s', async (_n, path) => {
    const v = await verdict(path, 'read');
    expect(v).toMatchObject({ ok: false, code: 'policy_denied' });
  });
});

describe('location', () => {
  test('a file in the home is fine', async () => {
    expect(await verdict(`${HOME}\\Documents\\nota.txt`, 'read')).toMatchObject({ ok: true, exists: true });
  });
  test('the casing of the OS is accepted (NTFS ignores case)', async () => {
    expect(await verdict(`${HOME.toUpperCase()}\\documents\\NOTA.TXT`, 'modify')).toMatchObject({ ok: true });
  });
  test.each(['C:\\Windows\\win.ini', 'C:\\Program Files\\x.txt', 'D:\\outro\\a.txt', 'C:\\Users\\outra\\a.txt'])(
    'outside the home: %s',
    async p => expect(await verdict(p, 'read')).toMatchObject({ ok: false }),
  );
  test('a prefix lookalike is not inside the home', async () => {
    expect(await verdict('C:\\Users\\petrus2\\a.txt', 'read')).toMatchObject({ ok: false });
  });
  test('a missing file can be created in an existing folder', async () => {
    expect(await verdict(`${HOME}\\Documents\\novo.txt`, 'create')).toMatchObject({ ok: true, exists: false });
  });
});

describe('fixed lists', () => {
  test.each([
    'AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\x.txt',
    'AppData\\Local\\Google\\Chrome\\User Data\\Default\\Login Data',
    '.ssh\\config',
    '.aws\\credentials',
    '.config\\gh\\hosts.yml',
    '.claude\\settings.json',
    '.gitconfig',
    '.npmrc',
    'tailscale\\x.txt',
  ])('blocked folder or file: %s', async rel => {
    const r = rules({}, [`${HOME}\\${rel}`.replace(/\\[^\\]+$/, '')]);
    expect(await verdict(`${HOME}\\${rel}`, 'read', r)).toMatchObject({ ok: false, code: 'policy_denied' });
  });

  test.each(['.env', '.env.local', 'senha.kdbx', 'chave.pem', 'id_rsa', 'id_ed25519.pub', 'NTUSER.DAT', 'vpn.ovpn'])(
    'sensitive name anywhere: %s',
    async name => expect(await verdict(`${HOME}\\Documents\\${name}`, 'read')).toMatchObject({ ok: false }),
  );

  test('a normal dotfile folder like .environment is not caught by .env', async () => {
    expect(await verdict(`${HOME}\\Documents\\.environment.txt`, 'create')).toMatchObject({ ok: true });
  });

  test.each(['programa.exe', 'run.bat', 'x.ps1', 'atalho.lnk', 'a.js', 'a.vbs', 'a.reg', 'a.url', 'LIB.DLL'])(
    'never created or changed: %s',
    async name => {
      expect(await verdict(`${HOME}\\Desktop\\${name}`, 'create')).toMatchObject({ ok: false });
      expect(await verdict(`${HOME}\\Desktop\\${name}`, 'modify')).toMatchObject({ ok: false });
    },
  );

  test('reading an executable name is not blocked by the write list', async () => {
    expect(await verdict(`${HOME}\\Desktop\\ajuda.js`, 'read')).toMatchObject({ ok: true });
  });

  test.each([`${HOME}\\Documents\\repo\\.git\\hooks\\pre-commit`, `${HOME}\\Documents\\repo\\.git\\config`, `${HOME}\\Documents\\p\\.vscode\\tasks.json`])(
    'code-running config is not written: %s',
    async p => {
      const dir = p.replace(/\\[^\\]+$/, '');
      const r = rules({}, [`${HOME}\\Documents\\repo`, `${HOME}\\Documents\\repo\\.git`, `${HOME}\\Documents\\p`, dir]);
      expect(await verdict(p, 'create', r)).toMatchObject({ ok: false });
    },
  );
});

describe('the approved path is the real path (links, junctions)', () => {
  const links = { [`${HOME}\\Documents\\Projeto`]: `${HOME}\\Desktop\\Financeiro` };
  const extra = [`${HOME}\\Desktop\\Financeiro`, `${HOME}\\Desktop\\Financeiro\\balanco.txt`, `${HOME}\\Documents\\Projeto`];

  test('changing through a junction is path_changed, never the other folder', async () => {
    const r = rules(links, extra);
    for (const access of ['modify', 'create'] as const) {
      const v = await verdict(`${HOME}\\Documents\\Projeto\\balanco.txt`, access, r);
      expect(v).toMatchObject({ ok: false, code: 'path_changed' });
    }
  });

  test('reading through a junction is allowed if the real place is inside the home', async () => {
    const v = await verdict(`${HOME}\\Documents\\Projeto\\balanco.txt`, 'read', rules(links, extra));
    expect(v).toMatchObject({ ok: true, real: `${HOME}\\Desktop\\Financeiro\\balanco.txt` });
  });

  test('a junction to a blocked folder cannot be used to read it', async () => {
    const l = { [`${HOME}\\Documents\\atalho`]: `${HOME}\\.ssh` };
    const r = rules(l, [`${HOME}\\.ssh`, `${HOME}\\.ssh\\id_rsa`, `${HOME}\\Documents\\atalho`]);
    expect(await verdict(`${HOME}\\Documents\\atalho\\id_rsa`, 'read', r)).toMatchObject({ ok: false });
  });

  test('a junction to outside the home cannot be used to read it', async () => {
    const l = { [`${HOME}\\Documents\\fora`]: 'D:\\segredos' };
    const r = rules(l, [`${HOME}\\Documents\\fora`, 'D:\\segredos', 'D:\\segredos\\a.txt']);
    expect(await verdict(`${HOME}\\Documents\\fora\\a.txt`, 'read', r)).toMatchObject({ ok: false });
  });
});
