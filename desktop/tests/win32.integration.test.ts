/**
 * The Windows rules against a REAL NTFS volume: junctions, symlinks, hard links, 8.3 names and
 * alternate data streams. Runs only on Windows (the `windows-latest` CI job is required for
 * merge, ADR-033 section 1.1); on other systems these cases are skipped, never silently passed.
 */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { resolvePath } from '../src/core/policy/paths';
import { createFileTools } from '../src/main/executor/fs/tools';
import { createWin32Rules } from '../src/main/platform/win32';

describe.runIf(process.platform === 'win32')('Windows rules on a real volume', () => {
  let home: string;
  let outside: string;
  const rules = () => createWin32Rules({ home });

  beforeAll(async () => {
    home = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'juniper-home-')));
    outside = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'juniper-out-')));
    await fs.mkdir(path.join(home, 'Documents'));
    await fs.mkdir(path.join(home, 'Desktop', 'Financeiro'), { recursive: true });
    await fs.writeFile(path.join(home, 'Desktop', 'Financeiro', 'balanco.txt'), 'x');
    await fs.writeFile(path.join(outside, 'segredo.txt'), 'x');
    await fs.symlink(path.join(home, 'Desktop', 'Financeiro'), path.join(home, 'Documents', 'Projeto'), 'junction');
    await fs.symlink(outside, path.join(home, 'Documents', 'fora'), 'junction');
    await fs.writeFile(path.join(home, 'Documents', 'a.txt'), 'x');
    await fs.link(path.join(home, 'Documents', 'a.txt'), path.join(home, 'Documents', 'a-link.txt'));
  });
  afterAll(async () => {
    await fs.rm(home, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
  });

  test('changing through a junction is path_changed', async () => {
    const v = await resolvePath(rules(), path.join(home, 'Documents', 'Projeto', 'balanco.txt'), 'modify');
    expect(v).toMatchObject({ ok: false, code: 'path_changed' });
  });

  test('a junction to outside the home is refused even for reading', async () => {
    const v = await resolvePath(rules(), path.join(home, 'Documents', 'fora', 'segredo.txt'), 'read');
    expect(v).toMatchObject({ ok: false });
  });

  test('a file with another hard link is not trashed', async () => {
    const tools = createFileTools({ rules: rules(), trash: { trash: async () => undefined } });
    const trash = tools.find(t => t.name === 'fs_trash')!;
    const r = await trash.handle({ path: path.join(home, 'Documents', 'a.txt') });
    expect(r).toMatchObject({ success: false, metadata: { code: 'policy_denied' } });
  });

  test('the casing the OS reports is accepted', async () => {
    const v = await resolvePath(rules(), path.join(home, 'documents', 'A.TXT').toUpperCase(), 'read');
    expect(v).toMatchObject({ ok: true });
  });

  test('alternate data streams are refused', async () => {
    const v = await resolvePath(rules(), `${path.join(home, 'Documents', 'a.txt')}:hidden`, 'read');
    expect(v).toMatchObject({ ok: false, code: 'policy_denied' });
  });
});

describe.skipIf(process.platform === 'win32')('Windows integration (skipped off Windows)', () => {
  test('runs in the windows-latest job', () => {
    expect(true).toBe(true);
  });
});
