/**
 * A `PlatformRules` for tests that need the REAL file system on a Linux CI: the same decisions
 * as the Windows rules (home, fixed lists, sensitive and executable names), with POSIX paths.
 * The Windows syntax cases are tested in win32.test.ts with a fake probe.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import type { PlatformRules, Resolved } from '../../src/core/platform';

export function posixRules(home: string, blocked: string[] = []): PlatformRules {
  const p = path.posix;
  const isInside = (c: string, parent: string): boolean => {
    const a = p.normalize(c).replace(/\/+$/, '');
    const b = p.normalize(parent).replace(/\/+$/, '');
    return a === b || a.startsWith(`${b}/`);
  };
  return {
    name: 'posix-test',
    isCaseSensitive: true,
    homeDir: () => home,
    blockedPaths: () => [p.join(home, '.ssh'), p.join(home, 'AppData'), ...blocked],
    unsafeSyntax: x => (!x.startsWith('/') ? 'precisa ser absoluto' : /\.\.|\/\.\//.test(x) ? 'sem .. nem .' : null),
    isSensitiveName: n => /^(\.env(\..*)?|id_rsa.*|.*\.(kdbx|pem|key))$/i.test(n),
    isExecutableName: n => /\.(exe|bat|cmd|ps1|lnk|js)$/i.test(n),
    isProtectedFolderName: (n, parent) => n === '.vscode' || (parent === '.git' && n === 'hooks'),
    isInside,
    samePath: (a, b) => p.normalize(a).replace(/\/+$/, '') === p.normalize(b).replace(/\/+$/, ''),
    components: x => x.split('/').filter(Boolean),
    join: (...parts) => p.join(...parts),
    dirname: x => p.dirname(x),
    basename: x => p.basename(x),
    extname: x => p.extname(x),
    async canonicalize(x: string): Promise<Resolved> {
      const parts = x.split('/').filter(Boolean);
      let current = '/';
      let existing = '/';
      let viaLink = false;
      let missing: string[] = [];
      for (let i = 0; i < parts.length; i += 1) {
        const next = p.join(current, parts[i] as string);
        try {
          if ((await fs.lstat(next)).isSymbolicLink()) viaLink = true;
          existing = current = next;
        } catch {
          missing = parts.slice(i);
          break;
        }
      }
      const base = await fs.realpath(existing);
      return { real: missing.length ? p.join(base, ...missing) : base, viaLink, exists: missing.length === 0 };
    },
  };
}
