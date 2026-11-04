/**
 * Windows implementation of `PlatformRules` (ADR-033 section 1.1).
 *
 * Every comparison and every path operation uses `path.win32` explicitly, never the `path` of
 * the system the code happens to run on: a test that passes on Linux must mean the same thing.
 * The real answers (real path, links) come from the injected `Probe`, which is Node's `fs` in
 * the app and a fake in unit tests.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import type { PlatformRules, Resolved } from '../../core/platform';

const win = path.win32;

export interface Probe {
  lstat(p: string): Promise<{ isSymbolicLink(): boolean }>;
  /** `fs.realpath.native` on Windows: asks the OS (GetFinalPathNameByHandle). */
  realpath(p: string): Promise<string>;
}

const nodeProbe: Probe = {
  lstat: p => fs.lstat(p),
  realpath: p => fs.realpath(p, { encoding: 'utf8' }),
};

export interface Win32Options {
  home: string;
  /** Extra folders to block (the app's own data folder). */
  appDataDir?: string;
  probe?: Probe;
}

const RESERVED = /^(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])$/i;
const FORBIDDEN_CHARS = /[<>"|?*\x00-\x1f]/;  
const SHORT_NAME = /~\d/;
const SENSITIVE_NAME =
  /^(\.env(\..*)?|ntuser\.(dat|ini).*|id_rsa.*|id_ed25519.*|.*\.(kdbx|pem|key|pfx|p12|ppk|rdp|ovpn|wallet|keystore))$/i;
const EXECUTABLE =
  /\.(exe|dll|bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|hta|lnk|scr|msi|msc|reg|url|cpl|jar|pif)$/i;
const HOME_BLOCKED = [
  'AppData',
  '.ssh',
  '.aws',
  '.gnupg',
  '.azure',
  '.kube',
  '.docker',
  '.oci',
  '.terraform',
  '.config',
  '.claude',
  '.codex',
  '.juniper',
  '.npmrc',
  '.pypirc',
  '.netrc',
  '.git-credentials',
  '.gitconfig',
  'tailscale',
];
const SYSTEM_BLOCKED = [
  'C:\\Windows',
  'C:\\Program Files',
  'C:\\Program Files (x86)',
  'C:\\ProgramData',
];
const MAX_PATH_CHARS = 1024;

/** Case-insensitive key: NFC, then the upper/lower round trip the file system roughly does. */
const key = (s: string): string => s.normalize('NFC').toUpperCase().toLowerCase();

export function createWin32Rules(options: Win32Options): PlatformRules {
  const probe = options.probe ?? nodeProbe;
  const home = win.normalize(options.home);

  const samePath = (a: string, b: string): boolean =>
    key(win.normalize(a).replace(/\\+$/, '')) === key(win.normalize(b).replace(/\\+$/, ''));

  const isInside = (child: string, parent: string): boolean => {
    const c = key(win.normalize(child).replace(/\\+$/, ''));
    const p = key(win.normalize(parent).replace(/\\+$/, ''));
    return c === p || c.startsWith(`${p}\\`);
  };

  const components = (p: string): string[] => {
    const root = win.parse(p).root;
    return p
      .slice(root.length)
      .split('\\')
      .filter(part => part !== '');
  };

  const unsafeSyntax = (p: string): string | null => {
    if (p.length > MAX_PATH_CHARS) return 'caminho comprido demais';
    if (p.startsWith('\\\\')) return 'caminhos de rede (UNC) e prefixos \\\\?\\ não são permitidos';
    if (p.includes('/')) return 'use barras invertidas; a barra normal não é aceita';
    if (!/^[A-Za-z]:\\/.test(p)) return 'precisa ser um caminho absoluto completo, como C:\\Users\\...';
    if (p.indexOf(':', 2) !== -1) return 'fluxos alternativos (:) não são permitidos';
    if (FORBIDDEN_CHARS.test(p.slice(2))) return 'o caminho tem caracteres inválidos';
    if (p.includes('%')) return 'variáveis (%VAR%) não são expandidas; use o caminho completo';
    const tail = p.slice(3).replace(/\\$/, ''); // one trailing backslash is just a folder
    for (const part of tail === '' ? [] : tail.split('\\')) {
      if (part === '') return 'caminho com barras duplas';
      if (part === '.' || part === '..') return 'o caminho não pode ter «.» nem «..»';
      if (part.endsWith('.') || part.endsWith(' ')) return 'nome terminado em ponto ou espaço';
      if (SHORT_NAME.test(part)) return 'nomes curtos (8.3, com ~) não são aceitos';
      const base = part.split('.')[0]?.trimEnd() ?? '';
      if (RESERVED.test(base)) return `«${part}» é um nome reservado do Windows`;
    }
    return null;
  };

  return {
    name: 'win32',
    isCaseSensitive: false,
    homeDir: () => home,
    blockedPaths: () => [
      ...HOME_BLOCKED.map(name => win.join(home, name)),
      ...SYSTEM_BLOCKED,
      ...(options.appDataDir ? [options.appDataDir] : []),
    ],
    unsafeSyntax,
    isSensitiveName: name => SENSITIVE_NAME.test(name),
    isExecutableName: name => EXECUTABLE.test(name),
    isProtectedFolderName: (name, parent) =>
      key(name) === '.vscode' ||
      (key(parent) === '.git' && ['hooks', 'config'].includes(key(name))),
    join: (...parts) => win.join(...parts),
    dirname: p => win.dirname(p),
    basename: p => win.basename(p),
    extname: p => win.extname(p),
    isInside,
    samePath,
    components,
    async canonicalize(p: string): Promise<Resolved> {
      const parts = components(p);
      let current = win.parse(p).root;
      let viaLink = false;
      let existing = current;
      let missing: string[] = [];
      for (let i = 0; i < parts.length; i += 1) {
        const next = win.join(current, parts[i] ?? '');
        try {
          const st = await probe.lstat(next);
          if (st.isSymbolicLink()) viaLink = true;
          existing = next;
          current = next;
        } catch {
          missing = parts.slice(i);
          break;
        }
      }
      const base = await probe.realpath(existing);
      const real = missing.length > 0 ? win.join(base, ...missing) : base;
      return { real, viaLink, exists: missing.length === 0 };
    },
  };
}
