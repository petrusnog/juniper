/**
 * The path policy of the file tools (ADR-033 sections 4.1 and 4.2): pure decisions over a
 * `PlatformRules`. A path is accepted only if it is a literal absolute path, its REAL location
 * (as the OS reports it) is inside the home and out of the fixed lists, and, for anything that
 * changes a file, it is exactly the path the user approved (no link, junction or reparse point
 * on the way).
 */
import type { PlatformRules } from '../platform';

export type Access = 'read' | 'create' | 'modify';

export type PathVerdict =
  | { ok: true; real: string; exists: boolean }
  | { ok: false; code: 'policy_denied' | 'path_changed'; reason: string };

const deny = (reason: string): PathVerdict => ({ ok: false, code: 'policy_denied', reason });

export async function resolvePath(
  rules: PlatformRules,
  raw: unknown,
  access: Access,
): Promise<PathVerdict> {
  if (typeof raw !== 'string' || raw === '') {
    return deny('caminho vazio');
  }
  const syntax = rules.unsafeSyntax(raw);
  if (syntax !== null) {
    return deny(syntax);
  }
  const home = rules.homeDir();
  if (!rules.isInside(raw, home)) {
    return deny('só posso mexer em arquivos dentro da sua pasta de usuário');
  }
  let resolved;
  try {
    resolved = await rules.canonicalize(raw);
  } catch {
    return deny('não consegui resolver esse caminho');
  }
  if (access !== 'read') {
    if (resolved.viaLink) {
      return {
        ok: false,
        code: 'path_changed',
        reason: 'o caminho passa por um atalho, junção ou link, que não sigo ao alterar arquivos',
      };
    }
    if (!rules.samePath(resolved.real, raw)) {
      return {
        ok: false,
        code: 'path_changed',
        reason: `o caminho real é outro (${resolved.real}), diferente do que foi aprovado`,
      };
    }
  }
  const real = resolved.real;
  if (!rules.isInside(real, home)) {
    return deny('o caminho real fica fora da sua pasta de usuário');
  }
  for (const blocked of rules.blockedPaths()) {
    if (rules.isInside(real, blocked)) {
      return deny('essa pasta é protegida e a Juniper nunca mexe nela');
    }
  }
  const parts = rules.components(real);
  const name = parts[parts.length - 1] ?? '';
  for (const part of parts) {
    if (rules.isSensitiveName(part)) {
      return deny('esse arquivo é sensível (chave, cofre ou segredo) e fica protegido');
    }
  }
  if (access !== 'read') {
    if (rules.isExecutableName(name)) {
      return deny('não crio nem altero programas, scripts e atalhos');
    }
    for (let i = 0; i < parts.length; i += 1) {
      if (rules.isProtectedFolderName(parts[i] ?? '', parts[i - 1] ?? '')) {
        return deny('essa pasta guarda configuração que executa código e fica protegida');
      }
    }
  }
  return { ok: true, real, exists: resolved.exists };
}
