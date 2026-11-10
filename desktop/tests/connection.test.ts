import { describe, expect, test } from 'vitest';
import { BrainConnection } from '../src/core/connection/BrainConnection';
import type { SocketLike } from '../src/core/connection/BrainConnection';
import { delayFor } from '../src/core/connection/backoff';
import { bearerOf, mask } from '../src/core/connection/credential';
import { checkEndpoint } from '../src/core/connection/endpoint';

class FakeSocket implements SocketLike {
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  sent: Record<string, unknown>[] = [];
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(): void {}
  recv(message: object): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

function setup() {
  const sockets: FakeSocket[] = [];
  const headers: Record<string, string>[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const connection = new BrainConnection({
    url: 'ws://100.64.0.1:8765/v1/ws',
    bearer: 'jsc_id.jsk_secretsecret1',
    deviceId: 'dev-1',
    deviceName: 'PC',
    appVersion: '0.0.1',
    capabilities: { fs: true },
    createSocket: (_u, h) => {
      headers.push(h);
      const s = new FakeSocket();
      sockets.push(s);
      return s;
    },
    random: () => 0.5,
    setTimer: (fn, ms) => timers.push({ fn, ms }),
    clearTimer: () => undefined,
  });
  return { connection, sockets, headers, timers };
}

const WELCOME = { type: 'welcome', session: { id: 's1', resumed: false, turns: 0 } };

describe('BrainConnection', () => {
  test('sends the bearer header and a desktop hello that declares fs', () => {
    const { connection, sockets, headers } = setup();
    connection.connect();
    sockets[0]!.onopen?.();
    expect(headers[0]).toEqual({ Authorization: 'Bearer jsc_id.jsk_secretsecret1' });
    const hello = sockets[0]!.sent[0] as { client: Record<string, unknown>; type: string };
    expect(hello.type).toBe('hello');
    expect(hello.client).toMatchObject({ kind: 'desktop', device_id: 'dev-1', capabilities: { fs: true } });
  });

  test('answers ping with pong and becomes connected on welcome', () => {
    const { connection, sockets } = setup();
    connection.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.recv({ type: 'ping' });
    expect(sockets[0]!.sent.at(-1)).toEqual({ type: 'pong' });
    sockets[0]!.recv(WELCOME);
    expect(connection.state).toBe('connected');
  });

  test.each([
    [4401, 'unauthorized'],
    [4426, 'incompatible'],
    [4409, 'replaced'],
  ])('close %i stops retrying (%s)', (code, state) => {
    const { connection, sockets, timers } = setup();
    connection.connect();
    sockets[0]!.onclose?.({ code });
    expect(connection.state).toBe(state);
    expect(timers).toHaveLength(0);
  });

  test('a dropped connection goes offline and retries with back-off', () => {
    const { connection, sockets, timers } = setup();
    connection.connect();
    sockets[0]!.onclose?.({ code: 1006 });
    expect(connection.state).toBe('offline');
    expect(timers[0]!.ms).toBe(delayFor(0, () => 0.5));
    timers[0]!.fn();
    expect(sockets).toHaveLength(2);
  });

  test('1012 (a deploy) retries in half a second', () => {
    const { connection, sockets, timers } = setup();
    connection.connect();
    sockets[0]!.onclose?.({ code: 1012 });
    expect(timers[0]!.ms).toBe(500);
  });

  test('nothing is queued while offline', () => {
    const { connection } = setup();
    expect(connection.send({ type: 'pong' })).toBe(false);
  });

  test('a malformed frame does not drop the connection', () => {
    const { connection, sockets } = setup();
    connection.connect();
    sockets[0]!.onmessage?.({ data: 'not json' });
    expect(connection.state).toBe('connecting');
  });
});

describe('endpoint', () => {
  test.each([
    ['ws://100.64.0.1:8765/v1/ws', true],
    ['ws://100.127.255.254:8765/v1/ws', true],
    ['ws://juniper.tail1234.ts.net:8765/v1/ws', true],
    ['wss://brain.example.com/v1/ws', true],
    ['ws://100.128.0.1/v1/ws', false],
    ['ws://192.168.1.10:8765/v1/ws', false],
    ['ws://example.com/v1/ws', false],
    ['http://100.64.0.1/', false],
    ['ws://user:pw@100.64.0.1/', false],
    ['not a url', false],
  ])('%s -> %s', (url, ok) => expect(checkEndpoint(url).ok).toBe(ok));
});

describe('credential', () => {
  test('bearer is client_id.client_secret', () => {
    expect(bearerOf({ clientId: 'jsc_a', clientSecret: 'jsk_b' })).toBe('jsc_a.jsk_b');
  });
  test('mask hides secrets in logs', () => {
    const text = mask('erro com jsk_abcdefghijklmnop e Bearer jsc_x.jsk_abcdefghijkl');
    expect(text).not.toContain('abcdefghij');
  });
});

import { completeEndpoint } from '../src/core/connection/endpoint';
import { describeClose, describeSocketError, hintFor } from '../src/core/connection/diagnose';

describe('completeEndpoint (what a person types becomes the brain address)', () => {
  test.each([
    ['100.69.153.120', 'ws://100.69.153.120:8765/v1/ws'],
    ['100.69.153.120:8765', 'ws://100.69.153.120:8765/v1/ws'],
    ['ws://100.69.153.120', 'ws://100.69.153.120:8765/v1/ws'],
    ['ws://100.69.153.120/', 'ws://100.69.153.120:8765/v1/ws'],
    ['ws://100.69.153.120:8765', 'ws://100.69.153.120:8765/v1/ws'],
    ['  ws://100.69.153.120:8765/v1/ws  ', 'ws://100.69.153.120:8765/v1/ws'],
    ['juniper.tail1234.ts.net', 'ws://juniper.tail1234.ts.net:8765/v1/ws'],
    ['wss://brain.example.com', 'wss://brain.example.com/v1/ws'],
    ['brain.example.com', 'wss://brain.example.com/v1/ws'],
  ])('%s -> %s', (typed, expected) => expect(completeEndpoint(typed)).toBe(expected));

  test('an explicit port and path are kept', () => {
    expect(completeEndpoint('ws://100.69.153.120:9000/outro')).toBe('ws://100.69.153.120:9000/outro');
  });

  test('the incomplete address from the screenshot now passes the check, completed', () => {
    expect(checkEndpoint('ws://100.69.153.120')).toEqual({ ok: true, url: 'ws://100.69.153.120:8765/v1/ws' });
  });

  test('completing never loosens the security rule', () => {
    // typed with no scheme, a LAN address is completed to wss:// (TLS), never to plain ws://
    expect(completeEndpoint('192.168.1.10')).toMatch(/^wss:/);
    expect(checkEndpoint('ws://192.168.1.10').ok).toBe(false);
    expect(checkEndpoint('http://100.69.153.120').ok).toBe(false);
  });
});

describe('diagnosis', () => {
  test('a socket error becomes a short masked detail', () => {
    expect(describeSocketError({ message: 'connect ECONNREFUSED 100.69.153.120:8765' })).toContain('ECONNREFUSED');
    expect(describeSocketError({ message: 'Bearer jsc_x.jsk_abcdefghijklmnop' })).not.toContain('abcdefghij');
    expect(describeSocketError(undefined)).toBe('');
    expect(describeSocketError({ message: 'x'.repeat(500) })).toHaveLength(200);
  });

  test('a close without an error still says why', () => {
    expect(describeClose(1006)).toBe('conexão fechada, código 1006');
    expect(describeClose(4401, 'unauthorized')).toContain('unauthorized');
  });

  test.each([
    ['connect ECONNREFUSED 100.69.153.120:8765', '8765'],
    ['connect ETIMEDOUT 100.69.153.120:8765', 'Tailscale'],
    ['getaddrinfo ENOTFOUND brain', 'nome'],
    ['Unexpected server response: 404', '/v1/ws'],
    ['Unexpected server response: 401', 'credencial'],
    ['Client network socket disconnected before secure TLS connection was established', 'ws://'],
  ])('%s gives a hint mentioning %s', (detail, word) => expect(hintFor(detail)).toContain(word));

  test('no detail, no hint', () => {
    expect(hintFor(null)).toBeNull();
    expect(hintFor('algo desconhecido')).toBeNull();
  });
});

describe('BrainConnection keeps the reason a connection failed', () => {
  test('the socket error is kept until the connection is up', () => {
    const { connection, sockets } = setup();
    connection.connect();
    sockets[0]!.onerror?.({ message: 'connect ECONNREFUSED 100.64.0.1:8765' });
    sockets[0]!.onclose?.({ code: 1006 });
    expect(connection.detail).toContain('ECONNREFUSED');
    expect(connection.state).toBe('offline');
  });

  test('a close with no error still leaves a detail', () => {
    const { connection, sockets } = setup();
    connection.connect();
    sockets[0]!.onclose?.({ code: 1006 });
    expect(connection.detail).toContain('1006');
  });

  test('a socket that cannot even be created is reported, not swallowed', () => {
    const connection = new BrainConnection({
      url: 'ws://100.64.0.1:8765/v1/ws', bearer: 'b', deviceId: 'd', appVersion: '1',
      createSocket: () => { throw new Error('Invalid URL'); },
      setTimer: () => 1, clearTimer: () => undefined,
    });
    connection.connect();
    expect(connection.detail).toContain('Invalid URL');
  });

  test('the detail is cleared once connected', () => {
    const { connection, sockets } = setup();
    connection.connect();
    sockets[0]!.onerror?.({ message: 'connect ECONNREFUSED' });
    sockets[0]!.onopen?.();
    sockets[0]!.recv({ type: 'welcome', session: { id: 's', resumed: false, turns: 0 } });
    expect(connection.detail).toBeNull();
  });
});
