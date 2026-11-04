/**
 * The file tools on the desktop (ADR-033 section 4): list, search, read, write, mkdir, move,
 * copy, replace and trash. Every path goes through `resolvePath` first, so the decision (home,
 * fixed lists, the approved path is the real path) is the one tested in `core/policy`.
 *
 * Failures are typed results, never exceptions. Nothing here writes file content to a log.
 */
import fsp from 'node:fs/promises';
import { constants } from 'node:fs';
import type { LocalTool, ToolOutcome } from '../../../core/executor/Executor';
import type { PlatformRules } from '../../../core/platform';
import { resolvePath } from '../../../core/policy/paths';
import type { Access } from '../../../core/policy/paths';

/** Sends something to the Recycle Bin (Electron: `shell.trashItem`). Never deletes for good. */
export interface Trash {
  trash(p: string): Promise<void>;
}

export const READ_LIMIT = 1024 * 1024;
export const READ_BUDGET = 5 * 1024 * 1024; // per window (the executor does not see turns)
export const BUDGET_WINDOW_MS = 2 * 60 * 1000;
export const LIST_LIMIT = 500;
export const SEARCH_LIMIT = 200;
export const SEARCH_MS = 5000;
export const SEARCH_DEPTH = 8;
export const TRASH_ITEM_LIMIT = 200;
const TEXT_EXTENSIONS = new Set(['.txt', '.md', '.csv', '.json']);

const fail = (code: string, error: string): ToolOutcome => ({
  success: false,
  error,
  metadata: { code },
});

const ok = (output: string, metadata?: Record<string, unknown>): ToolOutcome => ({
  success: true,
  output,
  ...(metadata ? { metadata } : {}),
});

export interface FsDeps {
  rules: PlatformRules;
  trash: Trash;
  now?: () => number;
}

