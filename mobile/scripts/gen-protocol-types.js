#!/usr/bin/env node
/**
 * Generates src/protocol/types.generated.ts from docs/protocol/schema.json (ADR-022, section 7).
 *
 *   node scripts/gen-protocol-types.js          writes the file
 *   node scripts/gen-protocol-types.js --check  fails if the file is out of date
 *
 * The schema is the source of truth (ADR-012); never edit the generated file by hand.
 */
const fs = require('fs');
const path = require('path');
const { compile } = require('json-schema-to-typescript');

const SCHEMA = path.join(
  __dirname,
  '..',
  '..',
  'docs',
  'protocol',
  'schema.json',
);
const OUT = path.join(__dirname, '..', 'src', 'protocol', 'types.generated.ts');

async function generate() {
  const schema = JSON.parse(fs.readFileSync(SCHEMA, 'utf8'));
  const rest = { ...schema };
  delete rest.discriminator; // a pydantic detail the TypeScript types do not need
  const errorCodes = rest['x-error-codes'];
  delete rest['x-error-codes'];
  const body = await compile(rest, 'JuniperMessage', {
    bannerComment:
      '/* GENERATED from docs/protocol/schema.json by scripts/gen-protocol-types.js. Do not edit. */',
    additionalProperties: false,
    unreachableDefinitions: true,
    style: { singleQuote: true, trailingComma: 'all', arrowParens: 'avoid' },
  });
  const codes = JSON.stringify(errorCodes, null, 2);
  return `${body}\nexport const PROTOCOL_VERSION = ${schema['x-protocol-version']};\nexport const MAX_MESSAGE_BYTES = ${schema['x-max-message-bytes']};\n/** error.code -> whether the connection stays open is up to the server; value = retryable. */\nexport const ERROR_CODES: Record<string, boolean> = ${codes};\n`;
}

async function main() {
  const text = await generate();
  if (process.argv.includes('--check')) {
    const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
    if (current !== text) {
      console.error(
        'types.generated.ts is out of date: run `npm run gen:protocol`.',
      );
      process.exit(1);
    }
    return;
  }
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, text);
}

if (require.main === module) {
  main();
}
module.exports = { generate, OUT };
