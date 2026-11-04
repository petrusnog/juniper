import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from 'vitest';
import { checkUrl, isTrusted, normaliseDomain } from '../src/core/policy/urlPolicy';

// The same corpus the brain (pytest) and the phone (Jest) run, so the three cannot drift.
const cases = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', '..', 'tests', 'fixtures', 'url_policy_cases.json'), 'utf8'),
) as {
  urls: { url: string; allowed: boolean; normalized?: string }[];
  trust: { host: string; domains: string[]; trusted: boolean }[];
};

test.each(cases.urls.map(c => [c.url.slice(0, 70), c] as const))('url policy: %j', (_n, c) => {
  const result = checkUrl(c.url);
  expect(result.ok).toBe(c.allowed);
  if (c.allowed) expect(result.url).toBe(c.normalized);
  else expect(result.reason).toBeTruthy();
});

test.each(cases.trust.map(c => [`${c.host} in ${JSON.stringify(c.domains)}`, c] as const))(
  'trust: %s',
  (_n, c) => expect(isTrusted(c.host, c.domains)).toBe(c.trusted),
);

test('non-string input is refused, never thrown', () => {
  for (const v of [null, undefined, 3, {}, []]) expect(checkUrl(v).ok).toBe(false);
});

test('normaliseDomain', () => {
  expect(normaliseDomain(' Music.YouTube.com. ')).toBe('music.youtube.com');
});
