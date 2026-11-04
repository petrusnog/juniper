// Bundles main, preload and renderer into dist/ with esbuild (no framework, ADR-033).
const esbuild = require('esbuild');
const fs = require('fs');
const path = require('path');

const out = path.join(__dirname, '..', 'dist');
const root = path.join(__dirname, '..', 'src');

async function main() {
  fs.rmSync(out, { recursive: true, force: true });
  const common = { bundle: true, sourcemap: false, logLevel: 'warning', target: 'node22' };
  // The signer this build trusts for self-updates; empty for a development build.
  const publisher = process.env.JUNIPER_PUBLISHER || '';
  await esbuild.build({ ...common, define: { __PUBLISHER__: JSON.stringify(publisher) }, platform: 'node', format: 'cjs', external: ['electron', 'bufferutil', 'utf-8-validate'],
    entryPoints: { 'main/index': path.join(root, 'main', 'index.ts'), 'preload/index': path.join(root, 'preload', 'index.ts') },
    outdir: out });
  await esbuild.build({ ...common, platform: 'browser', format: 'iife', target: 'chrome130',
    entryPoints: {
      'renderer/app': path.join(root, 'renderer', 'app.ts'),
      'renderer/pcm-worklet': path.join(root, 'renderer', 'pcm-worklet.ts'),
    }, outdir: out });
  for (const f of ['index.html', 'app.css']) {
    fs.copyFileSync(path.join(root, 'renderer', f), path.join(out, 'renderer', f));
  }
}
main().catch(e => { console.error(e); process.exit(1); });
