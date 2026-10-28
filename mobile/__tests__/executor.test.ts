import type { BrainConnection } from '../src/connection/BrainConnection';
import type { MediaAutoplay, OpenResult } from '../src/executor/MediaAutoplay';
import {
  OPEN_URL_TOOL,
  OpenUrlExecutor,
} from '../src/executor/OpenUrlExecutor';

class FakeConnection {
  sent: Record<string, any>[] = [];
  listener: ((m: any) => void) | null = null;
  send(message: Record<string, any>) {
    this.sent.push(message);
    return true;
  }
  onMessage(l: (m: any) => void) {
    this.listener = l;
    return () => {
      this.listener = null;
    };
  }
}

class FakeMedia implements MediaAutoplay {
  opened: string[] = [];
  answer: OpenResult = { opened: true, autoplay: true };
  throws = false;
  async openAndPlay(url: string) {
    if (this.throws) {
      throw new Error('boom');
    }
    this.opened.push(url);
    return this.answer;
  }
}

function setup() {
  const connection = new FakeConnection();
  const media = new FakeMedia();
  const executor = new OpenUrlExecutor(
    connection as unknown as BrainConnection,
    media,
    () => 42,
  );
  executor.start();
  return { connection, media, executor };
}

const WELCOME = {
  type: 'welcome',
  protocol_version: 1,
  server_version: 'x',
  heartbeat_seconds: 15,
  session: { id: 's', resumed: false, turns: 0 },
};
const invoke = (url: unknown, extra: Record<string, unknown> = {}) => ({
  type: 'invoke',
  call_id: 'k1',
  tool_name: 'open_url',
  arguments: { url },
  tier: 'write',
  timeout_ms: 5000,
  ...extra,
});
const flush = () => new Promise(resolve => setImmediate(resolve));

test('registers open_url as a write tool on every welcome', () => {
  const { connection } = setup();
  connection.listener?.(WELCOME);
  expect(connection.sent).toEqual([
    { type: 'register', tools: [OPEN_URL_TOOL] },
  ]);
  expect(OPEN_URL_TOOL.tier).toBe('write');
  connection.listener?.(WELCOME);
  expect(connection.sent).toHaveLength(2);
});

test('opens a valid link, reports autoplay and keeps a history', async () => {
  const { connection, media, executor } = setup();
  connection.listener?.(invoke('HTTPS://Music.YouTube.com./watch?v=abc'));
  await flush();
  expect(media.opened).toEqual(['https://music.youtube.com/watch?v=abc']); // the normalised URL
  expect(connection.sent.at(-1)).toMatchObject({
    type: 'result',
    call_id: 'k1',
    success: true,
    metadata: { url: 'https://music.youtube.com/watch?v=abc', autoplay: true },
  });
  expect(connection.sent.at(-1)?.output).toContain('começou a tocar');
  expect(executor.history).toEqual([
    {
      at: 42,
      url: 'https://music.youtube.com/watch?v=abc',
      outcome: 'opened',
      detail: 'true',
    },
  ]);
});

test('autoplay failing is a success that says so, so the brain can tell the person', async () => {
  const { connection, media } = setup();
  media.answer = { opened: true, autoplay: false };
  connection.listener?.(invoke('https://music.youtube.com/watch?v=abc'));
  await flush();
  const result = connection.sent.at(-1)!;
  expect(result.success).toBe(true);
  expect(result.metadata.autoplay).toBe(false);
  expect(result.output).toContain('basta tocar no play');
});

test.each([
  ['file:///etc/passwd'],
  ['http://192.168.1.1/'],
  ['http://100.64.0.1:8766/'],
  ['https://evil.com\\.music.youtube.com/'],
  // eslint-disable-next-line no-script-url
  ['javascript:alert(1)'],
  [''],
  [undefined],
  [42],
])(
  'refuses %p without opening anything (the phone revalidates the brain)',
  async url => {
    const { connection, media, executor } = setup();
    connection.listener?.(invoke(url));
    await flush();
    expect(media.opened).toEqual([]);
    expect(connection.sent.at(-1)).toMatchObject({
      type: 'result',
      success: false,
      metadata: { code: 'policy_denied' },
    });
    expect(executor.history[0].outcome).toBe('refused');
  },
);

test('an unknown tool is answered, not ignored', async () => {
  const { connection } = setup();
  connection.listener?.(
    invoke('https://a.example/', { tool_name: 'format_disk' }),
  );
  await flush();
  expect(connection.sent.at(-1)).toMatchObject({
    success: false,
    metadata: { code: 'unknown_tool' },
  });
});

test('the app not opening the link is a typed failure', async () => {
  const { connection, media } = setup();
  media.answer = {
    opened: false,
    autoplay: null,
    detail: 'blocked_by_android',
  };
  connection.listener?.(invoke('https://music.youtube.com/watch?v=abc'));
  await flush();
  expect(connection.sent.at(-1)).toMatchObject({
    success: false,
    metadata: { code: 'os_error', detail: 'blocked_by_android' },
  });
});

test('an exception in the native module never escapes', async () => {
  const { connection, media } = setup();
  media.throws = true;
  connection.listener?.(invoke('https://music.youtube.com/watch?v=abc'));
  await flush();
  expect(connection.sent.at(-1)).toMatchObject({
    success: false,
    metadata: { code: 'os_error' },
  });
});

test('the history is bounded', async () => {
  const { connection, executor } = setup();
  for (let i = 0; i < 60; i++) {
    connection.listener?.(invoke('file:///x'));
  }
  await flush();
  expect(executor.history).toHaveLength(50);
});

test('in the background without the overlay permission it does not claim to have opened', async () => {
  const { connection, media, executor } = setup();
  media.answer = {
    opened: true,
    autoplay: true,
    foreground: false,
    overlay: false,
  };
  connection.listener?.(invoke('https://music.youtube.com/watch?v=abc'));
  await flush();
  const result = connection.sent.at(-1)!;
  expect(result.success).toBe(false); // "autoplay: true" may be the previous song still playing
  expect(result.metadata.code).toBe('background_blocked');
  expect(result.error).toContain('Exibir sobre outros apps');
  expect(executor.history[0]).toMatchObject({
    outcome: 'failed',
    detail: 'background_blocked',
  });
});

test.each([
  [{ foreground: false, overlay: true }],
  [{ foreground: true, overlay: false }],
  [{}],
])(
  'with the screen in front, the overlay permission or no report, it opens normally (%j)',
  async extra => {
    const { connection, media } = setup();
    media.answer = { opened: true, autoplay: true, ...extra };
    connection.listener?.(invoke('https://music.youtube.com/watch?v=abc'));
    await flush();
    expect(connection.sent.at(-1)).toMatchObject({ success: true });
  },
);
