import { delayFor } from '../src/connection/backoff';
import {
  BrainConnection,
  ConnectionState,
  SocketLike,
} from '../src/connection/BrainConnection';

class FakeSocket implements SocketLike {
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: { code: number; reason?: string }) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  sent: Record<string, unknown>[] = [];
  closed = false;
  constructor(public url: string, public headers: Record<string, string>) {}
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  drop(code: number) {
    this.onclose?.({ code });
  }
}

const WELCOME = {
  type: 'welcome',
  protocol_version: 1,
  server_version: 'x',
  heartbeat_seconds: 15,
  session: { id: 's-1', resumed: false, turns: 0 },
};

function setup(
  extra: Partial<ConstructorParameters<typeof BrainConnection>[0]> = {},
) {
  const sockets: FakeSocket[] = [];
  const timers: { fn: () => void; ms: number; live: boolean }[] = [];
  let clock = 1_000;
  const connection = new BrainConnection({
    url: 'ws://100.1.2.3:8765/v1/ws',
    token: 'segredo-do-token',
    deviceId: 'mobile-1',
    deviceName: 'Pixel',
    appVersion: '0.1.0',
    createSocket: (url, headers) => {
      const s = new FakeSocket(url, headers);
      sockets.push(s);
      return s;
    },
    random: () => 0.5,
    now: () => clock,
    setTimer: (fn, ms) => {
      const t = { fn, ms, live: true };
      timers.push(t);
      return t;
    },
    clearTimer: h => {
      (h as { live: boolean }).live = false;
    },
    ...extra,
  });
  const states: ConnectionState[] = [];
  connection.onState(s => states.push(s));
  return {
    connection,
    sockets,
    timers,
    states,
    advance: (ms: number) => {
      clock += ms;
    },
    fire: () => {
      const t = timers.filter(x => x.live).pop();
      if (!t) {
        throw new Error('no timer');
      }
      t.live = false;
      t.fn();
    },
  };
}

test('opens with the bearer token, says hello as a mobile client and becomes connected', () => {
  const { connection, sockets, states } = setup();
  connection.connect();
  expect(sockets[0].url).toBe('ws://100.1.2.3:8765/v1/ws');
  expect(sockets[0].headers).toEqual({
    Authorization: 'Bearer segredo-do-token',
  });
  sockets[0].open();
  expect(sockets[0].sent[0]).toEqual({
    type: 'hello',
    protocol_version: 1,
    client: {
      kind: 'mobile',
      version: '0.1.0',
      device_id: 'mobile-1',
      device_name: 'Pixel',
    },
  });
  expect(connection.state).toBe('connecting');
  sockets[0].receive(WELCOME);
  expect(states).toEqual(['connecting', 'connected']);
});

test('never puts the token in anything it sends', () => {
  const { connection, sockets } = setup();
  connection.connect();
  sockets[0].open();
  sockets[0].receive(WELCOME);
  connection.send({ type: 'chat.send', id: '1', text: 'oi' });
  expect(JSON.stringify(sockets[0].sent)).not.toContain('segredo-do-token');
});

test('resumes the conversation that was open', () => {
  const { connection, sockets } = setup({
    resume: () => ({ session_id: 's-9' }),
  });
  connection.connect();
  sockets[0].open();
  expect(sockets[0].sent[0].resume).toEqual({ session_id: 's-9' });
});

test('answers ping with pong and hands the other messages to the listeners', () => {
  const { connection, sockets } = setup();
  const seen: string[] = [];
  connection.onMessage(m => seen.push(m.type));
  connection.connect();
  sockets[0].open();
  sockets[0].receive(WELCOME);
  sockets[0].receive({ type: 'ping' });
  sockets[0].receive({ type: 'chat.token', turn_id: 't', text: 'a' });
  expect(sockets[0].sent.at(-1)).toEqual({ type: 'pong' });
  expect(seen).toEqual(['welcome', 'chat.token']);
});

