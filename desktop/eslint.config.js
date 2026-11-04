// Flat config. The boundary rules are ADR-033 section 1.1: the core knows nothing of Electron,
// Node's fs/os/path or the `ws` package, and only src/main/platform may look at the OS.
const tseslint = require('typescript-eslint');

const NODE_MODULES = ['node:*', 'fs', 'fs/*', 'os', 'path', 'child_process', 'electron', 'ws'];

module.exports = tseslint.config(
  { ignores: ['eslint.config.js', 'dist/**', 'node_modules/**', 'src/shared/types.generated.ts', 'scripts/**'] },
  ...tseslint.configs.recommended,
  {
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "MemberExpression[object.name='process'][property.name='platform']",
          message: 'Only src/main/platform may look at the OS (ADR-033 1.1). Use PlatformRules.',
        },
      ],
      'no-restricted-imports': [
        'error',
        { paths: [{ name: 'node:os', message: 'Only src/main/platform may import node:os.' }, { name: 'os', message: 'Only src/main/platform may import os.' }] },
      ],
    },
  },
  {
    // The core is pure: no Electron, no Node I/O.
    files: ['src/core/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', { patterns: NODE_MODULES.map(name => ({ group: [name], message: 'src/core must stay pure (ADR-033 1.1).' })) }],
    },
  },
  {
    // The renderer reaches the system only through the bridge.
    files: ['src/renderer/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', { patterns: NODE_MODULES.map(name => ({ group: [name], message: 'The renderer has no system access.' })) }],
    },
  },
  {
    // The one place allowed to know the OS.
    files: ['src/main/platform/**/*.ts'],
    rules: { 'no-restricted-syntax': 'off', 'no-restricted-imports': 'off' },
  },
  {
    // Tests may check the platform to skip Windows-only cases.
    files: ['tests/**/*.ts'],
    rules: { 'no-restricted-syntax': 'off', 'no-restricted-imports': 'off' },
  },
);