export function createFileTools(deps: FsDeps): LocalTool[] {
  const { rules, trash } = deps;
  const now = deps.now ?? Date.now;
  let budget = { startedAt: 0, used: 0 };

  const guarded = async (
    raw: unknown,
    access: Access,
  ): Promise<{ real: string; exists: boolean } | ToolOutcome> => {
    const verdict = await resolvePath(rules, raw, access);
    if (verdict.ok) return { real: verdict.real, exists: verdict.exists };
    return fail(verdict.code, `Não posso mexer nesse caminho: ${verdict.reason}.`);
  };
  const isOutcome = (v: { real: string } | ToolOutcome): v is ToolOutcome => 'success' in v;

  const hiddenFromListing = (name: string): boolean =>
    rules.isSensitiveName(name) ||
    rules.blockedPaths().some(b => rules.samePath(rules.basename(b), name));

  async function countItems(dir: string, limit: number): Promise<number> {
    let count = 0;
    const stack = [dir];
    while (stack.length > 0 && count <= limit) {
      const current = stack.pop() as string;
      for (const entry of await fsp.readdir(current, { withFileTypes: true })) {
        count += 1;
        if (entry.isDirectory() && !entry.isSymbolicLink()) {
          stack.push(rules.join(current, entry.name));
        }
        if (count > limit) break;
      }
    }
    return count;
  }

  const requireSingleLink = async (real: string): Promise<ToolOutcome | null> => {
    const st = await fsp.lstat(real);
    if (st.isFile() && st.nlink > 1) {
      return fail('policy_denied', 'Esse arquivo tem mais de um nome (hard link); não altero.');
    }
    return null;
  };

  const tools: LocalTool[] = [
    {
      name: 'fs_list',
      description: 'Lista os arquivos e pastas de uma pasta do computador (até 500 entradas).',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string' } },
        required: ['path'],
      },
      tier: 'read',
      async handle(args) {
        const r = await guarded(args.path, 'read');
        if (isOutcome(r)) return r;
        let entries;
        try {
          entries = await fsp.readdir(r.real, { withFileTypes: true });
        } catch {
          return fail('os_error', 'Não consegui listar essa pasta.');
        }
        const shown = entries.filter(e => !hiddenFromListing(e.name));
        const lines = shown
          .slice(0, LIST_LIMIT)
          .map(e => `${e.isDirectory() ? 'pasta  ' : 'arquivo'} ${e.name}`);
        const more = shown.length > LIST_LIMIT ? `\n… e mais ${shown.length - LIST_LIMIT}` : '';
        return ok(lines.join('\n') + more || '(pasta vazia)', { entries: shown.length });
      },
    },
    {
      name: 'fs_search',
      description: 'Procura arquivos e pastas pelo nome (não pelo conteúdo): até 200 resultados.',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string' }, name: { type: 'string' } },
        required: ['path', 'name'],
      },
      tier: 'read',
      async handle(args) {
        const r = await guarded(args.path, 'read');
        if (isOutcome(r)) return r;
        const needle = String(args.name).toLowerCase();
        if (needle === '') return fail('invalid_args', 'Diga parte do nome a procurar.');
        const deadline = now() + SEARCH_MS;
        const found: string[] = [];
        const queue: [string, number][] = [[r.real, 0]];
        let truncated = false;
        while (queue.length > 0) {
          const [dir, depth] = queue.shift() as [string, number];
          if (now() > deadline || found.length >= SEARCH_LIMIT) {
            truncated = true;
            break;
          }
          let entries;
          try {
            entries = await fsp.readdir(dir, { withFileTypes: true });
          } catch {
            continue;
          }
          for (const e of entries) {
            const full = rules.join(dir, e.name);
            if (hiddenFromListing(e.name) || e.isSymbolicLink()) continue;
            if (rules.blockedPaths().some(b => rules.isInside(full, b))) continue;
            if (e.name.toLowerCase().includes(needle)) found.push(full);
            if (e.isDirectory() && depth < SEARCH_DEPTH) queue.push([full, depth + 1]);
            if (found.length >= SEARCH_LIMIT) {
              truncated = true;
              break;
            }
          }
        }
        const tail = truncated ? '\n(busca interrompida: limite de resultados ou de tempo)' : '';
        return ok(found.length > 0 ? found.join('\n') + tail : `Nada com «${args.name}».${tail}`, {
          results: found.length,
        });
      },
    },
    {
      name: 'fs_read',
      description: 'Lê um arquivo de texto (.txt, .md, .csv, .json), até 1 MB por chamada.',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string' }, offset: { type: 'integer' } },
        required: ['path'],
      },
      tier: 'read',
      async handle(args) {
        const r = await guarded(args.path, 'read');
        if (isOutcome(r)) return r;
        const ext = rules.extname(r.real).toLowerCase();
        if (!TEXT_EXTENSIONS.has(ext)) {
          return fail('policy_denied', 'Só leio arquivos de texto (.txt, .md, .csv, .json).');
        }
        const t = now();
        if (t - budget.startedAt > BUDGET_WINDOW_MS) budget = { startedAt: t, used: 0 };
        if (budget.used >= READ_BUDGET) {
          return fail('limit_reached', 'Já li bastante agora; espere um pouco para ler mais.');
        }
        let handle;
        try {
          handle = await fsp.open(r.real, constants.O_RDONLY);
        } catch {
          return fail('os_error', 'Não consegui abrir esse arquivo.');
        }
        try {
          // The path is checked again after opening: a junction swapped in between is caught.
          const again = await rules.canonicalize(String(args.path));
          if (!rules.samePath(again.real, r.real)) {
            return fail('path_changed', 'O caminho mudou enquanto eu abria o arquivo.');
          }
          const st = await handle.stat();
          if (!st.isFile()) return fail('policy_denied', 'Isso não é um arquivo comum.');
          const offset = typeof args.offset === 'number' ? Math.max(0, args.offset) : 0;
          const length = Math.min(READ_LIMIT, st.size - offset, READ_BUDGET - budget.used);
          if (length <= 0) return ok('', { size: st.size, offset, end: true });
          const buffer = Buffer.alloc(length);
          const { bytesRead } = await handle.read(buffer, 0, length, offset);
          budget.used += bytesRead;
          const end = offset + bytesRead >= st.size;
          return ok(buffer.subarray(0, bytesRead).toString('utf8'), {
            size: st.size,
            offset,
            next_offset: end ? null : offset + bytesRead,
          });
        } finally {
          await handle.close();
        }
      },
    },
    {
      name: 'fs_write',
      description: 'Cria um arquivo de texto novo. Nunca sobrescreve um arquivo que já existe.',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string' }, content: { type: 'string' } },
        required: ['path', 'content'],
      },
      tier: 'write',
      async handle(args) {
        const r = await guarded(args.path, 'create');
        if (isOutcome(r)) return r;
        if (r.exists) return fail('destination_exists', 'Já existe um arquivo com esse nome.');
        const content = String(args.content);
        if (Buffer.byteLength(content) > READ_LIMIT) {
          return fail('limit_reached', 'O texto passa de 1 MB; divida em arquivos menores.');
        }
        try {
          await fsp.writeFile(r.real, content, { encoding: 'utf8', flag: 'wx' });
        } catch (e) {
          return (e as NodeJS.ErrnoException).code === 'EEXIST'
            ? fail('destination_exists', 'Já existe um arquivo com esse nome.')
            : fail('os_error', 'Não consegui criar o arquivo.');
        }
        return ok(`Criei ${r.real} (${Buffer.byteLength(content)} bytes).`, { path: r.real });
      },
    },
    {
      name: 'fs_mkdir',
      description: 'Cria uma pasta (e as pastas pai que faltarem).',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string' } },
        required: ['path'],
      },
      tier: 'write',
      async handle(args) {
        const r = await guarded(args.path, 'create');
        if (isOutcome(r)) return r;
        if (r.exists) return ok(`A pasta ${r.real} já existe.`, { path: r.real });
        try {
          await fsp.mkdir(r.real, { recursive: true });
        } catch {
          return fail('os_error', 'Não consegui criar a pasta.');
        }
        return ok(`Criei a pasta ${r.real}.`, { path: r.real });
      },
    },
    twoPaths('fs_move', 'Move ou renomeia, sem sobrescrever.', 'modify', async (src, dst) => {
      const single = await requireSingleLink(src);
      if (single) return single;
      try {
        await fsp.rename(src, dst);
      } catch {
        return fail('os_error', 'Não consegui mover (talvez seja outro disco).');
      }
      return ok(`Movi ${src} para ${dst}.`);
    }),
    twoPaths('fs_copy', 'Copia um arquivo, sem sobrescrever.', 'read', async (src, dst) => {
      try {
        await fsp.copyFile(src, dst, constants.COPYFILE_EXCL);
      } catch (e) {
        return (e as NodeJS.ErrnoException).code === 'EEXIST'
          ? fail('destination_exists', 'Já existe algo no destino.')
          : fail('os_error', 'Não consegui copiar (só arquivos, não pastas).');
      }
      return ok(`Copiei ${src} para ${dst}.`);
    }),
    {
      name: 'fs_replace',
      description:
        'Substitui um arquivo existente por texto novo (content) ou por outro arquivo (source). O original vai antes para a Lixeira.',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string' },
          content: { type: 'string' },
          source: { type: 'string' },
        },
        required: ['path'],
      },
      tier: 'destructive',
      async handle(args) {
        if ((args.content === undefined) === (args.source === undefined)) {
          return fail('invalid_args', 'Informe content ou source (só um dos dois).');
        }
        const target = await guarded(args.path, 'modify');
        if (isOutcome(target)) return target;
        if (!target.exists) return fail('os_error', 'O arquivo a substituir não existe.');
        const single = await requireSingleLink(target.real);
        if (single) return single;
        let staged: string;
        const temp = rules.join(
          rules.dirname(target.real),
          `.juniper-${Math.random().toString(36).slice(2)}.tmp`,
        );
        try {
          if (args.source !== undefined) {
            const src = await guarded(args.source, 'read');
            if (isOutcome(src)) return src;
            await fsp.copyFile(src.real, temp, constants.COPYFILE_EXCL);
          } else {
            const content = String(args.content);
            if (Buffer.byteLength(content) > READ_LIMIT) {
              return fail('limit_reached', 'O texto passa de 1 MB.');
            }
            await fsp.writeFile(temp, content, { encoding: 'utf8', flag: 'wx' });
          }
          staged = temp;
          await trash.trash(target.real); // the original is recoverable before anything is lost
          await fsp.rename(staged, target.real);
        } catch {
          await fsp.rm(temp, { force: true }).catch(() => undefined);
          return fail('os_error', 'Não consegui substituir (o original não foi apagado).');
        }
        return ok(`Substituí ${target.real}; o original está na Lixeira.`, { path: target.real });
      },
    },
    {
      name: 'fs_trash',
      description: 'Manda um arquivo ou pasta para a Lixeira (nunca apaga em definitivo).',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string' }, recursive: { type: 'boolean' } },
        required: ['path'],
      },
      tier: 'destructive',
      async handle(args) {
        const r = await guarded(args.path, 'modify');
        if (isOutcome(r)) return r;
        if (!r.exists) return fail('os_error', 'Isso não existe.');
        const st = await fsp.lstat(r.real);
        if (st.isDirectory()) {
          if (args.recursive !== true) {
            return fail('policy_denied', 'É uma pasta: só com recursive=true.');
          }
          if ((await countItems(r.real, TRASH_ITEM_LIMIT)) > TRASH_ITEM_LIMIT) {
            return fail('policy_denied', `A pasta tem mais de ${TRASH_ITEM_LIMIT} itens.`);
          }
        } else {
          const single = await requireSingleLink(r.real);
          if (single) return single;
        }
        try {
          await trash.trash(r.real);
        } catch {
          return fail('os_error', 'Não consegui mandar para a Lixeira, então não apaguei nada.');
        }
        return ok(`Mandei ${r.real} para a Lixeira.`, { path: r.real });
      },
    },
  ];

  function twoPaths(
    name: string,
    description: string,
    sourceAccess: Access,
    run: (src: string, dst: string) => Promise<ToolOutcome>,
  ): LocalTool {
    return {
      name,
      description,
      parameters: {
        type: 'object',
        properties: { source: { type: 'string' }, destination: { type: 'string' } },
        required: ['source', 'destination'],
      },
      tier: 'write',
      async handle(args) {
        const src = await guarded(args.source, sourceAccess);
        if (isOutcome(src)) return src;
        if (!src.exists) return fail('os_error', 'A origem não existe.');
        const dst = await guarded(args.destination, 'create');
        if (isOutcome(dst)) return dst;
        if (dst.exists) return fail('destination_exists', 'Já existe algo no destino.');
        return run(src.real, dst.real);
      },
    };
  }

  return tools;
}