test('ignores a malformed or binary frame without dropping the connection', () => {
  const { connection, sockets } = setup();
  const seen: string[] = [];
  connection.onMessage(m => seen.push(m.type));
  connection.connect();
  sockets[0].open();
  sockets[0].onmessage?.({ data: 'not json' });
  sockets[0].onmessage?.({ data: new ArrayBuffer(4) });
  sockets[0].receive(WELCOME);
  expect(seen).toEqual(['welcome']);
  expect(connection.state).toBe('connected');
});

test('send reports false and queues nothing while the connection is not up', () => {
  const { connection, sockets } = setup();
  expect(connection.send({ type: 'pong' })).toBe(false);
  connection.connect();
  sockets[0].open();
  expect(connection.send({ type: 'pong' })).toBe(false); // hello sent, welcome not yet
  sockets[0].receive(WELCOME);
  expect(connection.send({ type: 'pong' })).toBe(true);
});

test('reconnects with growing delays after a drop', () => {
  const { connection, sockets, timers, states, fire } = setup();
  connection.connect();
  sockets[0].open();
  sockets[0].receive(WELCOME);
  sockets[0].drop(1006);
  expect(states.at(-1)).toBe('offline');
  expect(timers.at(-1)?.ms).toBe(1000);
  fire();
  expect(sockets).toHaveLength(2);
  sockets[1].drop(1006); // never became stable
  expect(timers.at(-1)?.ms).toBe(2000);
  fire();
  sockets[2].drop(1006);
  expect(timers.at(-1)?.ms).toBe(4000);
});

test('the back-off resets after a connection that stayed up for 30 s', () => {
  const { connection, sockets, timers, fire, advance } = setup();
  connection.connect();
  sockets[0].open();
  sockets[0].receive(WELCOME);
  sockets[0].drop(1006);
  fire();
  sockets[1].drop(1006);
  fire();
  sockets[2].open();
  sockets[2].receive(WELCOME);
  advance(31_000);
  sockets[2].drop(1006);
  expect(timers.at(-1)?.ms).toBe(1000);
});

test('a deploy (1012) reconnects almost at once and resets the back-off', () => {
  const { connection, sockets, timers } = setup();
  connection.connect();
  sockets[0].open();
  sockets[0].receive(WELCOME);
  sockets[0].drop(1012);
  expect(timers.at(-1)?.ms).toBe(500);
});

test.each([
  [4401, 'unauthorized'],
  [4426, 'incompatible'],
  [4409, 'replaced'],
])('close code %d stops retrying and reports %s', (code, state) => {
  const { connection, sockets, timers } = setup();
  connection.connect();
  sockets[0].open();
  sockets[0].drop(code);
  expect(connection.state).toBe(state);
  expect(timers.filter(t => t.live)).toHaveLength(0);
});

test('close() stops everything and does not retry', () => {
  const { connection, sockets, timers } = setup();
  connection.connect();
  sockets[0].open();
  connection.close();
  expect(sockets[0].closed).toBe(true);
  expect(connection.state).toBe('idle');
  expect(timers.filter(t => t.live)).toHaveLength(0);
});

test('a socket that cannot even be created goes offline and retries', () => {
  let calls = 0;
  const { connection, timers, states } = setup({
    createSocket: () => {
      calls += 1;
      throw new Error('boom');
    },
  });
  connection.connect();
  expect(calls).toBe(1);
  expect(states.at(-1)).toBe('offline');
  expect(timers.filter(t => t.live)).toHaveLength(1);
});

test('delayFor follows 1, 2, 4, 8, 16, 30 s with +-20% jitter', () => {
  expect([0, 1, 2, 3, 4, 5, 9].map(n => delayFor(n, () => 0.5))).toEqual([
    1000, 2000, 4000, 8000, 16000, 30000, 30000,
  ]);
  expect(delayFor(0, () => 0)).toBe(800);
  expect(delayFor(0, () => 1)).toBe(1200);
});
