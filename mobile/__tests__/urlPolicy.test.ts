import fs from 'fs';
import path from 'path';
import {
  checkUrl,
  isTrusted,
  normaliseDomain,
} from '../src/executor/urlPolicy';

// The same file the brain's pytest runs: the two implementations must agree on every case.
const cases = JSON.parse(
  fs.readFileSync(
    path.join(
      __dirname,
      '..',
      '..',
      'tests',
      'fixtures',
      'url_policy_cases.json',
    ),
    'utf8',
  ),
) as {
  urls: { url: string; allowed: boolean; normalized?: string }[];
  trust: { host: string; domains: string[]; trusted: boolean }[];
};

test.each(cases.urls.map(c => [c.url.slice(0, 70), c]))(
  'url policy: %j',
  (_name, c) => {
    const result = checkUrl(c.url);
    expect(result.ok).toBe(c.allowed);
    if (c.allowed) {
      expect(result.url).toBe(c.normalized);
    } else {
      expect(result.reason).toBeTruthy();
    }
  },
);

test.each(
  cases.trust.map(c => [`${c.host} in ${JSON.stringify(c.domains)}`, c]),
)('trust: %s', (_name, c) => {
  expect(isTrusted(c.host, c.domains)).toBe(c.trusted);
});

test('non-string input is refused, never thrown', () => {
  for (const value of [null, undefined, 3, {}, []]) {
    expect(checkUrl(value).ok).toBe(false);
  }
});

test('normaliseDomain', () => {
  expect(normaliseDomain(' Music.YouTube.com. ')).toBe('music.youtube.com');
  expect(normaliseDomain('com')).toBeNull();
  expect(normaliseDomain('8.8.8.8')).toBeNull();
});
