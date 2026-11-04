import { describe, expect, test } from 'vitest';
import { codeOf, trustedVersion, UpdateController } from '../src/core/update/UpdateController';
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
    signedVersion: async () => '0.1.60',
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
    ['huge size', { ...INFO, size: 400 * 1024 * 1024 }],
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
    const { c, log } = await ready({ signedVersion: async () => null });
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

describe('apply: the signed file must be the announced release', () => {
  async function ready(over: Partial<UpdatePorts> = {}) {
    const { ports, log } = fake(over);
    const c = new UpdateController(ports, '0.1.57', 'Juniper Labs');
    await c.check();
    return { c, log };
  }

  test('an old installer with a valid signature is refused (replay of a past release)', async () => {
    const { c, log } = await ready({ signedVersion: async () => '0.1.20' });
    await c.apply();
    expect(log).toEqual(['download', 'remove']);
    expect((c.state as { message: string }).message).toContain('versão anunciada');
  });

  test('a signed installer of another version than the announced one is refused', async () => {
    const { c, log } = await ready({ signedVersion: async () => '0.1.99' });
    await c.apply();
    expect(log).not.toContain('install');
  });

  test('the file is hashed again right before it runs', async () => {
    let calls = 0;
    const { c, log } = await ready({
      sha256: async () => (calls++ === 0 ? SHA : 'c'.repeat(64)), // swapped after being checked
    });
    await c.apply();
    expect(log).toEqual(['download', 'remove']);
    expect((c.state as { message: string }).message).toContain('mudou');
  });
});

describe('trustedVersion', () => {
  const out = (over: object = {}) =>
    JSON.stringify({ status: 'Valid', name: 'Juniper Labs', version: '0.1.60', ...over });
  test('a valid signature by exactly the publisher returns the signed file\'s version', () => {
    expect(trustedVersion(out(), 'Juniper Labs')).toBe('0.1.60');
    expect(trustedVersion(out({ name: 'juniper labs' }), 'Juniper Labs')).toBe('0.1.60');
  });
  test.each([
    ['another signer', out({ name: 'Evil Corp' })],
    ['a name that merely contains the publisher', out({ name: 'Juniper Labs Fake' })],
    ['a subject-shaped string', out({ name: 'O="x, CN=Juniper Labs", CN=Outro' })],
    ['an invalid status', out({ status: 'HashMismatch' })],
    ['not signed', out({ status: 'NotSigned', name: '' })],
    ['no version', JSON.stringify({ status: 'Valid', name: 'Juniper Labs' })],
    ['garbage', 'erro do powershell'],
  ])('refuses %s', (_n, o) => expect(trustedVersion(o, 'Juniper Labs')).toBeNull());
  test('refuses everything with an empty publisher', () => {
    expect(trustedVersion(out(), '')).toBeNull();
  });
});
