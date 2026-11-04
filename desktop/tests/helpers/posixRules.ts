/**
 * A `PlatformRules` for tests that need the REAL file system on a Linux CI: the same decisions
 * as the Windows rules (home, fixed lists, sensitive and executable names), with POSIX paths.
 * The Windows syntax cases are tested in win32.test.ts with a fake probe.
 */
import { realpath } from 'node:fs';
import fs from 'node:fs/promises';
import { promisify } from 'node:util';
import path from 'node:path';
import type { PlatformRules, Resolved } from '../../src/core/platform';
import { createWin32Rules } from '../../src/main/platform/win32';

const realpathNative = promisify(realpath.native);

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

/**
 * The rules these file tests run under: the REAL Windows rules on Windows (the CI runs them on
 * NTFS), the POSIX stand-in elsewhere. Same decisions, so the same tests prove both.
 */
export function testRules(home: string): PlatformRules {
  return process.platform === 'win32' ? createWin32Rules({ home }) : posixRules(home);
}

/** A directory link that works without privileges: a junction on Windows, a symlink elsewhere. */
export async function linkDir(target: string, link: string): Promise<void> {
  await fs.symlink(target, link, process.platform === 'win32' ? 'junction' : undefined);
}

/** `realpath` as the OS reports it (expands 8.3 names such as RUNNER~1 on Windows). */
export const realDir = (p: string): Promise<string> => realpathNative(p);
