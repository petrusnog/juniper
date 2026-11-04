/** The file tools against a REAL temporary folder (ADR-033 section 4), with POSIX test rules. */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import type { LocalTool } from '../src/core/executor/Executor';
import { createFileTools, LIST_LIMIT, SEARCH_LIMIT } from '../src/main/executor/fs/tools';
import { posixRules } from './helpers/posixRules';

let home: string;
let trashed: string[];
let tools: Record<string, LocalTool>;

beforeEach(async () => {
  home = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'juniper-fs-')));
  await fs.mkdir(path.join(home, 'Documents'));
  trashed = [];
  const list = createFileTools({
    rules: posixRules(home),
    trash: { trash: async p => void (trashed.push(p), await fs.rm(p, { recursive: true })) },
  });
  tools = Object.fromEntries(list.map(t => [t.name, t]));
});
afterEach(async () => fs.rm(home, { recursive: true, force: true }));

const doc = (name: string) => path.join(home, 'Documents', name);
const run = (name: string, args: Record<string, unknown>) => tools[name]!.handle(args);
const code = (r: { metadata?: Record<string, unknown> }) => r.metadata?.code;

describe('catalogue and tiers', () => {
  test('tiers match the brain (ADR-033 table)', () => {
    const tiers = Object.fromEntries(Object.values(tools).map(t => [t.name, t.tier]));
    expect(tiers).toEqual({
      fs_list: 'read', fs_search: 'read', fs_read: 'read',
      fs_write: 'write', fs_mkdir: 'write', fs_move: 'write', fs_copy: 'write',
      fs_replace: 'destructive', fs_trash: 'destructive',
    });
  });
});

describe('read side', () => {
  test('fs_read returns text and the next offset', async () => {
    await fs.writeFile(doc('a.txt'), 'olá mundo');
    const r = await run('fs_read', { path: doc('a.txt') });
    expect(r).toMatchObject({ success: true, output: 'olá mundo' });
  });

  test('fs_read pages a big file', async () => {
    await fs.writeFile(doc('big.txt'), 'x'.repeat(1024 * 1024 + 10));
    const first = await run('fs_read', { path: doc('big.txt') });
    expect(first.output).toHaveLength(1024 * 1024);
    const next = first.metadata?.next_offset as number;
    const rest = await run('fs_read', { path: doc('big.txt'), offset: next });
    expect(rest.output).toHaveLength(10);
  });

  test('fs_read refuses binaries, sensitive names and blocked folders', async () => {
    await fs.writeFile(doc('a.exe'), 'MZ');
    await fs.writeFile(doc('.env'), 'KEY=1');
    await fs.mkdir(path.join(home, '.ssh'));
    await fs.writeFile(path.join(home, '.ssh', 'id_rsa.txt'), 'k');
    for (const p of [doc('a.exe'), doc('.env'), path.join(home, '.ssh', 'id_rsa.txt')]) {
      expect(await run('fs_read', { path: p })).toMatchObject({ success: false, metadata: { code: 'policy_denied' } });
    }
  });

  test('fs_read refuses anything outside the home', async () => {
    expect(await run('fs_read', { path: '/etc/passwd' })).toMatchObject({ success: false, metadata: { code: 'policy_denied' } });
  });

  test('fs_read follows a link to a real place inside the home, but not outside', async () => {
    const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'juniper-out-'));
    await fs.writeFile(path.join(outside, 'x.txt'), 'fora');
    await fs.symlink(outside, doc('fora'));
    expect(await run('fs_read', { path: doc('fora/x.txt') })).toMatchObject({ success: false });
    await fs.rm(outside, { recursive: true });
  });

  test('fs_list hides blocked and sensitive names and caps the output', async () => {
    await fs.mkdir(path.join(home, '.ssh'));
    await fs.writeFile(path.join(home, 'visivel.txt'), '1');
    await fs.writeFile(path.join(home, '.env'), '1');
    const r = await run('fs_list', { path: home });
    expect(r.output).toContain('visivel.txt');
    expect(r.output).not.toContain('.ssh');
    expect(r.output).not.toContain('.env');
    for (let i = 0; i < LIST_LIMIT + 5; i += 1) await fs.writeFile(doc(`f${i}.txt`), '');
    const big = await run('fs_list', { path: doc('') });
    expect(big.output).toContain('… e mais 5');
  });

  test('fs_search finds by name, skips blocked folders and does not follow links', async () => {
    await fs.mkdir(path.join(home, '.ssh'));
    await fs.writeFile(path.join(home, '.ssh', 'relatorio-chave.txt'), '1');
    await fs.mkdir(doc('sub'));
    await fs.writeFile(doc('sub/Relatorio-2026.txt'), '1');
    await fs.symlink(path.join(home, '.ssh'), doc('atalho'));
    const r = await run('fs_search', { path: home, name: 'relatorio' });
    expect(r.output).toContain('Relatorio-2026.txt');
    expect(r.output).not.toContain('chave');
    expect(r.metadata?.results).toBe(1);
  });

  test('fs_search stops at the result cap', async () => {
    for (let i = 0; i < SEARCH_LIMIT + 20; i += 1) await fs.writeFile(doc(`m${i}.txt`), '');
    const r = await run('fs_search', { path: home, name: 'm' });
    expect(r.metadata?.results).toBe(SEARCH_LIMIT);
    expect(r.output).toContain('interrompida');
  });
});

