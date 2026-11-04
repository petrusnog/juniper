import { describe, expect, test } from 'vitest';
import { Executor } from '../src/core/executor/Executor';
import type { LocalTool } from '../src/core/executor/Executor';
import { openUrlTool } from '../src/core/executor/openUrl';
import { validateArguments } from '../src/core/executor/schema';

/** The slice of BrainConnection the executor uses. */
class FakeConnection {
  sent: Record<string, unknown>[] = [];
  private listeners: ((m: { type: string }) => void)[] = [];
  onMessage(l: (m: { type: string }) => void) {
    this.listeners.push(l);
    return () => undefined;
  }
  send(m: Record<string, unknown>) {
    this.sent.push(m);
    return true;
  }
  emit(m: Record<string, unknown>) {
    this.listeners.forEach(l => l(m as { type: string }));
  }
  results() {
    return this.sent.filter(m => m.type === 'result');
  }
}

const flush = () => new Promise(r => setTimeout(r, 0));

function setup(tools: LocalTool[], now = () => 0) {
  const conn = new FakeConnection();
  const executor = new Executor(conn as never, tools, now);
  executor.start();
  return { conn, executor };
}

let runs = 0;
const echo: LocalTool = {
  name: 'fs_echo',
  description: 'x',
  parameters: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
  tier: 'destructive',
  async handle(args) {
    runs += 1;
    return { success: true, output: `ok ${args.path}` };
  },
};

const invoke = (over: Record<string, unknown> = {}) => ({
  type: 'invoke',
  call_id: 'k-1',
  tool_name: 'fs_echo',
  arguments: { path: 'C:\\Users\\p\\a.txt' },
  tier: 'destructive',
  timeout_ms: 10000,
  ...over,
});

describe('Executor revalidation', () => {
  test('registers its tools on welcome with their declared tiers', () => {
    const { conn } = setup([echo]);
    conn.emit({ type: 'welcome' });
    expect(conn.sent[0]).toMatchObject({ type: 'register', tools: [{ name: 'fs_echo', tier: 'destructive' }] });
  });

  test('runs a valid invoke', async () => {
    runs = 0;
    const { conn } = setup([echo]);
    conn.emit(invoke());
    await flush();
    expect(conn.results()[0]).toMatchObject({ call_id: 'k-1', success: true });
    expect(runs).toBe(1);
  });

  test.each([
    ['unknown_tool', { tool_name: 'run_shell' }],
    ['invalid_args', { arguments: {} }],
    ['invalid_args', { arguments: { path: 3 } }],
    ['invalid_args', { arguments: { path: 'x', extra: 1 } }],
    ['tier_mismatch', { tier: 'read' }],
    ['tier_mismatch', { tier: undefined }],
  ])('refuses with %s and never runs the tool', async (code, over) => {
    runs = 0;
    const { conn } = setup([echo]);
    conn.emit(invoke(over));
    await flush();
    expect(conn.results()[0]).toMatchObject({ success: false, metadata: { code } });
    expect(runs).toBe(0);
  });

  test('a repeated call_id is answered again but not executed twice', async () => {
    runs = 0;
    const { conn } = setup([echo]);
    conn.emit(invoke());
    await flush();
    conn.emit(invoke());
    await flush();
    expect(conn.results()).toHaveLength(2);
    expect(runs).toBe(1);
  });

  test('two invokes with the same call_id in flight run the action once', async () => {
    runs = 0;
    const { conn } = setup([{ ...echo, handle: async () => { runs += 1; await flush(); return { success: true, output: 'ok' }; } }]);
    conn.emit(invoke());
    conn.emit(invoke());
    await flush();
    await flush();
    expect(runs).toBe(1);
    expect(conn.results()).toHaveLength(2);
  });

  test('the replay window is ten minutes', async () => {
    runs = 0;
    let t = 0;
    const { conn } = setup([echo], () => t);
    conn.emit(invoke());
    await flush();
    t = 10 * 60 * 1000 + 1;
    conn.emit(invoke());
    await flush();
    expect(runs).toBe(2);
  });

  test('a tool that throws becomes a typed os_error', async () => {
    const boom: LocalTool = { ...echo, async handle() { throw new Error('secret detail'); } };
    const { conn } = setup([boom]);
    conn.emit(invoke());
    await flush();
    const result = conn.results()[0] as { error: string; metadata: { code: string } };
    expect(result.metadata.code).toBe('os_error');
    expect(result.error).not.toContain('secret detail');
  });

  test('the kill switch removes the tools and refuses their calls', async () => {
    runs = 0;
    const { conn, executor } = setup([echo]);
    executor.setPaused('fs_', true);
    expect(conn.sent.at(-1)).toMatchObject({ type: 'register', tools: [] });
    conn.emit(invoke());
    await flush();
    expect(conn.results()[0]).toMatchObject({ metadata: { code: 'unknown_tool' } });
    expect(runs).toBe(0);
  });

  test('activity is recorded without arguments or content', async () => {
    const { conn, executor } = setup([echo]);
    conn.emit(invoke());
    await flush();
    expect(JSON.stringify(executor.activity)).not.toContain('a.txt');
  });
});

describe('open_url', () => {
  test('opens the NORMALISED url in the browser', async () => {
    const opened: string[] = [];
    const tool = openUrlTool({ open: async u => void opened.push(u) });
    const r = await tool.handle({ url: 'HTTPS://WWW.YouTube.com/results?search_query=jazz' });
    expect(r.success).toBe(true);
    expect(opened).toEqual(['https://www.youtube.com/results?search_query=jazz']);
  });

  test.each(['file:///C:/Windows/win.ini', 'javascript:alert(1)', 'http://127.0.0.1/', 'https://evil.com\\.music.youtube.com/', 3])(
    'refuses %j with policy_denied',
    async url => {
      const opened: string[] = [];
      const r = await openUrlTool({ open: async u => void opened.push(u) }).handle({ url });
      expect(r).toMatchObject({ success: false, metadata: { code: 'policy_denied' } });
      expect(opened).toEqual([]);
    },
  );

  test('a browser failure is os_error', async () => {
    const r = await openUrlTool({ open: async () => { throw new Error('x'); } }).handle({ url: 'https://example.com/' });
    expect(r).toMatchObject({ success: false, metadata: { code: 'os_error' } });
  });
});

describe('validateArguments', () => {
  test('rejects non-objects and unknown keys', () => {
    expect(validateArguments({ type: 'object', properties: {} }, 'x')).toBeTruthy();
    expect(validateArguments({ type: 'object', properties: {} }, { a: 1 })).toBeTruthy();
  });
  test('integers are not booleans or floats', () => {
    const s = { type: 'object', properties: { n: { type: 'integer' } } };
    expect(validateArguments(s, { n: 1 })).toBeNull();
    expect(validateArguments(s, { n: 1.5 })).toBeTruthy();
    expect(validateArguments(s, { n: true })).toBeTruthy();
  });
});
