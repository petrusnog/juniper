/**
 * The terminal look of Juniper (docs/pages/dashboard.md#visual) must be the same here as in the
 * phone app and the dashboard: these tests keep the desktop from drifting away from it.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, test } from 'vitest';

const root = path.join(__dirname, '..');
const read = (...p: string[]) => fs.readFileSync(path.join(root, ...p), 'utf8');
const css = read('src', 'renderer', 'app.css');
const html = read('src', 'renderer', 'index.html');
const tokens = read('..', 'mobile', 'src', 'theme', 'tokens.ts');

/** `key: '#rrggbb'` pairs of the phone app's `terminal` palette. */
function palette(): Record<string, string> {
  const block = /export const terminal: Palette = \{([\s\S]*?)\};/.exec(tokens)?.[1] ?? '';
  return Object.fromEntries([...block.matchAll(/(\w+):\s*'(#[0-9a-fA-F]{6})'/g)].map(m => [m[1] as string, (m[2] as string).toLowerCase()]));
}

const VARS: Record<string, string> = {
  background: '--bg', card: '--card', ink: '--ink', muted: '--muted', border: '--border', accent: '--accent',
  onAccent: '--on-accent', cyan: '--cyan', blue: '--blue', violet: '--violet', green: '--green', amber: '--amber', red: '--red',
};

describe('colours', () => {
  test('the phone palette was found', () => {
    expect(Object.keys(palette()).sort()).toEqual(Object.keys(VARS).sort());
  });

  test.each(Object.entries(VARS))('%s is the phone app\'s value in %s', (key, variable) => {
    const value = palette()[key];
    const found = new RegExp(`${variable}:\\s*(#[0-9a-fA-F]{6})`).exec(css)?.[1]?.toLowerCase();
    expect(found).toBe(value);
  });

  test('the neon green of the logo is the accent', () => {
    expect(palette().accent).toBe('#39e639');
  });

  test('square corners, like the phone app', () => {
    const radius = /radius\.card|card:\s*(\d+)/.exec(tokens);
    expect(radius).not.toBeNull();
    expect(css).toMatch(/--radius:\s*3px/);
  });

  test('no colour is hard-coded outside the variables block', () => {
    const outside = css.replace(/:root\s*\{[\s\S]*?\}/, '');
    expect(outside.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([]);
  });
});

describe('type and mark', () => {
  test('the monospace stack the app already had is kept', () => {
    expect(css).toMatch(/font:\s*15px\/1\.45 Consolas, "Cascadia Mono", ui-monospace, monospace/);
  });

  test('the logo is the same image as the phone app\'s', () => {
    expect(fs.readFileSync(path.join(root, 'src', 'renderer', 'logo.png')).equals(fs.readFileSync(path.join(root, '..', 'mobile', 'branding', 'logo.png')))).toBe(true);
    expect(fs.readFileSync(path.join(root, 'build', 'icon.png')).equals(fs.readFileSync(path.join(root, 'src', 'renderer', 'logo.png')))).toBe(true);
  });

  test('the top-left mark is logo + name + blinking cursor', () => {
    const bar = /<header id="bar">([\s\S]*?)<\/header>/.exec(html)?.[1] ?? '';
    expect(bar).toContain('class="brand-logo"');
    expect(bar).toContain('<span class="brand-name">Juniper</span>');
    expect(bar).toContain('class="cursor"');
    expect(bar.indexOf('brand')).toBeLessThan(bar.indexOf('id="status"')); // first thing on the left
  });

  test('the first-run mark is large, with JUNIPER in capitals', () => {
    expect(html).toMatch(/<div class="brand large"[\s\S]*?<span class="brand-name">JUNIPER<\/span>/);
  });

  test('the cursor blinks 550 ms on, 550 ms off, and stands still with reduced motion', () => {
    expect(css).toMatch(/--blink:\s*1100ms/);
    expect(css).toMatch(/@keyframes blink/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*?animation:\s*none/);
  });
});

describe('chat looks like the phone app\'s', () => {
  test('one bubble style per role, user on the right in the accent colour', () => {
    for (const role of ['user', 'assistant', 'error', 'notice']) expect(css).toContain(`.bubble.${role}`);
    expect(css).toMatch(/\.bubble\.user\s*\{[^}]*align-self:\s*flex-end[^}]*color:\s*var\(--accent\)/);
    expect(css).toMatch(/\.bubble\.error\s*\{[^}]*color:\s*var\(--red\)/);
  });

  test('send is the accent button and stop is amber', () => {
    expect(css).toMatch(/button\.primary\s*\{[^}]*background:\s*var\(--accent\)[^}]*color:\s*var\(--on-accent\)/);
    expect(css).toMatch(/button\.warn\s*\{[^}]*background:\s*var\(--amber\)/);
  });

  test('the streaming caret, the interrupted note and the empty state are the phone\'s', () => {
    const app = read('src', 'renderer', 'app.ts');
    expect(app).toContain("' ▍'");
    expect(app).toContain('(interrompida)');
    expect(app).toContain('Escreva uma mensagem para começar.');
  });

  test('a conversation list exists, opened by the Conversas button', () => {
    expect(html).toContain('id="conversations"');
    expect(html).toContain('id="session-list"');
  });

  test('a focus glow, as in the dashboard', () => {
    expect(css).toMatch(/--glow:/);
    expect(css).toMatch(/:focus-visible[^{]*\{[^}]*box-shadow:\s*var\(--glow\)/);
  });
});