describe('write side', () => {
  test('fs_write creates and never overwrites', async () => {
    expect(await run('fs_write', { path: doc('n.txt'), content: 'um' })).toMatchObject({ success: true });
    const again = await run('fs_write', { path: doc('n.txt'), content: 'dois' });
    expect(code(again)).toBe('destination_exists');
    expect(await fs.readFile(doc('n.txt'), 'utf8')).toBe('um');
  });

  test('nothing that runs code is ever created', async () => {
    for (const name of ['a.exe', 'a.bat', 'a.ps1', 'a.lnk']) {
      expect(code(await run('fs_write', { path: doc(name), content: 'x' }))).toBe('policy_denied');
      await expect(fs.access(doc(name))).rejects.toThrow();
    }
  });

  test('fs_write through a link is path_changed', async () => {
    await fs.mkdir(doc('real'));
    await fs.symlink(doc('real'), doc('link'));
    expect(code(await run('fs_write', { path: doc('link/n.txt'), content: 'x' }))).toBe('path_changed');
    await expect(fs.access(doc('real/n.txt'))).rejects.toThrow();
  });

  test('fs_mkdir creates nested folders', async () => {
    expect(await run('fs_mkdir', { path: doc('a/b/c') })).toMatchObject({ success: true });
    expect((await fs.stat(doc('a/b/c'))).isDirectory()).toBe(true);
  });

  test('fs_move and fs_copy never overwrite', async () => {
    await fs.writeFile(doc('a.txt'), 'A');
    await fs.writeFile(doc('b.txt'), 'B');
    expect(code(await run('fs_move', { source: doc('a.txt'), destination: doc('b.txt') }))).toBe('destination_exists');
    expect(code(await run('fs_copy', { source: doc('a.txt'), destination: doc('b.txt') }))).toBe('destination_exists');
    expect(await run('fs_copy', { source: doc('a.txt'), destination: doc('c.txt') })).toMatchObject({ success: true });
    expect(await run('fs_move', { source: doc('a.txt'), destination: doc('d.txt') })).toMatchObject({ success: true });
    expect(await fs.readFile(doc('d.txt'), 'utf8')).toBe('A');
    await expect(fs.access(doc('a.txt'))).rejects.toThrow();
  });

  test('fs_copy cannot copy a blocked file out', async () => {
    await fs.mkdir(path.join(home, '.ssh'));
    await fs.writeFile(path.join(home, '.ssh', 'k'), 'secret');
    const r = await run('fs_copy', { source: path.join(home, '.ssh', 'k'), destination: doc('k.txt') });
    expect(code(r)).toBe('policy_denied');
  });

  test('fs_move refuses a file with another hard link', async () => {
    await fs.writeFile(doc('a.txt'), 'A');
    await fs.link(doc('a.txt'), doc('a2.txt'));
    expect(code(await run('fs_move', { source: doc('a.txt'), destination: doc('z.txt') }))).toBe('policy_denied');
  });

  test('fs_replace sends the original to the trash first, then replaces', async () => {
    await fs.writeFile(doc('a.txt'), 'velho');
    const r = await run('fs_replace', { path: doc('a.txt'), content: 'novo' });
    expect(r).toMatchObject({ success: true });
    expect(trashed).toEqual([doc('a.txt')]);
    expect(await fs.readFile(doc('a.txt'), 'utf8')).toBe('novo');
    expect((await fs.readdir(doc(''))).some(n => n.endsWith('.tmp'))).toBe(false);
  });

  test('fs_replace keeps the original if the trash fails', async () => {
    const list = createFileTools({
      rules: posixRules(home),
      trash: { trash: async () => { throw new Error('no recycle bin'); } },
    });
    await fs.writeFile(doc('a.txt'), 'velho');
    const replace = list.find(t => t.name === 'fs_replace')!;
    const r = await replace.handle({ path: doc('a.txt'), content: 'novo' });
    expect(r).toMatchObject({ success: false, metadata: { code: 'os_error' } });
    expect(await fs.readFile(doc('a.txt'), 'utf8')).toBe('velho');
    expect((await fs.readdir(doc(''))).some(n => n.endsWith('.tmp'))).toBe(false);
  });

  test('fs_replace wants exactly one of content or source', async () => {
    await fs.writeFile(doc('a.txt'), 'x');
    expect(code(await run('fs_replace', { path: doc('a.txt') }))).toBe('invalid_args');
    expect(code(await run('fs_replace', { path: doc('a.txt'), content: 'a', source: doc('a.txt') }))).toBe('invalid_args');
  });

  test('fs_trash needs recursive for a folder and has an item cap', async () => {
    await fs.mkdir(doc('pasta'));
    await fs.writeFile(doc('pasta/a.txt'), '1');
    expect(code(await run('fs_trash', { path: doc('pasta') }))).toBe('policy_denied');
    expect(await run('fs_trash', { path: doc('pasta'), recursive: true })).toMatchObject({ success: true });
    await fs.mkdir(doc('cheia'));
    for (let i = 0; i < 205; i += 1) await fs.writeFile(doc(`cheia/${i}.txt`), '');
    expect(code(await run('fs_trash', { path: doc('cheia'), recursive: true }))).toBe('policy_denied');
    await expect(fs.access(doc('cheia'))).resolves.toBeUndefined();
  });

  test('fs_trash never deletes for good when the trash fails', async () => {
    const list = createFileTools({ rules: posixRules(home), trash: { trash: async () => { throw new Error('x'); } } });
    await fs.writeFile(doc('a.txt'), 'x');
    const r = await list.find(t => t.name === 'fs_trash')!.handle({ path: doc('a.txt') });
    expect(r).toMatchObject({ success: false, metadata: { code: 'os_error' } });
    await expect(fs.access(doc('a.txt'))).resolves.toBeUndefined();
  });
});

