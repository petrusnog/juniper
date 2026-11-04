/** A small JSON Schema check for tool arguments (type, required, enum): what the tools declare. */
type Schema = {
  type?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  enum?: unknown[];
};

const TYPES: Record<string, (v: unknown) => boolean> = {
  string: v => typeof v === 'string',
  integer: v => typeof v === 'number' && Number.isInteger(v),
  number: v => typeof v === 'number' && Number.isFinite(v),
  boolean: v => typeof v === 'boolean',
  object: v => typeof v === 'object' && v !== null && !Array.isArray(v),
  array: Array.isArray,
};

/** `null` when the arguments obey the schema, otherwise a short reason (PT-BR). */
export function validateArguments(schema: unknown, args: unknown): string | null {
  const s = (schema ?? {}) as Schema;
  if (!TYPES.object?.(args)) {
    return 'os argumentos precisam ser um objeto';
  }
  const given = args as Record<string, unknown>;
  const props = s.properties ?? {};
  for (const key of s.required ?? []) {
    if (given[key] === undefined) {
      return `falta o argumento '${key}'`;
    }
  }
  for (const [key, value] of Object.entries(given)) {
    const spec = props[key];
    if (spec === undefined) {
      return `argumento desconhecido '${key}'`;
    }
    const check = spec.type !== undefined ? TYPES[spec.type] : undefined;
    if (check !== undefined && !check(value)) {
      return `argumento '${key}' deve ser ${spec.type}`;
    }
    if (spec.enum !== undefined && !spec.enum.includes(value)) {
      return `argumento '${key}' fora dos valores permitidos`;
    }
  }
  return null;
}
