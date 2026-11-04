import { describe, expect, test } from 'vitest';
import { codeOf, isTrustedSignature, UpdateController } from '../src/core/update/UpdateController';
import type { UpdatePorts } from '../src/core/update/UpdateController';

const SHA = 'a'.repeat(64);
const INFO = { available: true, version_code: 60, version_name: '0.1.60', sha256: SHA, size: 1000 };

function fake(over: Partial<UpdatePorts> = {}) {
  const log: string[] = [];
  const ports: UpdatePorts = {
    fetchInfo: async () => INFO,
    download: async () => (log.push('download'), 'C:\\tmp\\setup.exe'),
    fileSize: async () => 1000,
    sha256: async () => SHA,
    signedBy: async () => true,
    remove: async () => void log.push('remove'),
    install: async () => void log.push('install'),
    ...over,
  };
  return { ports, log };
}

describe('codeOf', () => {
  test.each([['0.1.57', 57], ['1.2.3', 3], ['dev', 0], ['', 0]])('%s -> %i', (v, n) => expect(codeOf(v)).toBe(n));
});

describe('UpdateController.check', () => {
  test('offers only a newer build', async () => {
    const { ports } = fake();
    const newer = new UpdateController(ports, '0.1.57', 'Juniper');
    await newer.check();
    expect(newer.state).toEqual({ phase: 'available', version: '0.1.60' });
    for (const installed of ['0.1.60', '0.1.61']) {
      const same = new UpdateController(ports, installed, 'Juniper');
      await same.check();
      expect(same.state.phase).toBe('none');
    }
  });

  test.each([
    ['nothing published', { available: false }],
    ['bad sha', { ...INFO, sha256: 'xyz' }],
    ['zero size', { ...INFO, size: 0 }],
    ['huge size', { ...INFO, size: 600 * 1024 * 1024 }],
    ['missing fields', { available: true }],
  ])('refuses to offer: %s', async (_n, info) => {
    const c = new UpdateController(fake({ fetchInfo: async () => info }).ports, '0.1.1', 'Juniper');
    await c.check();
    expect(c.state.phase).toBe('none');
  });

  test('a brain that cannot be asked is not an error on screen', async () => {
    const c = new UpdateController(fake({ fetchInfo: async () => { throw new Error('offline'); } }).ports, '0.1.1', 'Juniper');
    await c.check();
    expect(c.state.phase).toBe('none');
  });
});

describe('UpdateController.apply', () => {
  async function ready(over: Partial<UpdatePorts> = {}, publisher = 'Juniper') {
    const { ports, log } = fake(over);
    const c = new UpdateController(ports, '0.1.1', publisher);
    await c.check();
    return { c, log };
  }

  test('downloads, verifies everything and then installs', async () => {
    const { c, log } = await ready();
    await c.apply();
    expect(log).toEqual(['download', 'install']);
  });

  test('nothing is installed when the SHA-256 does not match, and the file is removed', async () => {
    const { c, log } = await ready({ sha256: async () => 'b'.repeat(64) });
    await c.apply();
    expect(log).toEqual(['download', 'remove']);
    expect(c.state).toMatchObject({ phase: 'error' });
  });

  test('nothing is installed when the size is different', async () => {
    const { c, log } = await ready({ fileSize: async () => 999 });
    await c.apply();
    expect(log).not.toContain('install');
  });

  test('nothing is installed without a valid signature by the publisher', async () => {
    const { c, log } = await ready({ signedBy: async () => false });
    await c.apply();
    expect(log).toEqual(['download', 'remove']);
    expect((c.state as { message: string }).message).toContain('assinatura');
  });

  test('an unsigned build (no publisher) never downloads', async () => {
    const { c, log } = await ready({}, '');
    await c.apply();
    expect(log).toEqual([]);
    expect(c.state.phase).toBe('error');
  });

  test('a failed download is an error, not a crash', async () => {
    const { c } = await ready({ download: async () => { throw new Error('reset'); } });
    await c.apply();
    expect(c.state.phase).toBe('error');
  });

  test('apply without an offer does nothing', async () => {
    const { ports, log } = fake();
    const c = new UpdateController(ports, '0.1.1', 'Juniper');
    await c.apply();
    expect(log).toEqual([]);
  });
});

describe('isTrustedSignature', () => {
  const ok = (cn: string) => JSON.stringify({ status: 'Valid', subject: `CN=${cn}, O=Juniper, C=BR` });
  test('accepts a valid signature whose CN is exactly the publisher', () => {
    expect(isTrustedSignature(ok('Juniper Labs'), 'Juniper Labs')).toBe(true);
    expect(isTrustedSignature(JSON.stringify({ status: 'Valid', subject: 'CN="Juniper, Labs", O=x' }), 'Juniper, Labs')).toBe(true);
  });
  test.each([
    ['another signer', ok('Evil Corp')],
    ['a CN that merely contains the name', ok('Juniper Labs Fake')],
    ['an invalid status', JSON.stringify({ status: 'HashMismatch', subject: 'CN=Juniper Labs' })],
    ['not signed', JSON.stringify({ status: 'NotSigned', subject: '' })],
    ['garbage', 'erro do powershell'],
  ])('refuses %s', (_n, out) => expect(isTrustedSignature(out, 'Juniper Labs')).toBe(false));
  test('refuses everything with an empty publisher', () => {
    expect(isTrustedSignature(ok('Juniper Labs'), '')).toBe(false);
  });
  test('the publisher match is not a substring of another field', () => {
    expect(isTrustedSignature(JSON.stringify({ status: 'Valid', subject: 'CN=Other, O=Juniper Labs' }), 'Juniper Labs')).toBe(false);
  });
});