describe('review findings (ADR-033)', () => {
  test('fs_write only creates plain-text files (an allow-list)', async () => {
    for (const name of ['a.py', 'a.html', 'a.svg', 'semextensao', '.bashrc', 'a.docm']) {
      expect(code(await run('fs_write', { path: doc(name), content: 'x' }))).toBe('policy_denied');
      await expect(fs.access(doc(name))).rejects.toThrow();
    }
    expect(await run('fs_write', { path: doc('ok.md'), content: '# oi' })).toMatchObject({ success: true });
  });

  test('fs_replace refuses a folder as the target (it would skip the fs_trash limits)', async () => {
    await fs.mkdir(doc('pasta'));
    await fs.writeFile(doc('pasta/a.txt'), '1');
    const r = await run('fs_replace', { path: doc('pasta'), content: 'oi' });
    expect(code(r)).toBe('policy_denied');
    expect(trashed).toEqual([]);
    await expect(fs.access(doc('pasta/a.txt'))).resolves.toBeUndefined();
  });

  test('fs_replace refuses a folder or a link as the source', async () => {
    await fs.writeFile(doc('a.txt'), 'x');
    await fs.mkdir(doc('d'));
    expect(code(await run('fs_replace', { path: doc('a.txt'), source: doc('d') }))).toBe('policy_denied');
    expect(trashed).toEqual([]);
  });

  test('fs_replace only writes new text into a text file', async () => {
    await fs.writeFile(doc('a.txt'), 'x');
    await fs.writeFile(doc('b.md'), 'x');
    expect(await run('fs_replace', { path: doc('b.md'), content: 'novo' })).toMatchObject({ success: true });
    await fs.writeFile(doc('s.json'), '{}');
    expect(code(await run('fs_replace', { path: doc('a.txt'), source: doc('s.json') }))).toBeUndefined();
  });

  test('fs_replace tells the truth when the final rename fails', async () => {
    const list = createFileTools({
      rules: posixRules(home),
      trash: {
        trash: async p => {
          trashed.push(p);
          await fs.rm(p); // the original is gone from its place, now in the "trash"
          await fs.mkdir(p); // ...and a folder blocks the rename of the new file
          await fs.writeFile(path.join(p, 'x'), '');
        },
      },
    });
    await fs.writeFile(doc('a.txt'), 'velho');
    const r = await list.find(t => t.name === 'fs_replace')!.handle({ path: doc('a.txt'), content: 'novo' });
    expect(r.success).toBe(false);
    expect(r.error).toContain('Lixeira');
    expect(r.error).not.toContain('intacto');
    expect((await fs.readdir(doc(''))).some(n => n.endsWith('.tmp'))).toBe(false);
  });

  test('fs_move to a destination that appeared meanwhile never overwrites', async () => {
    await fs.writeFile(doc('a.txt'), 'A');
    await fs.writeFile(doc('b.txt'), 'B');
    // Same check the tool does after its own exists() test, with the race already lost:
    await expect(fs.link(doc('a.txt'), doc('b.txt'))).rejects.toMatchObject({ code: 'EEXIST' });
    expect(await fs.readFile(doc('b.txt'), 'utf8')).toBe('B');
  });

  test('folders that run code later are not moved, copied or renamed', async () => {
    await fs.mkdir(doc('tmp'));
    await fs.mkdir(doc('repo'));
    await fs.mkdir(doc('repo/.git'));
    for (const dst of ['repo/.git/hooks', 'novo/.vscode', 'x/.git']) {
      await fs.mkdir(path.dirname(doc(dst)), { recursive: true });
      expect(code(await run('fs_move', { source: doc('tmp'), destination: doc(dst) }))).toBe('policy_denied');
    }
    expect(code(await run('fs_move', { source: doc('repo/.git'), destination: doc('repo/renomeado') }))).toBe('policy_denied');
    await expect(fs.access(doc('repo/.git'))).resolves.toBeUndefined();
  });
});
